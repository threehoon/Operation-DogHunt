"""真实存储的集成测试，默认跳过；DOGHUNT_TEST_INTEGRATION=1 时才运行。

P2：连本机 make storage-up 起的 Postgres，在单独的测试库（默认 doghunt_test，跑完删除）里验证
迁移 upgrade → downgrade -1 → upgrade、UTCDateTime 时区往返、会话回滚和 /health/storage。
连接参数取 backend/.env 的 DOGHUNT_DB_*（不使用 _isolation 强制的 SQLite）；不会动日常库 doghunt 的数据。
P3：迁移到 0002 后五张记忆表存在且能完整删除、部分唯一索引与 SQLite 行为一致、block_ids 是 JSONB、
外键级联与 SET NULL、CHECK、seed --parents 幂等、GET /api/v1/memory。
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
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.core.config import BACKEND_ROOT, Settings, get_settings
from app.db import session as db_session
from app.db.types import UTCDateTime
from app.main import create_app
from app.models.memory import MemoryParent, MemoryUsage
from app.repositories import memory as repo
from test_memory_store import schema_drift

ENABLED = os.environ.get("DOGHUNT_TEST_INTEGRATION") == "1"
TEST_DB_NAME = os.environ.get("DOGHUNT_TEST_DB_NAME", "doghunt_test")
HEAD = "0002"
MEMORY_TABLES = {"memory_parents", "memory_children", "memory_usage", "memory_events", "memory_extractions"}


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

    def memory_tables(self) -> set[str]:
        with db_session.session_scope() as s:
            names = s.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")).scalars()
            return set(names) & MEMORY_TABLES

    def fresh_memory_tables(self) -> None:
        """迁移到最新并清空记忆表，让每个用例单独运行也成立。"""
        self.alembic("upgrade", "head")
        with db_session.session_scope() as s:
            s.execute(text("TRUNCATE " + ", ".join(sorted(MEMORY_TABLES)) + " CASCADE"))

    def add_child(self, session, parent_id: str, status: str = "active", **extra):
        values = dict(title="先给结论", body="先结论后原因。", kind="explicit", source_kind="chat",
                      source_ref="abc123def456", source_label="聊天「集成」")
        values.update(extra)
        return repo.insert_child(session, parent_id=parent_id, status=status, **values)

    def test_1_migration_cycle_on_empty_database(self):
        with db_session.session_scope() as s:
            tables = s.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")).scalar()
        self.assertEqual(tables, 0, "测试库应该是空的")
        self.alembic("upgrade", "head")
        self.assertEqual(self.revision(), HEAD)
        self.assertEqual(self.memory_tables(), MEMORY_TABLES)
        self.alembic("downgrade", "-1")
        self.assertEqual(self.revision(), "0001")
        self.assertEqual(self.memory_tables(), set(), "downgrade 应删掉全部记忆表")
        self.alembic("upgrade", "head")
        self.assertEqual(self.revision(), HEAD)
        self.alembic("downgrade", "base")
        self.assertIsNone(self.revision())
        self.alembic("upgrade", "head")
        self.assertEqual(self.memory_tables(), MEMORY_TABLES)
        self.assertIn(f"{HEAD} (head)", self.alembic("current"))
        with db_session.get_engine().connect() as conn:
            self.assertEqual(schema_drift(conn), ([], {}), "Postgres 上迁移出来的结构与模型不一致")

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
        self.assertEqual(body["database"]["revision"], HEAD)
        self.assertTrue(body["database"]["url"].startswith("postgresql://doghunt@"))
        self.assertTrue(body["database"]["url"].endswith(f"/{TEST_DB_NAME}"))
        if self.password:
            self.assertNotIn(self.password, response.text)


    def test_5_memory_schema_constraints_on_postgres(self):
        self.fresh_memory_tables()
        with db_session.session_scope() as s:
            column_types = dict(s.execute(text(
                "SELECT table_name || '.' || column_name, data_type FROM information_schema.columns "
                "WHERE table_name IN ('memory_events', 'memory_children', 'memory_usage')")).all())
            index_def = s.execute(text(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_memory_children_live_content'")).scalar_one()
            checks = set(s.execute(text(
                "SELECT conname FROM pg_constraint WHERE contype = 'c' AND conrelid = 'memory_children'::regclass")).scalars())
            fk_rules = dict(s.execute(text(
                "SELECT constraint_name, delete_rule FROM information_schema.referential_constraints "
                "WHERE constraint_name LIKE 'fk_memory_%'")).all())
        self.assertEqual(column_types["memory_events.block_ids"], "jsonb")
        self.assertEqual(column_types["memory_children.expires_at"], "timestamp with time zone")
        self.assertEqual(column_types["memory_usage.id"], "bigint")
        self.assertIn("UNIQUE INDEX", index_def)
        self.assertIn("WHERE", index_def)
        self.assertIn("'active'", index_def)
        self.assertIn("'candidate'", index_def)
        self.assertLessEqual({"ck_memory_children_kind", "ck_memory_children_status", "ck_memory_children_embed_state",
                              "ck_memory_children_title_length", "ck_memory_children_body_length",
                              "ck_memory_children_forgotten_disabled"}, checks)
        self.assertEqual(fk_rules, {
            "fk_memory_children_parent_id_memory_parents": "CASCADE",
            "fk_memory_children_superseded_by_memory_children": "SET NULL",
            "fk_memory_usage_child_id_memory_children": "CASCADE",
        })

        # 部分唯一索引：与 SQLite 离线测试同样的序列
        with db_session.session_scope() as s:
            repo.seed_parents(s)
            pid = repo.get_parent_by_key(s, "output_pref").id
            first = self.add_child(s, pid)
        for status in ("active", "candidate"):
            with self.subTest(status=status), self.assertRaises(IntegrityError), db_session.session_scope() as s:
                self.add_child(s, pid, status=status)
        with db_session.session_scope() as s:
            repo.update_child(s, repo.get_child(s, first.id), status="superseded")
            again = self.add_child(s, pid)
            repo.update_child(s, again, status="disabled")
            live = self.add_child(s, pid, status="candidate")
            self.add_child(s, repo.get_parent_by_key(s, "method_pref").id)
            self.assertEqual(len(repo.find_by_content_hash(s, pid, first.content_hash)), 3)

        # CHECK；标题是 varchar(100)，Postgres 先按列长度报 DataError，CHECK 是第二道
        for bad in ({"kind": "guess"}, {"status": "deleted"}, {"title": "长" * 101}, {"body": "长" * 301}):
            with self.subTest(bad), self.assertRaises((IntegrityError, DataError)), db_session.session_scope() as s:
                self.add_child(s, pid, **{"title": "另一条", **bad})

        # JSONB 读写与包含查询；忘记写事件
        with db_session.session_scope() as s:
            repo.forget_child(s, again.id)
            event = repo.add_event(s, "merge", "合并", [live.id, "共同"])
        with db_session.session_scope() as s:
            self.assertEqual(repo.list_events(s)[0].block_ids, [live.id, "共同"])
            hits = s.execute(text("SELECT id FROM memory_events WHERE block_ids @> CAST(:ids AS jsonb)"),
                             {"ids": f'["{live.id}"]'}).scalars().all()
            self.assertEqual(hits, [event.id])
            self.assertEqual(repo.get_child(s, again.id).forgotten_at.tzinfo, UTC)
            self.assertNotIn(again.id, [c.id for c in repo.list_children(s)])
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:  # 已忘记的行只能是 disabled
            s.execute(text("UPDATE memory_children SET status = 'archived' WHERE id = :id"), {"id": again.id})

        # 自引用 SET NULL、父块删除级联子块和使用记录
        with db_session.session_scope() as s:
            repo.update_child(s, repo.get_child(s, first.id), superseded_by=live.id)
            s.add(MemoryUsage(child_id=live.id, trace_id="t1", rank=1, score=0.42))
        with db_session.session_scope() as s:
            s.delete(repo.get_child(s, live.id))
        with db_session.session_scope() as s:
            self.assertIsNone(repo.get_child(s, first.id).superseded_by)
            self.assertEqual(s.execute(text("SELECT count(*) FROM memory_usage")).scalar(), 0)
            s.delete(s.get(MemoryParent, pid))
        with db_session.session_scope() as s:
            self.assertEqual(repo.list_children(s, parent_id=pid, include_forgotten=True), [])
            self.assertEqual(s.execute(text("SELECT count(*) FROM memory_children WHERE parent_id = :p"),
                                       {"p": pid}).scalar(), 0)

    def test_6_seed_command_idempotent_and_memory_api(self):
        self.fresh_memory_tables()
        for _ in range(2):  # 与 make memory-seed 相同的命令行，跑两次
            result = subprocess.run([sys.executable, "-m", "app.services.memory", "seed", "--parents"], cwd=BACKEND_ROOT,
                                    capture_output=True, text=True, timeout=60,
                                    env={**os.environ, "DOGHUNT_DATABASE_URL": self.test_url})
            self.assertEqual(result.returncode, 0, result.stderr)
            if self.password:
                self.assertNotIn(self.password, result.stdout + result.stderr)
        self.assertIn("未变 5", result.stdout)
        with db_session.session_scope() as s:
            self.assertEqual(s.execute(text("SELECT count(*) FROM memory_parents")).scalar(), 5)
            pid = repo.get_parent_by_key(s, "event_research").id
            child = self.add_child(s, pid, kind="hypothesis", scope_kind="event", scope_key="g-1", event_id="g-1",
                                   scope_label="赤狐 对 猎犬", expires_at=datetime(2026, 11, 20, 12, tzinfo=UTC))
        with TestClient(create_app()) as client:
            response = client.get("/api/v1/memory")
            detail = client.get(f"/api/v1/memory/children/{child.id}")
            missing = client.get("/api/v1/memory/children/00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([p["key"] for p in body["parents"]],
                         ["output_pref", "method_pref", "team_focus", "event_research", "review_notes"])
        self.assertEqual([c["id"] for c in body["children"]], [child.id])
        self.assertEqual(body["children"][0]["expiresAt"], "2026-11-20T12:00:00+00:00")
        self.assertEqual(body["children"][0]["parentId"], pid)
        self.assertEqual(detail.json()["scope"], "赤狐 对 猎犬")
        self.assertEqual(missing.status_code, 404)
        if self.password:
            self.assertNotIn(self.password, response.text)


if __name__ == "__main__":
    unittest.main()
