"""记忆表与仓储（P3）的离线测试：约束、部分唯一索引、级联、停用与忘记、过滤分页、种子、迁移。

用临时 SQLite + Base.metadata.create_all（仅测试；正式库只走 Alembic）；真实 Postgres 见 test_memory_integration.py。
"""

import _isolation  # noqa: F401  先于任何 app 模块
import hashlib
import io
import logging
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import BACKEND_ROOT, get_settings
from app.db import session as db_session
from app.db.base import Base
from app.models.memory import MemoryChild, MemoryEvent, MemoryExtraction, MemoryParent, MemoryUsage
from app.repositories import memory as repo
from app.services.memory import __main__ as cli
from app.services.memory.parents import PARENT_CATALOG, PARENT_KEYS, get_parent_spec, is_known_parent_key

MEMORY_TABLES = {"memory_parents", "memory_children", "memory_usage", "memory_events", "memory_extractions"}


@contextmanager
def temp_database(create: bool = True, **extra_env: str):
    """临时 SQLite；create=True 时用 create_all 建表。退出时恢复隔离的测试库。"""
    url = f"sqlite:///{Path(tempfile.mkdtemp(prefix='doghunt-test-memory-')) / 'db.sqlite'}"
    with patch.dict(os.environ, {"DOGHUNT_DATABASE_URL": url, **extra_env}):
        get_settings.cache_clear()
        db_session.dispose_engine()
        try:
            if create:
                Base.metadata.create_all(db_session.get_engine())
            yield url
        finally:
            db_session.dispose_engine()
    get_settings.cache_clear()


def schema_drift(connection) -> tuple[list, dict[str, tuple[set[str], set[str]]]]:
    """迁移出来的库与模型的差异：autogenerate 能比的部分（表、列、类型、索引、唯一、外键）加上 CHECK 约束名。

    autogenerate 不比较 CHECK 和部分索引的 WHERE，所以 CHECK 名单独按表比；WHERE 由各自的用例检查。
    """
    diff = compare_metadata(MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata)
    inspector = inspect(connection)
    checks = {}
    for table in Base.metadata.sorted_tables:
        expected = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        actual = {c["name"] for c in inspector.get_check_constraints(table.name)}
        if expected != actual:
            checks[table.name] = (expected, actual)
    return diff, checks


def add_child(session, parent_id: str, title: str = "先给结论", body: str = "先结论后原因。", *,
              status: str = "active", kind: str = "explicit", **extra) -> MemoryChild:
    return repo.insert_child(session, parent_id=parent_id, title=title, body=body, kind=kind, status=status,
                             source_kind="chat", source_ref="abc123def456", source_label="聊天「测试」", **extra)


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self.ctx = temp_database()
        self.ctx.__enter__()
        with db_session.session_scope() as s:
            repo.seed_parents(s)
            self.parent_ids = {p.key: p.id for p in repo.list_parents(s)}
        self.pid = self.parent_ids["output_pref"]

    def tearDown(self):
        self.ctx.__exit__(None, None, None)


class ContentHashTests(unittest.TestCase):
    def test_child_hash_formula_and_whitespace(self):
        expected = hashlib.sha256("标题\n正文 两段\nevent\ng-1".encode()).hexdigest()
        self.assertEqual(repo.child_content_hash(" 标题 ", "正文\n\n  两段", "event", "g-1"), expected)
        self.assertEqual(repo.child_content_hash("t", "b", "global", None),
                         hashlib.sha256(b"t\nb\nglobal\n").hexdigest())
        self.assertNotEqual(repo.child_content_hash("t", "b", "event", "g-1"),
                            repo.child_content_hash("t", "b", "event", "g-2"))
        self.assertEqual(len(repo.child_content_hash("t", "b", "global", None)), 64)

    def test_parent_hash_formula(self):
        self.assertEqual(repo.parent_content_hash("用户输出偏好", "回答  顺序"),
                         hashlib.sha256("用户输出偏好\n回答 顺序".encode()).hexdigest())


class ParentCatalogTests(unittest.TestCase):
    def test_catalog_keys_match_document(self):
        self.assertEqual(PARENT_KEYS, ("output_pref", "method_pref", "team_focus", "event_research", "review_notes"))
        self.assertEqual(get_parent_spec("output_pref").title, "用户输出偏好")
        self.assertTrue(all(spec.collects for spec in PARENT_CATALOG))
        self.assertTrue(is_known_parent_key("review_notes"))
        self.assertFalse(is_known_parent_key("made_up"))
        self.assertIsNone(get_parent_spec("made_up"))


