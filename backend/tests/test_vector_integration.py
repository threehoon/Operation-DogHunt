"""真实 Milvus 集成测试（P4.4）：DOGHUNT_TEST_INTEGRATION=1 时才运行；连不上 Milvus 时 skip，不算失败。

只用独立的测试别名和版本（doghunt_test_memory_child / doghunt_test_memory_parent，版本 itest、itest2），
不碰正式别名 doghunt_memory_*；开始前和结束后都删掉测试别名和集合。地址读 backend/.env 的 DOGHUNT_MILVUS_URI
（默认 http://127.0.0.1:19530）。运行：

    cd backend && DOGHUNT_TEST_INTEGRATION=1 uv run --locked python -m unittest discover -s tests \\
        -p 'test_vector_integration.py' -v
"""

import _isolation  # noqa: F401  先于任何 app 模块
import io
import os
import time
import unittest
from unittest.mock import patch

from app.core.config import get_settings
from app.integrations import vectorstore as vs
from app.integrations.vectorstore import ChildVector, MilvusVectorStore, ParentVector
from app.services.memory import vector_init

ENABLED = os.environ.get("DOGHUNT_TEST_INTEGRATION") == "1"
DIM = 8
CHILD_ALIAS, PARENT_ALIAS = "doghunt_test_memory_child", "doghunt_test_memory_parent"
VERSIONS = ("itest", "itest2")
TEST_ENV = {
    "DOGHUNT_VECTORSTORE_PROVIDER": "milvus",
    "DOGHUNT_MILVUS_CHILD_ALIAS": CHILD_ALIAS,
    "DOGHUNT_MILVUS_PARENT_ALIAS": PARENT_ALIAS,
    "DOGHUNT_MILVUS_COLLECTION_VERSION": "itest",
    "DOGHUNT_EMBEDDING_DIM": str(DIM),
    "DOGHUNT_MILVUS_TIMEOUT_SECONDS": "5",
}
NOW = int(time.time())
WAIT_SECONDS = 15  # 检索用 Bounded 一致性，新写入/删除要等一会儿才可见


def vec(index: int, second: float = 0.0) -> list[float]:
    values = [0.0] * DIM
    values[index] = 1.0
    values[(index + 1) % DIM] = second
    return values


def child(id_: str, vector: list[float], **kwargs) -> ChildVector:
    base = {"parent_id": "itest-parent-1", "kind": "explicit", "scope_kind": "global", "updated_at": NOW,
            "content_hash": "h" * 64, "embed_model": "itest-model"}
    return ChildVector(id=id_, vector=vector, **{**base, **kwargs})


