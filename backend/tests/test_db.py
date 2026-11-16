"""数据库接入（P2）的离线测试：连接串拼接与脱敏、测试隔离、UTCDateTime、会话、迁移、存储健康接口与启动检查。

全部用临时 SQLite 或连不上的本机端口，不需要 Docker；真实 Postgres 见 test_memory_integration.py。
"""

import _isolation  # noqa: F401  先于任何 app 模块
import asyncio
import logging
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError

from app.core import applog
from app.core.config import BACKEND_ROOT, Settings, get_settings
from app.db import session as db_session
from app.db.types import UTCDateTime
from app.main import create_app, lifespan

ODD_PASSWORD = "p@ss:w/rd+ %#?&="
# 端口 1 在本机不会有服务，psycopg 立即得到 Connection refused，不需要 Docker。
CLOSED_PG_URL = f"postgresql+psycopg://doghunt:{'S3cretForTest'}@127.0.0.1:1/doghunt"
# 端口不是数字：make_url 抛 ValueError 而不是 ArgumentError（例如把 DOGHUNT_DB_HOST 写成 localhost:5433）
BAD_PORT_URL = "postgresql+psycopg://doghunt:BadPortPass42@127.0.0.1:notaport/doghunt"


@contextmanager
def use_database(url: str, **extra_env: str):
    """临时改用另一个连接串；退出时恢复隔离的测试库。"""
    env = {"DOGHUNT_DATABASE_URL": url, **extra_env}
    with patch.dict(os.environ, env):
        get_settings.cache_clear()
        db_session.dispose_engine()
        try:
            yield
        finally:
            db_session.dispose_engine()
    # 环境变量已恢复，再清一次，让后面的测试读回隔离配置。
    get_settings.cache_clear()


def temp_sqlite_url() -> str:
    folder = tempfile.mkdtemp(prefix="doghunt-test-db-")
    return f"sqlite:///{Path(folder) / 'db.sqlite'}"


class DatabaseUrlTests(unittest.TestCase):
    def test_url_built_from_parts_round_trips_special_password(self):
        settings = Settings(_env_file=None, DOGHUNT_DATABASE_URL="", db_password=SecretStr(ODD_PASSWORD),
                            db_host="127.0.0.1", db_port=55432, db_user="doghunt", db_name="doghunt")
        url = settings.database_url
        self.assertIsInstance(url, SecretStr)
        self.assertNotIn(ODD_PASSWORD, repr(url))
        parsed = make_url(url.get_secret_value())
        self.assertEqual(parsed.drivername, "postgresql+psycopg")
        self.assertEqual(parsed.password, ODD_PASSWORD)  # quote_plus 会把空格变 "+"，这里必须原样还原
        self.assertEqual((parsed.host, parsed.port, parsed.username, parsed.database),
                         ("127.0.0.1", 55432, "doghunt", "doghunt"))

    def test_override_wins_and_empty_password_has_no_colon(self):
        override = Settings(_env_file=None, DOGHUNT_DATABASE_URL="sqlite:///x.db", db_password=SecretStr("pw"))
        self.assertEqual(override.database_url.get_secret_value(), "sqlite:///x.db")
        no_password = Settings(_env_file=None, DOGHUNT_DATABASE_URL="")
        self.assertEqual(no_password.database_url.get_secret_value(),
                         "postgresql+psycopg://doghunt@127.0.0.1:5432/doghunt")

    def test_safe_url_drops_password(self):
        settings = Settings(_env_file=None, DOGHUNT_DATABASE_URL="", db_password=SecretStr(ODD_PASSWORD))
        shown = db_session.safe_url(settings.database_url.get_secret_value())
        self.assertEqual(shown, "postgresql://doghunt@127.0.0.1:5432/doghunt")
        self.assertEqual(db_session.safe_url("not a url"), "（连接串格式无效）")
        self.assertEqual(db_session.safe_url(BAD_PORT_URL), "（连接串格式无效）")
        host_with_port = Settings(_env_file=None, DOGHUNT_DATABASE_URL="", db_host="localhost:5433")
        self.assertEqual(db_session.safe_url(host_with_port.database_url.get_secret_value()), "（连接串格式无效）")

    def test_register_secrets_redacts_both_password_sources(self):
        settings = Settings(_env_file=None, db_password=SecretStr("PartsPassword123"),
                            DOGHUNT_DATABASE_URL="postgresql+psycopg://u:OverridePass456@h:1/d")
        db_session.register_database_secrets(settings)
        text_out = applog.redact("a PartsPassword123 b OverridePass456 c")
        self.assertNotIn("PartsPassword123", text_out)
        self.assertNotIn("OverridePass456", text_out)
        db_session.register_database_secrets(Settings(_env_file=None, DOGHUNT_DATABASE_URL="::bad::"))  # 不抛错
        db_session.register_database_secrets(Settings(_env_file=None, DOGHUNT_DATABASE_URL=BAD_PORT_URL))
        self.assertNotIn("BadPortPass42", applog.redact(f"url={BAD_PORT_URL}"))  # 解析不了时整条串当秘密