class ConstraintTests(MemoryTestCase):
    def test_sqlite_foreign_keys_enabled(self):
        with db_session.session_scope() as s:
            self.assertEqual(s.execute(text("PRAGMA foreign_keys")).scalar(), 1)
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            add_child(s, "00000000-0000-0000-0000-000000000000")

    def test_partial_unique_index_only_covers_active_and_candidate(self):
        with db_session.session_scope() as s:
            first = add_child(s, self.pid)
        for status in ("active", "candidate"):
            with self.subTest(status=status), self.assertRaises(IntegrityError), db_session.session_scope() as s:
                add_child(s, self.pid, status=status)
        with db_session.session_scope() as s:
            repo.update_child(s, repo.get_child(s, first.id), status="superseded")
            again = add_child(s, self.pid)  # A → B → A'：历史行可同内容
            repo.update_child(s, again, status="disabled")
            add_child(s, self.pid, status="candidate")
            add_child(s, self.parent_ids["method_pref"])  # 不同父块允许相同文字
            same = repo.find_by_content_hash(s, self.pid, first.content_hash)
        self.assertEqual(sorted(row.status for row in same), ["candidate", "disabled", "superseded"])

    def test_scope_label_change_does_not_affect_dedup(self):
        with db_session.session_scope() as s:
            add_child(s, self.pid, scope_label="所有聊天")
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            add_child(s, self.pid, scope_label="全部对话")

    def test_parent_delete_cascades_children_and_usage(self):
        with db_session.session_scope() as s:
            child = add_child(s, self.pid)
            s.add(MemoryUsage(child_id=child.id, trace_id="t1", rank=1, score=0.5))
        with db_session.session_scope() as s:
            s.delete(s.get(MemoryParent, self.pid))
        with db_session.session_scope() as s:
            self.assertIsNone(repo.get_child(s, child.id))
            self.assertEqual(s.scalars(select(MemoryUsage)).all(), [])
            self.assertEqual(len(repo.list_parents(s)), 4)

    def test_superseded_by_set_null_on_delete(self):
        with db_session.session_scope() as s:
            new = add_child(s, self.pid, title="新要求")
            old = add_child(s, self.pid, title="旧要求")
            repo.update_child(s, old, status="superseded", superseded_by=new.id)
        with db_session.session_scope() as s:
            s.delete(repo.get_child(s, new.id))
        with db_session.session_scope() as s:
            self.assertIsNone(repo.get_child(s, old.id).superseded_by)

    def test_check_constraints_reject_bad_values(self):
        cases = {
            "kind": dict(kind="guess"),
            "status": dict(status="deleted"),
            "scope_kind": dict(scope_kind="planet"),
            "title_length": dict(title="长" * 101),
            "body_length": dict(body="长" * 301),
        }
        for name, kwargs in cases.items():
            with self.subTest(name), self.assertRaises(IntegrityError) as caught, db_session.session_scope() as s:
                add_child(s, self.pid, **kwargs)
            self.assertIn("CHECK", str(caught.exception))
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            child = add_child(s, self.pid)
            child.embed_state = "maybe"
            s.flush()
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            repo.add_event(s, "disable", "不在取值里", [])
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            s.add(MemoryExtraction(trace_id="t1", extractor_model="m", prompt_version="v1", outcome="maybe"))
        with db_session.session_scope() as s:  # 边界值可以写入
            add_child(s, self.pid, title="长" * 100, body="长" * 300)

    def test_usage_and_extraction_uniqueness(self):
        with db_session.session_scope() as s:
            child = add_child(s, self.pid)
            s.add(MemoryUsage(child_id=child.id, trace_id="t1", rank=1))
            s.add(MemoryExtraction(trace_id="t1", extractor_model="m", prompt_version="v1"))
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            s.add(MemoryUsage(child_id=child.id, trace_id="t1", rank=2))
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            s.add(MemoryExtraction(trace_id="t1", extractor_model="m", prompt_version="v1"))
        with db_session.session_scope() as s:
            row = s.scalars(select(MemoryExtraction)).one()
            self.assertEqual((row.outcome, row.attempts, row.created_at.tzinfo), ("pending", 0, UTC))
            self.assertIsInstance(s.scalars(select(MemoryUsage)).one().id, int)

    def test_defaults_and_times_are_utc(self):
        with db_session.session_scope() as s:
            child = add_child(s, self.pid, expires_at=datetime(2026, 12, 1, 8, tzinfo=UTC) + timedelta(hours=8))
        with db_session.session_scope() as s:
            row = repo.get_child(s, child.id)
            self.assertEqual(len(row.id), 36)
            self.assertEqual((row.owner_id, row.scope_kind, row.scope_label, row.embed_state, row.embed_model,
                              row.version, row.recalls), ("local", "global", "所有聊天", "pending", "", 1, 0))
            self.assertEqual(row.expires_at, datetime(2026, 12, 1, 16, tzinfo=UTC))
            self.assertEqual(row.updated_at.tzinfo, UTC)


