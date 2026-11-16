"""真实存储的集成测试，默认跳过；DOGHUNT_TEST_INTEGRATION=1 时才运行。

P2：连本机 make storage-up 起的 Postgres，在单独的测试库（默认 doghunt_test，跑完删除）里验证
迁移 upgrade → downgrade -1 → upgrade、UTCDateTime 时区往返、会话回滚和 /health/storage。
连接参数取 backend/.env 的 DOGHUNT_DB_*（不使用 _isolation 强制的 SQLite）；不会动日常库 doghunt 的数据。
P4 再在这里加 Milvus 的用例。

  cd backend && DOGHUNT_TEST_INTEGRATION=1 uv run --locked python -m unittest discover -s tests -p "test_memory_integration.py" -v
"""

import _isolation  # noqa: F401  先于任何 app 模块
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.core.config import BACKEND_ROOT, Settings, get_settings
from app.db import session as db_session
from app.db.types import UTCDateTime
from app.main import create_app

ENABLED = os.environ.get("DOGHUNT_TEST_INTEGRATION") == "1"
TEST_DB_NAME = os.environ.get("DOGHUNT_TEST_DB_NAME", "doghunt_test")


def _real_settings(**overrides) -> Settings:
    # 初始化参数优先于环境变量，所以能绕过 _isolation 强制的 DOGHUNT_DATABASE_URL，仍从 backend/.env 读密码。
    return Settings(DOGHUNT_DATABASE_URL="", **overrides)


@unittest.skipUnless(ENABLED, "集成测试默认跳过；设 DOGHUNT_TEST_INTEGRATION=1 并先 make storage-up")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if TEST_DB_NAME == "doghunt" or not TEST_DB_NAME.replace("_", "").isalnum():
            raise unittest.SkipTest(f"测试库名 {TEST_DB_NAME!r} 不安全，拒绝运行")
        admin_url = _real_settings().database_url.get_secret_value()
        cls.test_url = _real_settings(db_name=TEST_DB_NAME).database_url.get_secret_value()
        cls.password = make_url(admin_url).password or ""
        cls.admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
        try:
            with cls.admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
        except OperationalError as exc:
            cls.admin.dispose()
            raise unittest.SkipTest(f"连不上本机 Postgres（先 make storage-up）：{type(exc.orig).__name__}") from None
        cls.env = patch.dict(os.environ, {"DOGHUNT_DATABASE_URL": cls.test_url})
        cls.env.start()
        get_settings.cache_clear()
        db_session.dispose_engine()

    @classmethod
    def tearDownClass(cls):
        db_session.dispose_engine()
        cls.env.stop()
        get_settings.cache_clear()
        with cls.admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        cls.admin.dispose()

    def alembic(self, *args: str) -> str:
        # 用命令行跑，和文档、Makefile 里的用法一致；连接串经环境变量传给 env.py 的 get_settings()。
        result = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND_ROOT, capture_output=True,
                                text=True, timeout=60, env={**os.environ, "DOGHUNT_DATABASE_URL": self.test_url})
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output.replace(self.password, "***") if self.password else output)
        if self.password:
            self.assertNotIn(self.password, output)
        return output

    def revision(self) -> str | None:
        return db_session.check_database().revision

    def test_1_migration_cycle_on_empty_database(self):
        with db_session.session_scope() as s:
            tables = s.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")).scalar()
        self.assertEqual(tables, 0, "测试库应该是空的")
        self.alembic("upgrade", "head")
        self.assertEqual(self.revision(), "0001")
        self.alembic("downgrade", "-1")
        self.assertIsNone(self.revision())
        self.alembic("upgrade", "head")
        self.assertEqual(self.revision(), "0001")
        self.assertIn("0001 (head)", self.alembic("current"))

    def test_2_utc_datetime_round_trip_with_non_utc_session(self):
        metadata = MetaData()
        table = Table("p2_time_probe", metadata, Column("id", Integer, primary_key=True), Column("at", UTCDateTime))
        engine = db_session.get_engine()
        metadata.create_all(engine)
        try:
            beijing = datetime(2026, 11, 16, 23, 30, tzinfo=timezone(timedelta(hours=8)))
            with db_session.session_scope() as s:
                s.execute(text("SET TIME ZONE 'Asia/Shanghai'"))  # 会话时区不是 UTC，读回也要是 UTC
                s.execute(insert(table).values(id=1, at=beijing))
                value = s.execute(select(table.c.at)).scalar_one()
                stored = s.execute(text("SELECT at AT TIME ZONE 'UTC' FROM p2_time_probe")).scalar_one()
                column_type = s.execute(text(
                    "SELECT data_type FROM information_schema.columns WHERE table_name='p2_time_probe' AND column_name='at'"
                )).scalar_one()
            self.assertEqual(value, beijing)
            self.assertEqual(value.tzinfo, UTC)
            self.assertEqual(value.hour, 15)
            self.assertEqual(stored, datetime(2026, 11, 16, 15, 30))
            self.assertEqual(column_type, "timestamp with time zone")
        finally:
            metadata.drop_all(engine)

    def test_3_session_rollback_and_statement_timeout(self):
        metadata = MetaData()
        table = Table("p2_tx_probe", metadata, Column("id", Integer, primary_key=True))
        engine = db_session.get_engine()
        metadata.create_all(engine)
        try:
            with self.assertRaises(RuntimeError), db_session.session_scope() as s:
                s.execute(insert(table).values(id=1))
                raise RuntimeError("boom")
            with db_session.session_scope() as s:
                self.assertEqual(s.execute(select(table.c.id)).all(), [])
                timeout = s.execute(text("SHOW statement_timeout")).scalar_one()
            self.assertEqual(timeout, "2s")
        finally:
            metadata.drop_all(engine)

    def test_4_storage_health_ok_without_password(self):
        self.alembic("upgrade", "head")  # 已在最新版本时什么也不做；让本用例单独运行也成立
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/health/storage")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"]["status"], "ok")
        self.assertEqual(body["database"]["revision"], "0001")
        self.assertTrue(body["database"]["url"].startswith("postgresql://doghunt@"))
        self.assertTrue(body["database"]["url"].endswith(f"/{TEST_DB_NAME}"))
        if self.password:
            self.assertNotIn(self.password, response.text)


if __name__ == "__main__":
    unittest.main()