class IsolationTests(unittest.TestCase):
    def test_forced_settings(self):
        settings = get_settings()
        self.assertEqual(settings.database_url.get_secret_value(), _isolation.TEST_DATABASE_URL)
        self.assertFalse(settings.db_startup_check)
        self.assertTrue(Path(_isolation.TEMP_ROOT).is_dir())

    def test_real_env_file_does_not_reach_real_database(self):
        """.env 里写了真实库、真实密码和 MEMORY_MODE=on，离线测试仍只用临时 SQLite，也不打开 psycopg 连接。"""
        folder = Path(tempfile.mkdtemp(prefix="doghunt-test-env-"))
        env_file = folder / ".env"
        env_file.write_text(
            "DOGHUNT_DATABASE_URL=postgresql+psycopg://doghunt:RealPass999@127.0.0.1:5432/doghunt\n"
            "DOGHUNT_DB_PASSWORD=RealPass999\nDOGHUNT_DB_STARTUP_CHECK=true\nDOGHUNT_MEMORY_MODE=on\n"
            "DOGHUNT_MEMORY_WRITE=true\nDOGHUNT_MEMORY_MAINTENANCE_ENABLED=true\n"
            "DOGHUNT_EMBEDDING_PROVIDER=local\nDOGHUNT_VECTORSTORE_PROVIDER=milvus\n",
            encoding="utf-8",
        )
        with patch.dict(Settings.model_config, {"env_file": env_file}):
            get_settings.cache_clear()
            db_session.dispose_engine()
            try:
                settings = get_settings()
                self.assertEqual(settings.database_url.get_secret_value(), _isolation.TEST_DATABASE_URL)
                self.assertFalse(settings.db_startup_check)
                self.assertEqual(settings.db_password.get_secret_value(), "RealPass999")  # 读到了，但不用它拼连接串
                for name in ("MEMORY_MODE", "MEMORY_WRITE", "MEMORY_MAINTENANCE_ENABLED",
                             "EMBEDDING_PROVIDER", "VECTORSTORE_PROVIDER"):
                    self.assertEqual(os.environ[f"DOGHUNT_{name}"], _isolation.FORCED_ENV[f"DOGHUNT_{name}"])
                with patch("psycopg.connect", side_effect=AssertionError("离线测试不应连接 Postgres")) as connect, \
                        TestClient(create_app()) as client:
                    body = client.get("/api/v1/health/storage").json()
                connect.assert_not_called()
                self.assertTrue(body["database"]["url"].startswith("sqlite:"))
                self.assertNotIn("RealPass999", str(body))
            finally:
                db_session.dispose_engine()
                get_settings.cache_clear()