class ChildStateTests(MemoryTestCase):
    def test_disable_keeps_in_list_forget_hides(self):
        with db_session.session_scope() as s:
            a = add_child(s, self.pid, title="停用的")
            b = add_child(s, self.pid, title="忘记的")
            a.embed_state = b.embed_state = "indexed"
            s.flush()
        with db_session.session_scope() as s:
            disabled = repo.disable_child(s, a.id)
            forgotten = repo.forget_child(s, b.id)
            self.assertEqual((disabled.status, disabled.forgotten_at, disabled.embed_state), ("disabled", None, "pending"))
            self.assertEqual((forgotten.status, forgotten.embed_state), ("disabled", "pending"))
            self.assertEqual(forgotten.forgotten_at.tzinfo, UTC)
        with db_session.session_scope() as s:
            self.assertEqual([c.id for c in repo.list_children(s)], [a.id])
            self.assertEqual({c.id for c in repo.list_children(s, include_forgotten=True)}, {a.id, b.id})
            self.assertEqual([c.id for c in repo.list_children(s, statuses=["disabled"])], [a.id])
            self.assertIsNotNone(repo.get_child(s, b.id))  # 详情仍可查
            events = repo.list_events(s)
            self.assertEqual([(e.action, e.block_ids) for e in events], [("forget", [b.id])])
            first_forgotten_at = repo.get_child(s, b.id).forgotten_at
            repo.forget_child(s, b.id)  # 再忘一次：不重复写事件、不改时间
            self.assertEqual(len(repo.list_events(s)), 1)
            self.assertEqual(repo.get_child(s, b.id).forgotten_at, first_forgotten_at)

    def test_restore_clears_forgotten_and_writes_event(self):
        with db_session.session_scope() as s:
            child = add_child(s, self.pid)
            repo.forget_child(s, child.id)
            restored = repo.restore_child(s, child.id)
            self.assertEqual((restored.status, restored.forgotten_at, restored.embed_state), ("active", None, "pending"))
            self.assertEqual([e.action for e in repo.list_events(s)], ["restore", "forget"])
            with self.assertRaises(ValueError):
                repo.restore_child(s, child.id)  # 已是 active
            for fn in (repo.disable_child, repo.forget_child, repo.restore_child):
                with self.assertRaises(LookupError):
                    fn(s, "no-such-id")

    def test_restore_keeps_version_chain_and_expiry(self):
        past = datetime(2026, 1, 1, tzinfo=UTC)
        with db_session.session_scope() as s:
            new = add_child(s, self.pid, title="B 新要求")
            old = add_child(s, self.pid, title="A 旧要求")
            repo.update_child(s, old, status="superseded", superseded_by=new.id)
            expired = add_child(s, self.pid, title="过期的比赛笔记", expires_at=past)
            for child in (old, expired):
                repo.forget_child(s, child.id)
            self.assertEqual(repo.restore_child(s, old.id).status, "superseded")  # 不和 B 同时生效
            self.assertEqual(repo.restore_child(s, expired.id).status, "expired")
            live = [c.id for c in repo.list_children(s, statuses=["active"])]
            self.assertEqual(live, [new.id])
            self.assertEqual([e.detail for e in repo.list_events(s, limit=2)],
                             ["恢复“过期的比赛笔记”为已过期。", "恢复“A 旧要求”为已替代。"])
            future = add_child(s, self.pid, title="未过期", expires_at=datetime.now(UTC) + timedelta(days=1))
            repo.disable_child(s, future.id)
            self.assertEqual(repo.restore_child(s, future.id).status, "active")

    def test_restore_conflicting_with_live_duplicate_raises(self):
        with db_session.session_scope() as s:
            first = add_child(s, self.pid)
            repo.disable_child(s, first.id)
            add_child(s, self.pid)  # 同内容新建了一条生效的
        with self.assertRaises(IntegrityError), db_session.session_scope() as s:
            repo.restore_child(s, first.id)

    def test_update_child_recomputes_hash_and_embed_state(self):
        with db_session.session_scope() as s:
            child = add_child(s, self.pid)
            child.embed_state = "indexed"
            s.flush()
            old_hash, old_updated = child.content_hash, child.updated_at
            repo.update_child(s, child, scope_label="新的展示文字")
            self.assertEqual((child.content_hash, child.embed_state), (old_hash, "indexed"))  # 展示文字不影响
            repo.update_child(s, child, body="新的正文")
            self.assertEqual(child.content_hash, repo.child_content_hash(child.title, "新的正文", "global", None))
            self.assertEqual(child.embed_state, "pending")
            self.assertGreaterEqual(child.updated_at, old_updated)
            child.embed_state = "indexed"
            repo.update_child(s, child, status="expired")
            self.assertEqual(child.embed_state, "pending")
            stamp = child.updated_at
            repo.update_child(s, child, status="expired")  # 没有实际变化
            self.assertEqual(child.updated_at, stamp)
            with self.assertRaises(ValueError):
                repo.update_child(s, child, forgotten_at=None)
            with self.assertRaises(ValueError):
                repo.update_child(s, child, parent_id=self.parent_ids["team_focus"])

    def test_milvus_fields_reset_embed_state(self):
        """第 8.2 节子块集合里的过滤字段和向量文本变了，向量就过时，要回到 pending。"""
        later = datetime(2027, 1, 1, tzinfo=UTC)
        with db_session.session_scope() as s:
            child = add_child(s, self.pid)
            other = add_child(s, self.pid, title="另一条")
            for field, value in {"kind": "inferred", "event_id": "g-9", "expires_at": later, "scope_kind": "event",
                                 "scope_key": "g-9", "title": "新标题", "body": "新正文", "status": "candidate"}.items():
                with self.subTest(field):
                    child.embed_state = "indexed"
                    s.flush()
                    repo.update_child(s, child, **{field: value})
                    self.assertEqual(child.embed_state, "pending")
            for field, value in {"scope_label": "新的展示文字", "source_label": "新来源", "source_ref": "r2",
                                 "source_kind": "record", "version": 2, "superseded_by": other.id}.items():
                with self.subTest(field):
                    child.embed_state = "indexed"
                    s.flush()
                    repo.update_child(s, child, **{field: value})
                    self.assertEqual(child.embed_state, "indexed")

    def test_forgotten_row_status_cannot_change_without_restore(self):
        with db_session.session_scope() as s:
            child = add_child(s, self.pid)
            repo.forget_child(s, child.id)
            with self.assertRaises(ValueError):
                repo.update_child(s, child, status="active")
            repo.update_child(s, child, scope_label="仍可改展示文字")  # 不改状态的修改照常
            self.assertEqual((child.status, child.scope_label), ("disabled", "仍可改展示文字"))
        with self.assertRaises(IntegrityError) as caught, db_session.session_scope() as s:  # 库里的 CHECK 兜底
            s.execute(text("UPDATE memory_children SET status = 'active' WHERE id = :id"), {"id": child.id})
        self.assertIn("CHECK", str(caught.exception))


