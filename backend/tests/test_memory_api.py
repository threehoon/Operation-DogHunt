"""记忆只读接口（P3.3）的离线测试：返回形状与前端类型对齐、默认不含已忘记、过滤分页、404、数据库不可用时 503。"""

import _isolation  # noqa: F401  先于任何 app 模块
import re
import unittest
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.core.config import BACKEND_ROOT
from app.db import session as db_session
from app.main import create_app
from app.repositories import memory as repo
from test_memory_store import add_child, temp_database

FRONTEND_TYPES = BACKEND_ROOT.parent / "frontend" / "src" / "types" / "memory.ts"


def frontend_fields(interface: str) -> set[str]:
    """从 frontend/src/types/memory.ts 读出某个 interface 的字段名，接口字段要和它对齐。"""
    source = FRONTEND_TYPES.read_text(encoding="utf-8")
    block = re.search(rf"export interface {interface} \{{(.*?)\n\}}", source, re.S).group(1)
    return set(re.findall(r"^\s{2}(\w+)\??:", block, re.M))


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() is not None, f"时间没有偏移：{value}"
    return parsed


class MemoryApiTests(unittest.TestCase):
    def setUp(self):
        self.ctx = temp_database()
        self.ctx.__enter__()
        self.client = TestClient(create_app())
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.ctx.__exit__(None, None, None)

    def seed(self) -> dict[str, str]:
        base = datetime(2026, 11, 1, tzinfo=UTC)
        with db_session.session_scope() as s:
            repo.seed_parents(s)
            out = repo.get_parent_by_key(s, "output_pref").id
            review = repo.get_parent_by_key(s, "review_notes").id
            new = add_child(s, out, title="先给结论，再讲原因")
            old = add_child(s, out, title="先讲原因，再给结论", status="superseded")
            repo.update_child(s, old, superseded_by=new.id)
            disabled = add_child(s, review, title="连续客场第三场", kind="observation",
                                 scope_kind="event", scope_key="g-1", scope_label="赤狐 对 猎犬", event_id="g-1",
                                 expires_at=base + timedelta(days=3))
            forgotten = add_child(s, review, title="被忘记的")
            for i, child in enumerate((old, new, disabled, forgotten)):
                child.updated_at = base + timedelta(hours=i)
            s.flush()
            repo.disable_child(s, disabled.id)
            repo.forget_child(s, forgotten.id)
            repo.add_event(s, "conflict", "新要求替代旧要求", [new.id, old.id])
            return {"out": out, "review": review, "new": new.id, "old": old.id,
                    "disabled": disabled.id, "forgotten": forgotten.id}

    def get(self, path: str = "/api/v1/memory", **params):
        return self.client.get(path, params=params)

    def test_empty_database_returns_empty_arrays(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"parents": [], "children": [], "log": []})

    def test_shape_matches_frontend_types(self):
        ids = self.seed()
        body = self.get().json()
        self.assertEqual(set(body), {"parents", "children", "log"})
        self.assertEqual([p["key"] for p in body["parents"]],
                         ["output_pref", "method_pref", "team_focus", "event_research", "review_notes"])
        for name, rows in (("MemoryParent", body["parents"]), ("MemoryChild", body["children"]),
                           ("MaintenanceEntry", body["log"])):
            expected = frontend_fields(name)
            self.assertTrue(expected, name)
            for row in rows:
                self.assertLessEqual(expected, set(row), f"{name} 缺少前端字段")
        child = next(c for c in body["children"] if c["id"] == ids["old"])
        self.assertEqual(child["parentId"], ids["out"])
        self.assertEqual(child["supersededBy"], ids["new"])
        self.assertEqual(child["source"], {"kind": "chat", "label": "聊天「测试」"})
        self.assertEqual(child["scope"], "所有聊天")
        self.assertIsNone(child["expiresAt"])
        self.assertEqual(parse_time(child["updatedAt"]), datetime(2026, 11, 1, tzinfo=UTC))
        self.assertTrue(child["updatedAt"].endswith("+00:00"))
        disabled = next(c for c in body["children"] if c["id"] == ids["disabled"])
        self.assertEqual((disabled["status"], disabled["scope"], disabled["scopeKind"], disabled["scopeKey"],
                          disabled["eventId"], disabled["forgottenAt"]),
                         ("disabled", "赤狐 对 猎犬", "event", "g-1", "g-1", None))
        self.assertEqual(parse_time(disabled["expiresAt"]), datetime(2026, 11, 4, tzinfo=UTC))
        log = body["log"]
        self.assertEqual([e["action"] for e in log], ["conflict", "forget"])
        self.assertTrue(all(isinstance(e["id"], str) and e["id"].isdigit() for e in log))
        self.assertEqual(log[0]["blockIds"], [ids["new"], ids["old"]])
        parse_time(log[0]["time"])

    def test_forgotten_hidden_by_default_and_filters(self):
        ids = self.seed()
        listed = [c["id"] for c in self.get().json()["children"]]
        self.assertEqual(listed, [ids["disabled"], ids["new"], ids["old"]])  # updatedAt 新的在前；停用的仍在
        everything = self.get(include_forgotten="true").json()["children"]
        forgotten = next(c for c in everything if c["id"] == ids["forgotten"])
        self.assertIsNotNone(parse_time(forgotten["forgottenAt"]))
        self.assertEqual([c["id"] for c in self.get(parent_id=ids["out"]).json()["children"]], [ids["new"], ids["old"]])
        multi = self.client.get("/api/v1/memory?status=superseded&status=disabled").json()["children"]
        self.assertEqual([c["id"] for c in multi], [ids["disabled"], ids["old"]])
        self.assertEqual([c["id"] for c in self.get(limit=1, offset=1).json()["children"]], [ids["new"]])
        self.assertEqual(len(self.get(log_limit=1).json()["log"]), 1)
        self.assertEqual(len(self.get(parent_id=ids["out"]).json()["parents"]), 5)  # 父块不受过滤影响

    def test_invalid_query_is_422(self):
        for params in ({"status": "deleted"}, {"limit": 0}, {"limit": 501}, {"offset": -1}, {"log_limit": 0}):
            with self.subTest(params):
                self.assertEqual(self.get(**params).status_code, 422)

    def test_child_detail_and_404(self):
        ids = self.seed()
        response = self.get(f"/api/v1/memory/children/{ids['forgotten']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], ids["forgotten"])
        self.assertIsNotNone(response.json()["forgottenAt"])
        self.assertLessEqual(frontend_fields("MemoryChild"), set(response.json()))
        missing = self.get("/api/v1/memory/children/00000000-0000-0000-0000-000000000000")
        self.assertEqual(missing.status_code, 404)
        self.assertIn("没有找到这条记忆", missing.json()["detail"])

    def test_bad_row_is_500_not_503(self):
        # 数据校验错误（pydantic ValidationError 也是 ValueError）不能报成“数据库不可用”
        with db_session.session_scope() as s:
            repo.add_event(s, "merge", "坏数据", [1])
        with TestClient(create_app(), raise_server_exceptions=False) as client:
            response = client.get("/api/v1/memory")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("记忆库暂时不可用", response.text)


class MemoryApiUnavailableTests(unittest.TestCase):
    PASSWORD = "MemApiSecret42"

    def assert_unavailable(self, response):
        self.assertEqual(response.status_code, 503)
        self.assertIn("记忆库暂时不可用", response.json()["detail"])
        self.assertNotIn(self.PASSWORD, response.text)
        self.assertNotIn("127.0.0.1:1", response.text)

    def test_database_down_is_503_without_password(self):
        url = f"postgresql+psycopg://doghunt:{self.PASSWORD}@127.0.0.1:1/doghunt"
        with temp_database(create=False, DOGHUNT_DATABASE_URL=url), TestClient(create_app()) as client:
            with self.assertLogs("app.memory", "WARNING") as logs:
                self.assert_unavailable(client.get("/api/v1/memory"))
            self.assert_unavailable(client.get("/api/v1/memory/children/x"))
        self.assertNotIn(self.PASSWORD, "\n".join(logs.output))

    def test_unmigrated_database_is_503(self):
        with temp_database(create=False), TestClient(create_app()) as client:
            self.assert_unavailable(client.get("/api/v1/memory"))

    def test_invalid_url_is_503(self):
        with temp_database(create=False, DOGHUNT_DATABASE_URL="postgresql+psycopg://u:p@h:notaport/db"), \
                TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/v1/memory").status_code, 503)


if __name__ == "__main__":
    unittest.main()