class UTCDateTimeTests(unittest.TestCase):
    def setUp(self):
        self.ctx = use_database(temp_sqlite_url())
        self.ctx.__enter__()
        self.metadata = MetaData()
        self.table = Table("t_time", self.metadata, Column("id", Integer, primary_key=True),
                           Column("at", UTCDateTime, nullable=True))
        self.metadata.create_all(db_session.get_engine())

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_round_trip_returns_utc_aware(self):
        beijing = datetime(2026, 11, 16, 23, 30, tzinfo=timezone(timedelta(hours=8)))
        with db_session.session_scope() as s:
            s.execute(insert(self.table).values(id=1, at=beijing))
            s.execute(insert(self.table).values(id=2, at=None))
        with db_session.session_scope() as s:
            rows = dict(s.execute(select(self.table.c.id, self.table.c.at)).all())
        self.assertEqual(rows[1], beijing)
        self.assertEqual(rows[1].utcoffset(), timedelta(0))
        self.assertEqual(rows[1].tzinfo, UTC)
        self.assertEqual(rows[1].hour, 15)
        self.assertIsNone(rows[2])

    def test_compare_with_aware_value(self):
        with db_session.session_scope() as s:
            s.execute(insert(self.table).values(id=1, at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC)))
            later = s.execute(select(self.table.c.id).where(
                self.table.c.at > datetime(2026, 1, 1, 7, 0, tzinfo=timezone(timedelta(hours=8))))).all()
        self.assertEqual(later, [(1,)])  # 北京 07:00 = UTC 前一天 23:00

    def test_naive_and_wrong_type_rejected(self):
        for bad in (datetime(2026, 1, 1, 0, 0), "2026-01-01"):
            with self.subTest(bad=bad), self.assertRaises(StatementError), db_session.session_scope() as s:
                s.execute(insert(self.table).values(id=9, at=bad))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.ctx = use_database(temp_sqlite_url())
        self.ctx.__enter__()
        metadata = MetaData()
        self.parent = Table("t_parent", metadata, Column("id", Integer, primary_key=True))
        self.child = Table("t_child", metadata, Column("id", Integer, primary_key=True),
                           Column("parent_id", ForeignKey("t_parent.id", ondelete="CASCADE")),
                           Column("name", String(20)))
        metadata.create_all(db_session.get_engine())

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_commit_and_rollback(self):
        with db_session.session_scope() as s:
            s.execute(insert(self.parent).values(id=1))
        with self.assertRaises(RuntimeError), db_session.session_scope() as s:
            s.execute(insert(self.parent).values(id=2))
            raise RuntimeError("boom")
        with db_session.session_scope() as s:
            self.assertEqual(s.execute(select(self.parent.c.id)).scalars().all(), [1])

    def test_sqlite_foreign_keys_enabled(self):
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            s.execute(insert(self.child).values(id=1, parent_id=404, name="orphan"))
        with db_session.session_scope() as s:
            s.execute(insert(self.parent).values(id=1))
            s.execute(insert(self.child).values(id=1, parent_id=1, name="a"))
        with db_session.session_scope() as s:
            s.execute(self.parent.delete())
            self.assertEqual(s.execute(select(self.child.c.id)).all(), [])  # 级联删除生效

    def test_engine_is_lazy_and_dispose_is_safe(self):
        db_session.dispose_engine()
        self.assertFalse(db_session.engine_created())
        db_session.dispose_engine()  # 没创建过也不报错
        first = db_session.get_engine()
        self.assertIs(first, db_session.get_engine())
        db_session.dispose_engine()
        self.assertIsNot(first, db_session.get_engine())
        self.assertIs(db_session.get_sessionmaker().kw["bind"], db_session.get_engine())

    def test_concurrent_first_use_builds_one_engine(self):
        db_session.dispose_engine()
        real_build = db_session._build_engine
        built = []

        def slow_build(settings):
            time.sleep(0.2)
            built.append(real_build(settings))
            return built[-1]

        results = []
        with patch.object(db_session, "_build_engine", side_effect=slow_build):
            threads = [threading.Thread(target=lambda: results.append(db_session.get_engine())) for _ in range(3)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(built), 1)
        self.assertEqual(len({id(e) for e in results}), 1)


class PostgresEngineOptionsTests(unittest.TestCase):
    def test_pool_and_timeouts_from_settings(self):
        settings = Settings(_env_file=None, DOGHUNT_DATABASE_URL=CLOSED_PG_URL, db_pool_size=3,
                            db_pool_timeout_seconds=7, db_statement_timeout_ms=1500)
        with patch.object(db_session, "create_engine") as create:
            db_session._build_engine(settings)
        kwargs = create.call_args.kwargs
        self.assertEqual((kwargs["pool_size"], kwargs["max_overflow"], kwargs["pool_timeout"], kwargs["pool_pre_ping"]),
                         (3, 5, 7, True))
        self.assertEqual(kwargs["connect_args"]["options"], "-c statement_timeout=1500")
        self.assertEqual(kwargs["connect_args"]["connect_timeout"], 2)


class MigrationTests(unittest.TestCase):
    def test_upgrade_downgrade_upgrade_on_empty_sqlite(self):
        with use_database(temp_sqlite_url()):
            config = Config(str(BACKEND_ROOT / "alembic.ini"))
            config.attributes["configure_logger"] = False
            root = logging.getLogger()
            level, handlers = root.level, list(root.handlers)
            command.upgrade(config, "head")
            self.assertEqual((root.level, root.handlers), (level, handlers))  # 进程内迁移不改日志配置
            self.assertEqual(db_session.check_database().revision, "0001")
            command.downgrade(config, "-1")
            self.assertIsNone(db_session.check_database().revision)
            command.upgrade(config, "head")
            self.assertEqual(db_session.check_database().revision, "0001")