class ListingTests(MemoryTestCase):
    def test_empty_database_lists_are_empty(self):
        with temp_database():  # 不种子的空库
            with db_session.session_scope() as s:
                self.assertEqual((repo.list_parents(s), repo.list_children(s), repo.list_events(s)), ([], [], []))

    def test_order_pagination_and_filters(self):
        base = datetime(2026, 11, 1, tzinfo=UTC)
        ids = []
        with db_session.session_scope() as s:
            for i in range(5):
                parent = self.pid if i % 2 == 0 else self.parent_ids["review_notes"]
                child = add_child(s, parent, title=f"条目{i}", status="candidate" if i == 4 else "active")
                child.updated_at = base + timedelta(hours=i)
                ids.append(child.id)
            s.flush()
        newest_first = list(reversed(ids))
        with db_session.session_scope() as s:
            self.assertEqual([c.id for c in repo.list_children(s)], newest_first)
            self.assertEqual([c.id for c in repo.list_children(s, limit=2, offset=2)], newest_first[2:4])
            self.assertEqual(repo.list_children(s, offset=10), [])
            self.assertEqual(len(repo.list_children(s, limit=0)), 1)  # 下限 1
            self.assertEqual({c.id for c in repo.list_children(s, parent_id=self.pid)}, {ids[0], ids[2], ids[4]})
            self.assertEqual([c.id for c in repo.list_children(s, statuses=["candidate"])], [ids[4]])
            self.assertEqual([c.id for c in repo.list_children(s, parent_id=self.pid, statuses=["active"])],
                             [ids[2], ids[0]])
            self.assertEqual(repo.list_children(s, statuses=[]), [])
            with self.assertRaises(ValueError):
                repo.list_children(s, statuses=["deleted"])

    def test_find_by_content_hash_newest_first(self):
        with db_session.session_scope() as s:
            old = add_child(s, self.pid)
            old.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
            repo.update_child(s, old, status="superseded")
            old.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
            new = add_child(s, self.pid)
            found = repo.find_by_content_hash(s, self.pid, new.content_hash)
            self.assertEqual([c.id for c in found], [new.id, old.id])
            self.assertEqual(repo.find_by_content_hash(s, self.parent_ids["team_focus"], new.content_hash), [])

    def test_events_order_limit_and_json_round_trip(self):
        with db_session.session_scope() as s:
            for i in range(3):
                event = repo.add_event(s, "merge", f"第{i}次", [f"id-{i}", "共同"])
                event.created_at = datetime(2026, 11, 1, i, tzinfo=UTC)
            s.flush()
        with db_session.session_scope() as s:
            events = repo.list_events(s, limit=2)
            self.assertEqual([e.detail for e in events], ["第2次", "第1次"])
            self.assertEqual(events[0].block_ids, ["id-2", "共同"])
            self.assertEqual([e.detail for e in repo.list_events(s, limit=2, offset=2)], ["第0次"])
            self.assertIsInstance(s.scalars(select(MemoryEvent)).first().id, int)