@unittest.skipUnless(ENABLED, "设置 DOGHUNT_TEST_INTEGRATION=1 才连接真实 Milvus")
class MilvusIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = patch.dict(os.environ, TEST_ENV)
        cls.env.start()
        get_settings.cache_clear()
        try:
            cls.admin = vs.new_client(get_settings())
            cls.admin.get_server_version()
        except Exception as exc:  # noqa: BLE001  没有服务时跳过
            cls.env.stop()
            get_settings.cache_clear()
            raise unittest.SkipTest(f"连不上 Milvus（{vs.safe_uri(get_settings().milvus_uri)}）：{vs.short_error(exc)}")
        cls.cleanup()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.cleanup()
            leftover = [n for n in cls.admin.list_collections() if n.startswith("doghunt_test_memory_")]
            assert not leftover, leftover
        finally:
            cls.admin.close()
            cls.env.stop()
            get_settings.cache_clear()

    @classmethod
    def cleanup(cls):
        aliases = set((cls.admin.list_aliases() or {}).get("aliases", []))
        for alias in (CHILD_ALIAS, PARENT_ALIAS):
            if alias in aliases:
                cls.admin.drop_alias(alias)
        for alias in (CHILD_ALIAS, PARENT_ALIAS):
            for version in VERSIONS:
                name = vs.collection_name(alias, version)
                if cls.admin.has_collection(name):
                    cls.admin.drop_collection(name)

    def run_init(self, *, force=False, **env) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {f"DOGHUNT_{k.upper()}": v for k, v in env.items()}):
            get_settings.cache_clear()
            try:
                code = vector_init.run_init(force, retries=6, retry_interval=10, out=out, err=err)
            finally:
                get_settings.cache_clear()
        return code, out.getvalue() + err.getvalue()

    def wait_for(self, fn, expected, message):
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            value = fn()
            if value == expected or time.monotonic() > deadline:
                self.assertEqual(value, expected, message)
                return
            time.sleep(0.5)

    def test_1_init_idempotent_and_alias(self):
        code, output = self.run_init()
        self.assertEqual(code, 0, output)
        self.assertNotIn("doghunt_memory_child", output)  # 没碰正式别名
        code, output = self.run_init()
        self.assertEqual(code, 0, output)
        self.assertIn("未变", output)
        self.assertNotIn("已建", output)
        for alias in (CHILD_ALIAS, PARENT_ALIAS):
            self.assertEqual(self.admin.describe_alias(alias)["collection_name"], f"{alias}_itest")
        child_name = f"{CHILD_ALIAS}_itest"
        types = {self.admin.describe_index(child_name, i)["field_name"]: self.admin.describe_index(child_name, i)["index_type"]
                 for i in self.admin.list_indexes(child_name)}
        self.assertEqual(types, {"vector": "HNSW", "parent_id": "INVERTED", "event_id": "INVERTED"})
        self.assertEqual(MilvusVectorStore().check().status, "ok")

    def test_2_insert_search_filter_delete(self):
        self.assertEqual(self.run_init()[0], 0)
        store = MilvusVectorStore()
        try:
            store.upsert_children([
                child("c-global", vec(0)),
                child("c-expired", vec(0, 0.1), expires_at=NOW - 10),
                child("c-evt1", vec(0, 0.2), event_id="itest-evt-1", scope_kind="event"),
                child("c-evt2", vec(0, 0.3), event_id="itest-evt-2", parent_id="itest-parent-2"),
                child("c-far", vec(4)),
            ])
            search = lambda **kw: [h[0] for h in store.search_children(vec(0), 10, now_epoch=NOW, **kw)]  # noqa: E731
            self.wait_for(lambda: len(search()), 4, "写入后应能搜到 4 条未过期的子块")
            hits = store.search_children(vec(0), 10, now_epoch=NOW)
            self.assertEqual(hits[0][0], "c-global")
            self.assertAlmostEqual(hits[0][1], 1.0, places=4)
            self.assertNotIn("c-expired", search())
            self.assertEqual(set(search(event_id="itest-evt-1")), {"c-global", "c-evt1", "c-far"})
            self.assertEqual(set(search(event_id="")), {"c-global", "c-far"})
            self.assertEqual(search(parent_ids=["itest-parent-2"]), ["c-evt2"])
            self.assertEqual(store.list_child_ids(), {"c-global", "c-expired", "c-evt1", "c-evt2", "c-far"})

            store.delete_children(["c-global", "c-evt1"])
            self.wait_for(lambda: set(search()), {"c-evt2", "c-far"}, "删除后不应再搜到")
            self.assertEqual(store.list_child_ids(), {"c-expired", "c-evt2", "c-far"})

            store.upsert_parents([ParentVector("itest-parent-1", vec(1), "output_pref"),
                                  ParentVector("itest-parent-2", vec(2), "method_pref")])
            self.wait_for(lambda: [h[0] for h in store.search_parents(vec(2), 1)], ["itest-parent-2"], "父块检索")
            self.assertEqual(store.list_parent_ids(), {"itest-parent-1", "itest-parent-2"})
        finally:
            store.close()

    def test_3_alias_conflict_then_force(self):
        self.assertEqual(self.run_init()[0], 0)
        code, output = self.run_init(milvus_collection_version="itest2")
        self.assertEqual(code, vector_init.EXIT_ALIAS_CONFLICT, output)
        self.assertEqual(self.admin.describe_alias(CHILD_ALIAS)["collection_name"], f"{CHILD_ALIAS}_itest")
        code, output = self.run_init(force=True, milvus_collection_version="itest2")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.admin.describe_alias(CHILD_ALIAS)["collection_name"], f"{CHILD_ALIAS}_itest2")
        self.assertTrue(self.admin.has_collection(f"{CHILD_ALIAS}_itest"))  # 旧集合保留
        code, output = self.run_init(force=True)  # 切回 itest
        self.assertEqual(code, 0, output)
        self.assertEqual(self.admin.describe_alias(CHILD_ALIAS)["collection_name"], f"{CHILD_ALIAS}_itest")

    def test_4_dim_mismatch(self):
        self.assertEqual(self.run_init()[0], 0)
        code, output = self.run_init(embedding_dim="16")
        self.assertEqual(code, vector_init.EXIT_MISMATCH, output)
        self.assertIn(f"collection dim={DIM}, config dim=16", output)
        with patch.dict(os.environ, {"DOGHUNT_EMBEDDING_DIM": "16"}):
            get_settings.cache_clear()
            store = MilvusVectorStore()
            try:
                status = store.check()
            finally:
                store.close()
                get_settings.cache_clear()
        self.assertEqual(status.status, "down")
        self.assertIn("config dim=16", status.error)


if __name__ == "__main__":
    unittest.main()