class StorageHealthTests(unittest.TestCase):
    def request(self) -> dict:
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/health/storage")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_ok_on_sqlite_without_migration(self):
        with use_database(temp_sqlite_url()):
            body = self.request()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"]["status"], "ok")
        self.assertIsNone(body["database"]["revision"])
        self.assertIsNone(body["database"]["error"])
        self.assertEqual(body["milvus"]["status"], "not_implemented")
        self.assertEqual(body["embedding"]["status"], "not_implemented")

    def test_database_down_is_200_degraded_without_password(self):
        with use_database(CLOSED_PG_URL):
            applog.register_secret("S3cretForTest")
            body = self.request()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["database"]["status"], "down")
        self.assertEqual(body["database"]["url"], "postgresql://doghunt@127.0.0.1:1/doghunt")
        self.assertIn("OperationalError", body["database"]["error"])
        self.assertNotIn("S3cretForTest", str(body))

    def test_error_message_is_redacted_even_if_driver_echoes_password(self):
        applog.register_secret("EchoedPass777")
        with use_database(temp_sqlite_url()), \
                patch.object(db_session, "get_engine", side_effect=RuntimeError("bad EchoedPass777 here")):
            status = db_session.check_database()
        self.assertFalse(status.ok)
        self.assertNotIn("EchoedPass777", status.error)

    def test_bad_port_is_200_degraded_and_app_still_starts(self):
        with use_database(BAD_PORT_URL, DOGHUNT_DB_STARTUP_CHECK="true"), \
                self.assertLogs("app.main", level=logging.WARNING):
            body = self.request()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["database"]["url"], "（连接串格式无效）")
        self.assertIn("ValueError", body["database"]["error"])
        self.assertNotIn("BadPortPass42", str(body))

    def test_query_error_after_connect_is_down_not_unmigrated(self):
        with use_database(temp_sqlite_url()), \
                patch.object(db_session, "inspect", side_effect=OperationalError("SELECT", {}, Exception("lost"))):
            status = db_session.check_database()
        self.assertFalse(status.ok)
        self.assertIn("lost", status.error)

    def test_invalid_url_reports_down(self):
        with use_database("::not-a-url::"):
            body = self.request()
        self.assertEqual(body["database"]["status"], "down")
        self.assertEqual(body["database"]["url"], "（连接串格式无效）")

    def test_existing_health_unchanged(self):
        with use_database(CLOSED_PG_URL), TestClient(create_app()) as client:
            body = client.get("/api/v1/health").json()
        self.assertEqual(body["status"], "ok")


class StartupCheckTests(unittest.TestCase):
    def run_lifespan(self) -> float:
        """返回从开始启动到应用可用的秒数（不含事件循环关闭时等后台线程的时间）。"""
        async def go() -> float:
            started = time.perf_counter()
            async with lifespan(create_app()):
                return time.perf_counter() - started
        return asyncio.run(go())

    def test_disabled_does_not_touch_database(self):
        with use_database(CLOSED_PG_URL), patch("app.main.check_database") as check:
            self.run_lifespan()
        check.assert_not_called()

    def test_database_down_only_warns(self):
        with use_database(CLOSED_PG_URL, DOGHUNT_DB_STARTUP_CHECK="true"), \
                self.assertLogs("app.main", level=logging.WARNING) as logs, \
                TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
        joined = "\n".join(logs.output)
        self.assertIn("数据库连不上", joined)
        self.assertNotIn("S3cretForTest", joined)

    def test_database_ok_logs_info(self):
        with use_database(temp_sqlite_url(), DOGHUNT_DB_STARTUP_CHECK="true"), \
                self.assertLogs("app.main", level=logging.INFO) as logs:
            self.run_lifespan()
        self.assertIn("数据库可连接", "\n".join(logs.output))

    def test_slow_database_does_not_block_startup(self):
        def slow():
            time.sleep(1.5)
        with use_database(temp_sqlite_url(), DOGHUNT_DB_STARTUP_CHECK="true"), \
                patch("app.main.check_database", side_effect=slow), \
                self.assertLogs("app.main", level=logging.WARNING) as logs:
            elapsed = self.run_lifespan()
        self.assertLess(elapsed, 1.4)
        self.assertIn("秒内没有响应", "\n".join(logs.output))

    def test_shutdown_disposes_engine(self):
        with use_database(temp_sqlite_url(), DOGHUNT_DB_STARTUP_CHECK="true"):
            self.run_lifespan()
            self.assertFalse(db_session.engine_created())


if __name__ == "__main__":
    unittest.main()