class SeedTests(unittest.TestCase):
    def test_seed_parents_idempotent_and_title_sync(self):
        with temp_database():
            with db_session.session_scope() as s:
                first = repo.seed_parents(s)
            with db_session.session_scope() as s:
                second = repo.seed_parents(s)
                parents = repo.list_parents(s)
            self.assertEqual(first.created, list(PARENT_KEYS))
            self.assertEqual((second.created, second.updated, second.unchanged), ([], [], list(PARENT_KEYS)))
            self.assertEqual([p.key for p in parents], list(PARENT_KEYS))
            p = parents[0]
            self.assertEqual((p.summary, p.embed_state, p.version), ("回答顺序、长度、格式、赔率显示", "pending", 1))
            self.assertEqual(p.content_hash, repo.parent_content_hash(p.title, p.summary))
            with db_session.session_scope() as s:
                parent = repo.get_parent_by_key(s, "output_pref")
                parent.embed_state, parent.summary = "indexed", "程序维护的摘要"
            renamed = [type(PARENT_CATALOG[0])("output_pref", "输出偏好（改名）", "x"), *PARENT_CATALOG[1:]]
            with db_session.session_scope() as s:
                third = repo.seed_parents(s, renamed)
                parent = repo.get_parent_by_key(s, "output_pref")
            self.assertEqual(third.updated, ["output_pref"])
            self.assertEqual((parent.title, parent.summary, parent.version, parent.embed_state),
                             ("输出偏好（改名）", "程序维护的摘要", 2, "pending"))
            self.assertEqual(parent.content_hash, repo.parent_content_hash("输出偏好（改名）", "程序维护的摘要"))

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_cli_seed_list_and_errors(self):
        with temp_database():
            self.assertEqual(self.run_cli("seed", "--parents")[0], 0)
            code, out, _ = self.run_cli("seed", "--parents", "--samples")
            self.assertEqual(code, 0)
            self.assertIn("新建 0", out)
            self.assertIn(f"新建 {len(cli.SAMPLES)}", out)
            self.assertIn(f"跳过 {len(cli.SAMPLES)}", self.run_cli("seed", "--samples")[1])
            with db_session.session_scope() as s:
                self.assertEqual(len(repo.list_parents(s)), 5)
                children = repo.list_children(s)
                self.assertEqual(len(children), len(cli.SAMPLES))
                self.assertTrue(all(c.source_label == "开发样例" for c in children))
                repo.forget_child(s, children[0].id)
            code, out, _ = self.run_cli("list")
            self.assertEqual(code, 0)
            self.assertIn("[output_pref] 用户输出偏好", out)
            self.assertNotIn(children[0].id, out)
            self.assertIn("已忘记", self.run_cli("list", "--all")[1])
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                cli.main(["seed"])
            self.assertEqual(caught.exception.code, 2)
        with temp_database():
            self.assertIn("还没有父块", self.run_cli("list")[1])

    def test_cli_samples_refused_in_production(self):
        with temp_database(DOGHUNT_ENVIRONMENT="production"):
            code, _, err = self.run_cli("seed", "--samples")
            self.assertEqual(code, 1)
            self.assertIn("production", err)
            with db_session.session_scope() as s:
                self.assertEqual(repo.list_children(s), [])

    def test_cli_database_unavailable_exit_1_without_password(self):
        password = "CliSecret42pass"
        with temp_database(create=False, DOGHUNT_DATABASE_URL=f"postgresql+psycopg://doghunt:{password}@127.0.0.1:1/doghunt"):
            code, out, err = self.run_cli("seed", "--parents")
        self.assertEqual(code, 1)
        self.assertIn("数据库操作失败", err)
        self.assertNotIn(password, out + err)

    def test_cli_registers_password_itself(self):
        # 驱动错误里回显了密码：CLI 入口自己登记密码，不靠测试或调用方事先登记
        password = "CliEchoSecret77x"
        echoed = OperationalError("SELECT 1", {}, Exception(f'password authentication failed: "{password}"'))
        with temp_database(create=False, DOGHUNT_DATABASE_URL=f"postgresql+psycopg://doghunt:{password}@127.0.0.1:1/doghunt"), \
                patch.object(repo, "seed_parents", side_effect=echoed):
            code, out, err = self.run_cli("seed", "--parents")
        self.assertEqual(code, 1)
        self.assertIn("password authentication failed", err)
        self.assertNotIn(password, out + err)

    def test_cli_invalid_url_exit_1_and_program_errors_not_hidden(self):
        with temp_database(create=False, DOGHUNT_DATABASE_URL="postgresql+psycopg://u:p@h:notaport/db"):
            self.assertEqual(self.run_cli("list")[0], 1)
        with temp_database(), patch.object(repo, "list_parents", side_effect=ValueError("程序错误")):
            with self.assertRaises(ValueError):
                self.run_cli("list")


class MigrationTests(unittest.TestCase):
    def test_upgrade_downgrade_upgrade_creates_and_drops_memory_tables(self):
        with temp_database(create=False):
            config = Config(str(BACKEND_ROOT / "alembic.ini"))
            config.attributes["configure_logger"] = False
            root = logging.getLogger()
            level, handlers = root.level, list(root.handlers)

            def tables() -> set[str]:
                return set(inspect(db_session.get_engine()).get_table_names()) & MEMORY_TABLES

            command.upgrade(config, "head")
            self.assertEqual(db_session.check_database().revision, "0002")
            self.assertEqual(tables(), MEMORY_TABLES)
            command.downgrade(config, "-1")
            self.assertEqual(db_session.check_database().revision, "0001")
            self.assertEqual(tables(), set())
            command.upgrade(config, "head")
            self.assertEqual(db_session.check_database().revision, "0002")
            self.assertEqual((root.level, root.handlers), (level, handlers))
            with db_session.get_engine().connect() as conn:
                self.assertEqual(schema_drift(conn), ([], {}), "迁移出来的结构与模型不一致")
            with db_session.get_engine().connect() as conn:
                index_sql = conn.execute(text(
                    "SELECT sql FROM sqlite_master WHERE name='uq_memory_children_live_content'")).scalar_one()
            self.assertIn("UNIQUE", index_sql)
            self.assertIn("WHERE status IN ('active', 'candidate')", index_sql)
            with db_session.get_engine().connect() as conn:
                table_sql = conn.execute(text(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_children'")).scalar_one()
            # 约束名与模型一致，不被命名约定套两次
            self.assertIn("CONSTRAINT ck_memory_children_kind CHECK", table_sql)
            self.assertIn("CONSTRAINT fk_memory_children_superseded_by_memory_children", table_sql)
            self.assertNotIn("ck_memory_children_ck_", table_sql)
            # 迁移出来的库同样遵守部分唯一索引
            with db_session.session_scope() as s:
                repo.seed_parents(s)
                pid = repo.get_parent_by_key(s, "output_pref").id
                add_child(s, pid, status="superseded")
                add_child(s, pid)
            with self.assertRaises(IntegrityError), db_session.session_scope() as s:
                add_child(s, pid, status="candidate")


if __name__ == "__main__":
    unittest.main()
