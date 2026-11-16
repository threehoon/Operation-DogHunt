"""向量库（P4）的离线测试：假实现、过滤表达式、Milvus 封装（mock 客户端）、集合初始化、健康接口与启动检查。

不需要 Docker：Milvus 用内存里的 MockMilvusClient 模拟；真实 Milvus 见 test_vector_integration.py。
"""

import _isolation  # noqa: F401  先于任何 app 模块
import asyncio
import io
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core import applog
from app.core.config import BACKEND_ROOT, Settings, get_settings
from app.integrations import vectorstore as vs
from app.integrations.vectorstore import (
    NEVER_EXPIRES,
    ChildVector,
    FakeVectorStore,
    MilvusVectorStore,
    ParentVector,
    VectorStoreError,
    build_child_filter,
)
from app.main import create_app, lifespan
from app.services.memory import vector_init

DIM = 4
NOW = 1_800_000_000
TOKEN = "MilvusTok3n-ShouldNeverLeak"


def vec(*values: float) -> list[float]:
    return list(values) + [0.0] * (DIM - len(values))


def child(id_: str, vector: list[float], **kwargs) -> ChildVector:
    base = {"parent_id": "p1", "kind": "explicit", "scope_kind": "global", "updated_at": NOW,
            "content_hash": "h" * 64, "embed_model": "bge-m3"}
    return ChildVector(id=id_, vector=vector, **{**base, **kwargs})


def settings(**overrides) -> Settings:
    values = {"embedding_dim": DIM, "milvus_timeout_seconds": 2, "vectorstore_provider": "milvus", **overrides}
    return Settings(_env_file=None, **values)


@contextmanager
def use_env(**env: str):
    """临时改环境变量并清配置缓存与向量库实例；退出时恢复隔离配置。"""
    with patch.dict(os.environ, {f"DOGHUNT_{k.upper()}": v for k, v in env.items()}):
        get_settings.cache_clear()
        vs.close_vector_store()
        try:
            yield
        finally:
            vs.close_vector_store()
    get_settings.cache_clear()


# ---------------------------------------------------------------- 模拟 Milvus 客户端


class NodeNotMatch(Exception):
    message = "node not match[expectedNodeID=7][actualNodeID=8]"


class MockIndexParams:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add_index(self, field_name: str, index_type: str = "", **kwargs) -> None:
        self.items.append({"field_name": field_name, "index_type": index_type, **kwargs})


class MockMilvusClient:
    """只实现 vectorstore.py / vector_init.py 用到的方法；记录调用，可注入故障。"""

    def __init__(self) -> None:
        self.collections: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.failures: dict[str, list[BaseException]] = {}  # 方法名 → 依次抛出的异常
        self.reject_inverted = False
        self.search_result: list[list[dict]] = [[]]
        self.query_batches: list[list[dict]] = []
        self.closed = False

    def _hit(self, method: str, *args, **kwargs) -> None:
        self.calls.append((method, args, kwargs))
        queue = self.failures.get(method)
        if queue:
            raise queue.pop(0)

    def _resolve(self, name: str) -> str:
        return self.aliases.get(name, name)

    def names(self, method: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == method]

    def prepare_index_params(self) -> MockIndexParams:
        return MockIndexParams()

    def has_collection(self, name, timeout=None, **kwargs):
        self._hit("has_collection", name, timeout=timeout)
        return name in self.collections

    def create_collection(self, name, schema=None, timeout=None, **kwargs):
        self._hit("create_collection", name, timeout=timeout)
        fields = [{"name": f.name, "params": dict(f.params)} for f in schema.fields]
        self.collections[name] = {"fields": fields, "indexes": {}, "loaded": False}

    def describe_collection(self, name, timeout=None, **kwargs):
        self._hit("describe_collection", name, timeout=timeout)
        real = self._resolve(name)
        return {"collection_name": real, "fields": self.collections[real]["fields"]}

    def list_indexes(self, name):
        self._hit("list_indexes", name)
        return list(self.collections[name]["indexes"])

    def describe_index(self, name, index_name, timeout=None, **kwargs):
        self._hit("describe_index", name, index_name)
        return dict(self.collections[name]["indexes"][index_name])

    def create_index(self, name, index_params, timeout=None, **kwargs):
        self._hit("create_index", name, index_params.items[0]["index_type"], timeout=timeout)
        for item in index_params.items:
            if item["index_type"] == "INVERTED" and self.reject_inverted:
                raise RuntimeError("index type INVERTED not supported")
            self.collections[name]["indexes"][item["field_name"]] = item

    def get_load_state(self, name, timeout=None, **kwargs):
        self._hit("get_load_state", name, timeout=timeout)
        return {"state": "Loaded" if self.collections[self._resolve(name)]["loaded"] else "NotLoad"}

    def load_collection(self, name, timeout=None, **kwargs):
        self._hit("load_collection", name, timeout=timeout)
        self.collections[self._resolve(name)]["loaded"] = True

    def list_aliases(self, timeout=None, **kwargs):
        self._hit("list_aliases", timeout=timeout, **kwargs)
        return {"aliases": list(self.aliases), "db_name": "default"}

    def describe_alias(self, alias, timeout=None, **kwargs):
        self._hit("describe_alias", alias, timeout=timeout)
        return {"alias": alias, "collection_name": self.aliases[alias], "db_name": "default"}

    def create_alias(self, name, alias, timeout=None, **kwargs):
        self._hit("create_alias", name, alias)
        self.aliases[alias] = name

    def alter_alias(self, name, alias, timeout=None, **kwargs):
        self._hit("alter_alias", name, alias)
        self.aliases[alias] = name

    def upsert(self, name, rows, timeout=None, **kwargs):
        self._hit("upsert", name, rows, timeout=timeout, **kwargs)

    def delete(self, name, ids=None, timeout=None, filter=None, **kwargs):
        self._hit("delete", name, ids=ids, filter=filter, timeout=timeout, **kwargs)

    def search(self, name, **kwargs):
        self._hit("search", name, **kwargs)
        return self.search_result

    def query_iterator(self, name, **kwargs):
        self._hit("query_iterator", name, **kwargs)
        batches = list(self.query_batches) + [[]]

        class Iterator:
            closed = False

            def next(self):
                return batches.pop(0)

            def close(self):
                Iterator.closed = True
        self.iterator = Iterator
        return Iterator()

    def close(self):
        self.closed = True


def initialized_client(dim: int = DIM, *, loaded: bool = True) -> MockMilvusClient:
    """已经跑过 run_init 的模拟客户端。"""
    client = MockMilvusClient()
    with use_env(embedding_dim=str(dim)):
        assert vector_init.run_init(client=client, out=io.StringIO(), err=io.StringIO()) == 0
    for collection in client.collections.values():
        collection["loaded"] = loaded
    client.calls.clear()
    return client


# ---------------------------------------------------------------- 假实现与过滤表达式


class FakeVectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeVectorStore(settings(vectorstore_provider="fake"))

    def test_upsert_search_delete_and_list(self):
        self.assertEqual(self.store.upsert_children([child("a", vec(1)), child("b", vec(1, 1)), child("c", vec(0, 1))]), 3)
        hits = self.store.search_children(vec(1), 2, now_epoch=NOW)
        self.assertEqual([h[0] for h in hits], ["a", "b"])
        self.assertAlmostEqual(hits[0][1], 1.0)
        self.assertAlmostEqual(hits[1][1], 2 ** -0.5)
        self.store.upsert_children([child("a", vec(0, 0, 1))])  # 同 id 覆盖
        self.assertEqual(self.store.search_children(vec(1), 1, now_epoch=NOW)[0][0], "b")
        self.store.delete_children(["a", "a", "missing"])
        self.assertEqual(self.store.list_child_ids(), {"b", "c"})
        self.store.delete_children([])

    def test_expiry_sentinel_and_filters(self):
        self.store.upsert_children([
            child("forever", vec(1)),  # expires_at=None → 2100 年
            child("expired", vec(1), expires_at=NOW),
            child("future", vec(1), expires_at=NOW + 60),
            child("evt", vec(1), event_id="evt-1", scope_kind="event"),
            child("other", vec(1), event_id="evt-2", parent_id="p2"),
        ])
        self.assertEqual(self.store.children["forever"]["expires_at"], NEVER_EXPIRES)
        self.assertEqual(self.store.children["forever"]["event_id"], "")
        ids = lambda **kw: {h[0] for h in self.store.search_children(vec(1), 10, now_epoch=NOW, **kw)}  # noqa: E731
        self.assertEqual(ids(), {"forever", "future", "evt", "other"})  # expires_at > now，等于 now 也算过期
        self.assertEqual(ids(event_id="evt-1"), {"forever", "future", "evt"})
        self.assertEqual(ids(event_id=""), {"forever", "future"})
        self.assertEqual(ids(parent_ids=["p2"]), {"other"})
        self.assertEqual(ids(parent_ids=[]), set())
        self.assertEqual(ids(event_id="evt-2", parent_ids=["p1"]), {"forever", "future"})

    def test_parents(self):
        self.store.upsert_parents([ParentVector("p1", vec(1), "output_pref"), ParentVector("p2", vec(0, 1), "method")])
        self.assertEqual(self.store.search_parents(vec(0, 1), 1)[0][0], "p2")
        self.store.delete_parents(["p2"])
        self.assertEqual(self.store.list_parent_ids(), {"p1"})

    def test_invalid_rows_rejected_before_any_write(self):
        bad_rows = [
            child("x", vec(1)[:3]),  # 维度不对
            child("x", [1.0, float("nan"), 0, 0]),
            child("x" * 37, vec(1)),  # id 超过 36
            child("x", vec(1), kind="k" * 65),
            child("x", vec(1), embed_model="m" * 101),
            child("x", vec(1), event_id='e" or id != "'),
            child("x", vec(1), expires_at=True),
        ]
        for row in bad_rows:
            with self.subTest(row=row.id), self.assertRaises(ValueError):
                self.store.upsert_children([child("ok", vec(1)), row])
        self.assertEqual(self.store.list_child_ids(), set())
        self.store.upsert_children([child("x", vec(1), embed_model="m" * 100, kind="k" * 64)])  # 恰好到上限可以
        with self.assertRaises(ValueError):
            self.store.search_children(vec(1), 0, now_epoch=NOW)
        with self.assertRaises(ValueError):
            self.store.search_children(vec(1), 5, now_epoch=NOW, event_id="a b")

    def test_check_reports_fake(self):
        status = self.store.check()
        self.assertEqual(status.status, "fake")
        self.assertTrue(status.ok)
        self.assertEqual(self.store.ensure_loaded().status, "fake")


class FilterExpressionTests(unittest.TestCase):
    def test_expressions(self):
        self.assertEqual(build_child_filter(NOW), f"expires_at > {NOW}")
        self.assertEqual(build_child_filter(NOW, "evt-1"),
                         f'expires_at > {NOW} and (event_id == "evt-1" or event_id == "")')
        self.assertEqual(build_child_filter(NOW, ""), f'expires_at > {NOW} and event_id == ""')
        uid = "0b5c1a7e-9f3d-4c1e-8a2b-1234567890ab"
        self.assertEqual(build_child_filter(NOW, parent_ids=[uid, "p2", uid]),
                         f'expires_at > {NOW} and parent_id in ["{uid}", "p2"]')

    def test_injection_and_bad_values_rejected(self):
        for bad in ('x" or id != "', "x\\", "a b", "引号", "", "x" * 65, 5, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_child_filter(NOW, parent_ids=[bad])
        for bad in ('evt" or event_id != "', "evt\n", 7):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_child_filter(NOW, bad)
        for bad in ("1700000000", 1.5, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_child_filter(bad)
        with self.assertRaises(ValueError):
            build_child_filter(NOW, parent_ids=[])
        with self.assertRaises(ValueError):
            build_child_filter(NOW, parent_ids=[f"p{i}" for i in range(101)])

    def test_quote_value_escapes(self):
        self.assertEqual(vs.quote_value("evt-1"), '"evt-1"')
        with self.assertRaises(ValueError):
            vs.quote_value('a"b')

    def test_safe_uri_and_short_error(self):
        self.assertEqual(vs.safe_uri("http://root:Secret1@127.0.0.1:19530/?token=abc"), "http://127.0.0.1:19530/")
        self.assertEqual(vs.safe_uri("http://127.0.0.1:19530"), "http://127.0.0.1:19530")
        self.assertEqual(vs.safe_uri("not a uri"), "（地址格式无效）")
        self.assertEqual(vs.safe_uri("http://h:notaport"), "（地址格式无效）")
        applog.register_secret(TOKEN)
        self.assertNotIn(TOKEN, vs.short_error(RuntimeError(f"auth {TOKEN} failed\nstack")))
        self.assertIn("NodeNotMatch: node not match", vs.short_error(NodeNotMatch("ignored")))
        self.assertIn("没有详细信息", vs.short_error(TimeoutError()))


# ---------------------------------------------------------------- Milvus 封装（模拟客户端）


class MilvusVectorStoreTests(unittest.TestCase):
    def make(self, client=None, **overrides) -> MilvusVectorStore:
        self.client = client or initialized_client()
        return MilvusVectorStore(settings(**overrides), client_factory=lambda **_: self.client)

    def test_search_uses_alias_filter_ef_bounded_and_timeouts(self):
        store = self.make()
        self.client.search_result = [[{"id": "a", "distance": 0.9, "entity": {}}, {"id": "b", "distance": 0.4}]]
        hits = store.search_children(vec(1), 12, now_epoch=NOW, event_id="evt-1", parent_ids=["p1"])
        self.assertEqual(hits, [("a", 0.9), ("b", 0.4)])
        _, args, kwargs = self.client.names("search")[-1]
        self.assertEqual(args, ("doghunt_memory_child",))  # 只用别名
        self.assertEqual(kwargs["filter"],
                         f'expires_at > {NOW} and (event_id == "evt-1" or event_id == "") and parent_id in ["p1"]')
        self.assertEqual(kwargs["search_params"], {"metric_type": "COSINE", "params": {"ef": 64}})
        self.assertEqual((kwargs["limit"], kwargs["consistency_level"], kwargs["timeout"], kwargs["retry_times"]),
                         (12, "Bounded", 2.0, 0))
        self.assertIsInstance(kwargs["timeout"], float)  # float + retry_times=0：只发一次请求
        store.search_children(vec(1), 3, now_epoch=NOW, timeout=0.5)
        self.assertEqual(self.client.names("search")[-1][2]["timeout"], 0.5)
        calls = len(self.client.calls)
        self.assertEqual(store.search_children(vec(1), 3, now_epoch=NOW, parent_ids=[]), [])
        self.assertEqual(len(self.client.calls), calls)  # 空父块列表不发请求
        store.search_parents(vec(1), 3)
        self.assertEqual(self.client.names("search")[-1][1], ("doghunt_memory_parent",))

    def test_upsert_delete_list(self):
        store = self.make()
        self.assertEqual(store.upsert_children([child("a", vec(1), event_id=None)]), 1)
        _, (alias, rows), _ = self.client.names("upsert")[-1]
        self.assertEqual(alias, "doghunt_memory_child")
        self.assertEqual(set(rows[0]), {"id", "vector", "parent_id", "kind", "scope_kind", "event_id", "expires_at",
                                        "updated_at", "content_hash", "embed_model"})  # 不存 status（D13）
        self.assertEqual((rows[0]["event_id"], rows[0]["expires_at"]), ("", NEVER_EXPIRES))
        self.assertEqual(store.upsert_children([]), 0)
        store.delete_children(["a", "a", "b"])
        delete_kwargs = self.client.names("delete")[-1][2]
        self.assertEqual((delete_kwargs["ids"], delete_kwargs["filter"], delete_kwargs["retry_times"]),
                         (None, 'id in ["a", "b"]', 0))
        store.delete_children([f"id-{i}" for i in range(250)])
        self.assertEqual(len(self.client.names("delete")), 1 + 3)  # 每批最多 100 个
        _, _, upsert_kwargs = self.client.names("upsert")[0]
        self.assertEqual(upsert_kwargs["timeout"], 2)  # upsert 用 int 超时（内部读结构那步不接收 retry_times）
        self.assertIsInstance(upsert_kwargs["timeout"], int)
        store.upsert_parents([ParentVector("p1", vec(1), "output_pref")])
        self.assertEqual(self.client.names("upsert")[-1][1][0], "doghunt_memory_parent")
        self.client.query_batches = [[{"id": "a"}, {"id": "b"}], [{"id": "c"}]]
        self.assertEqual(store.list_child_ids(), {"a", "b", "c"})
        self.assertTrue(self.client.iterator.closed)
        self.assertEqual(self.client.names("query_iterator")[-1][2]["consistency_level"], "Strong")

    def test_errors_become_redacted_vectorstore_error(self):
        applog.register_secret(TOKEN)
        store = self.make(milvus_token=SecretStr(TOKEN))
        self.client.failures["search"] = [RuntimeError(f"rpc failed token={TOKEN}")]
        with self.assertRaises(VectorStoreError) as caught:
            store.search_children(vec(1), 3, now_epoch=NOW)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.client.failures["upsert"] = [TimeoutError()]
        with self.assertRaises(VectorStoreError):
            store.upsert_children([child("a", vec(1))])
        with self.assertRaises(ValueError):  # 入参错误不包装，直接是调用方的 bug
            store.search_children(vec(1), 3, now_epoch=NOW, event_id='" or 1')

    def test_check_ok_not_initialized_and_down(self):
        store = self.make()
        status = store.check()
        self.assertEqual(status.status, "ok")
        self.assertEqual(status.collections, {"doghunt_memory_child": "doghunt_memory_child_v1",
                                              "doghunt_memory_parent": "doghunt_memory_parent_v1"})
        self.assertEqual((status.dim, status.uri, status.error), (DIM, "http://127.0.0.1:19530", None))

        uninitialized = self.make(MockMilvusClient()).check()
        self.assertEqual(uninitialized.status, "not_initialized")
        self.assertIn("make memory-init", uninitialized.detail)
        self.assertFalse(self.client.names("describe_alias"))  # 不 describe 不存在的别名（避免 pymilvus 刷 ERROR 日志）

        mismatch = self.make(initialized_client(dim=8)).check()
        self.assertEqual(mismatch.status, "down")
        self.assertIn("collection dim=8, config dim=4", mismatch.error)

        unloaded = self.make(initialized_client(loaded=False)).check()
        self.assertEqual(unloaded.status, "down")
        self.assertIn("未加载", unloaded.error)

        timing_out = self.make()
        self.client.failures["list_aliases"] = [TimeoutError()]
        self.assertEqual(timing_out.check().status, "down")

    def test_connect_failure_is_down_and_retried_next_time(self):
        attempts = []

        def factory(**_):
            attempts.append(1)
            if len(attempts) == 1:
                raise ConnectionError("Fail connecting to server on 127.0.0.1:1")
            return initialized_client()
        store = MilvusVectorStore(settings(milvus_uri=f"http://root:{TOKEN}@127.0.0.1:1"), client_factory=factory,
                                  connect_cooldown=0)
        first = store.check()
        self.assertEqual(first.status, "down")
        self.assertIn("ConnectionError", first.error)
        self.assertEqual(first.uri, "http://127.0.0.1:1")
        self.assertNotIn(TOKEN, str(first))
        self.assertEqual(store.check().status, "ok")
        self.assertEqual(len(attempts), 2)

    def test_ensure_loaded_loads_once_only_when_just_unloaded(self):
        store = self.make(initialized_client(loaded=False))
        self.assertEqual(store.ensure_loaded().status, "ok")
        self.assertEqual(len(self.client.names("load_collection")), 2)

        store = self.make(initialized_client(dim=8, loaded=False))
        self.assertEqual(store.ensure_loaded().status, "down")
        self.assertFalse(self.client.names("load_collection"))  # 维度不符时不碰集合

        store = self.make(initialized_client(loaded=False))
        self.client.failures["load_collection"] = [RuntimeError("out of memory")]
        status = store.ensure_loaded()
        self.assertEqual(status.status, "down")
        self.assertIn("load 失败", status.error)

    def test_close(self):
        store = self.make()
        store.check()
        store.close()
        self.assertTrue(self.client.closed)
        self.assertEqual(store.check().status, "down")
        with self.assertRaises(VectorStoreError):
            store.list_child_ids()
        store.close()  # 重复关闭不报错


class PymilvusRetryBoundTests(unittest.TestCase):
    """用 pymilvus 真实的重试装饰器和虚拟时钟：服务连上后断开（UNAVAILABLE），调用要在约 timeout 秒内结束。"""

    def setUp(self):
        import grpc
        from pymilvus import MilvusClient, decorators

        class Unavailable(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNAVAILABLE

            def details(self):
                return "connection refused"

        clock = {"now": 1000.0}
        fake_time = type("FakeTime", (), {
            "time": staticmethod(lambda: clock["now"]),
            "sleep": staticmethod(lambda seconds: clock.update(now=clock["now"] + seconds)),
        })
        patcher = patch.object(decorators, "time", fake_time)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock, self.attempts = clock, []
        retry = decorators.retry_on_rpc_failure

        test = self

        class Handler:  # 模拟 GrpcHandler：方法都套真实装饰器，每次都 UNAVAILABLE
            @retry()
            def list_aliases(self, collection_name="", timeout=None, **kwargs):
                test.attempts.append("list_aliases")
                raise Unavailable()

            @retry()
            def describe_collection(self, collection_name, timeout=None, **kwargs):
                test.attempts.append("describe_collection")
                raise Unavailable()

            @retry()
            def upsert_rows(self, collection_name, entities, partition_name=None, timeout=None, **kwargs):
                # 真实实现在这里先读集合结构，且只传 timeout、不传 retry_times
                self.describe_collection(collection_name, timeout=timeout)

        self.client = object.__new__(MilvusClient)
        self.client._get_connection = lambda: Handler()

    def elapsed(self, fn) -> float:
        start = self.clock["now"]
        fn()
        return self.clock["now"] - start

    def test_float_timeout_without_retry_limit_is_the_bug(self):
        with self.assertRaises(Exception):
            self.client.list_aliases(timeout=2.0)
        self.assertGreater(len(self.attempts), 70)  # 默认按次数重试 75 次，与 timeout 无关

    def test_check_returns_down_after_single_attempt(self):
        store = MilvusVectorStore(settings(), client_factory=lambda **_: self.client)
        elapsed = self.elapsed(lambda: self.assertEqual(store.check().status, "down"))
        self.assertEqual(self.attempts, ["list_aliases"])
        self.assertLess(elapsed, 2)

    def test_upsert_is_bounded_by_int_timeout(self):
        store = MilvusVectorStore(settings(), client_factory=lambda **_: self.client)

        def run():
            with self.assertRaises(VectorStoreError):
                store.upsert_children([child("a", vec(1))])
        elapsed = self.elapsed(run)
        self.assertLessEqual(elapsed, 2 + 3)  # 超时 2 秒 + 最后一次退避（pymilvus 最长 3 秒）
        self.assertLess(len(self.attempts), 15)

    def test_rpc_timeout_rounds_up_to_int(self):
        self.assertEqual([vs.rpc_timeout(x) for x in (0.2, 1, 2.0, 2.1)], [1, 1, 2, 3])
        self.assertTrue(all(isinstance(vs.rpc_timeout(x), int) for x in (0.2, 2.0)))
        self.assertEqual(vs.single_attempt(2), {"timeout": 2.0, "retry_times": 0})


class ConnectConcurrencyTests(unittest.TestCase):
    def test_waiters_give_up_after_their_timeout_and_failure_is_cached(self):
        attempts = []

        def slow_refuse(*, timeout):
            attempts.append(timeout)
            time.sleep(1)
            raise ConnectionError("Fail connecting to server")
        store = MilvusVectorStore(settings(), client_factory=slow_refuse)
        results: list[tuple[float, str]] = []

        def worker():
            started = time.perf_counter()
            status = store.check(timeout=0.2)
            results.append((time.perf_counter() - started, status.status))
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
            time.sleep(0.02)
        for t in threads:
            t.join()
        self.assertEqual([r[1] for r in results], ["down"] * 3)
        times = sorted(r[0] for r in results)
        self.assertLess(times[1], 0.6)  # 另外两个线程只等 0.2 秒锁，不排队累加
        self.assertLess(times[2], 1.5)
        self.assertEqual(attempts, [0.2])  # 建连接用调用方的 timeout，只尝试一次
        started = time.perf_counter()
        status = store.check(timeout=0.2)
        self.assertLess(time.perf_counter() - started, 0.1)  # 冷却期内直接 down
        self.assertIn("秒内不重试", status.error)
        self.assertEqual(len(attempts), 1)

    def test_reconnects_after_cooldown(self):
        attempts = []

        def flaky(*, timeout):
            attempts.append(1)
            if len(attempts) == 1:
                raise ConnectionError("down")
            return initialized_client()
        store = MilvusVectorStore(settings(), client_factory=flaky, connect_cooldown=0.05)
        self.assertEqual(store.check().status, "down")
        self.assertEqual(store.check().status, "down")  # 冷却期内
        time.sleep(0.06)
        self.assertEqual(store.check().status, "ok")
        self.assertEqual(len(attempts), 2)


class DeleteInjectionTests(unittest.TestCase):
    def test_milvus_delete_rejects_quotes_and_uses_own_filter(self):
        client = initialized_client()
        store = MilvusVectorStore(settings(), client_factory=lambda **_: client)
        for bad in ("a'] or id not in ['", 'a"] or id != "', "a b", "x" * 37, ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                store.delete_children(["ok-1", bad])
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                store.delete_parents([bad])
        self.assertFalse(client.names("delete"))
        uid = "0b5c1a7e-9f3d-4c1e-8a2b-1234567890ab"
        store.delete_children([uid])
        self.assertEqual(client.names("delete")[-1][2]["filter"], f'id in ["{uid}"]')
        with self.assertRaises(ValueError):
            store.upsert_children([child("a'] or ['", vec(1))])
        with self.assertRaises(ValueError):
            vs.build_id_filter([])


class InvalidUriTests(unittest.TestCase):
    def test_bad_uri_app_starts_and_reports_down(self):
        bad = "http://user:BadUriPass55@[::1"
        with use_env(vectorstore_provider="milvus", milvus_startup_check="true", milvus_uri=bad), \
                self.assertLogs("app.main", level=logging.WARNING), TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            response = client.get("/api/v1/health/storage")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["status"], body["milvus"]["status"], body["milvus"]["uri"]),
                         ("degraded", "down", "（地址格式无效）"))
        self.assertNotIn("BadUriPass55", response.text)
        self.assertNotIn("BadUriPass55", applog.redact(f"uri={bad}"))  # 解析不了时整条地址当秘密


class NewClientTests(unittest.TestCase):
    def test_each_client_gets_own_connection_and_failures_are_unregistered(self):
        pymilvus = vs._pymilvus()
        config = settings(milvus_token=SecretStr(TOKEN), milvus_timeout_seconds=3)
        with patch.object(pymilvus, "MilvusClient") as client_cls:
            vs.new_client(config)
            vs.new_client(config, timeout=7)
        first, second = (call.kwargs for call in client_cls.call_args_list)
        self.assertNotEqual(first["alias"], second["alias"])  # 一个 close() 不会断掉另一个
        self.assertEqual((first["token"], first["timeout"], second["timeout"]), (TOKEN, 3, 7))
        with patch.object(pymilvus, "MilvusClient", side_effect=ConnectionError("down")), \
                patch.object(pymilvus.connections, "remove_connection") as remove, self.assertRaises(ConnectionError):
            vs.new_client(config)
        self.assertTrue(remove.call_args.args[0].startswith("doghunt-"))


class GetVectorStoreTests(unittest.TestCase):
    def test_provider_selection_and_rebuild_on_new_settings(self):
        with use_env(vectorstore_provider="fake"):
            store = vs.get_vector_store()
            self.assertIsInstance(store, FakeVectorStore)
            self.assertIs(store, vs.get_vector_store())
        with use_env(vectorstore_provider="milvus"):
            self.assertIsInstance(vs.get_vector_store(), MilvusVectorStore)  # 只建对象，不连接
            self.assertTrue(vs.vector_store_created())
        self.assertFalse(vs.vector_store_created())


# ---------------------------------------------------------------- 集合初始化


class RunInitTests(unittest.TestCase):
    def run_init(self, client, *, force=False, **env) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        self.sleeps: list[float] = []
        with use_env(embedding_dim=str(env.pop("dim", DIM)), **env):
            code = vector_init.run_init(force, client=client, retries=3, retry_interval=0.01,
                                        sleep=self.sleeps.append, out=out, err=err)
        return code, out.getvalue() + err.getvalue()

    def test_creates_everything_then_idempotent(self):
        client = MockMilvusClient()
        code, output = self.run_init(client)
        self.assertEqual(code, 0, output)
        child_v1 = client.collections["doghunt_memory_child_v1"]
        names = {f["name"]: f["params"] for f in child_v1["fields"]}
        self.assertEqual(names["vector"], {"dim": DIM})
        self.assertEqual({k: v.get("max_length") for k, v in names.items() if k not in ("vector",)},
                         {"id": 36, "parent_id": 64, "kind": 64, "scope_kind": 64, "event_id": 64, "expires_at": None,
                          "updated_at": None, "content_hash": 64, "embed_model": 100})
        self.assertNotIn("status", names)
        self.assertEqual(child_v1["indexes"]["vector"]["params"], {"M": 16, "efConstruction": 200})
        self.assertEqual(child_v1["indexes"]["vector"]["metric_type"], "COSINE")
        self.assertEqual({f: i["index_type"] for f, i in child_v1["indexes"].items()},
                         {"vector": "HNSW", "parent_id": "INVERTED", "event_id": "INVERTED"})
        self.assertEqual(set(client.collections["doghunt_memory_parent_v1"]["indexes"]), {"vector"})
        self.assertTrue(all(c["loaded"] for c in client.collections.values()))
        self.assertEqual(client.aliases, {"doghunt_memory_child": "doghunt_memory_child_v1",
                                          "doghunt_memory_parent": "doghunt_memory_parent_v1"})
        self.assertIn("实际类型 INVERTED", output)

        client.calls.clear()
        code, output = self.run_init(client)
        self.assertEqual(code, 0, output)
        mutating = {"create_collection", "create_index", "load_collection", "create_alias", "alter_alias"}
        self.assertFalse([c for c in client.calls if c[0] in mutating])
        self.assertIn("未变", output)

    def test_half_initialized_collection_is_completed(self):
        client = MockMilvusClient()
        self.run_init(client)
        child_v1 = client.collections["doghunt_memory_child_v1"]
        del child_v1["indexes"]["event_id"]
        child_v1["loaded"] = False
        del client.aliases["doghunt_memory_parent"]
        code, _ = self.run_init(client)
        self.assertEqual(code, 0)
        self.assertIn("event_id", child_v1["indexes"])
        self.assertTrue(child_v1["loaded"])
        self.assertIn("doghunt_memory_parent", client.aliases)

    def test_dim_mismatch_exits_nonzero_without_alias(self):
        client = MockMilvusClient()
        self.run_init(client, dim=8)
        del client.aliases["doghunt_memory_child"]
        client.calls.clear()
        code, output = self.run_init(client, dim=DIM)
        self.assertEqual(code, vector_init.EXIT_MISMATCH)
        self.assertIn("collection dim=8, config dim=4", output)
        self.assertNotIn("doghunt_memory_child", client.aliases)  # 不进入半可用状态
        self.assertFalse(client.names("drop_collection"))
        self.assertFalse(client.names("create_collection"))

    def test_alias_conflict_needs_force(self):
        client = MockMilvusClient()
        self.run_init(client)
        code, output = self.run_init(client, milvus_collection_version="v2")
        self.assertEqual(code, vector_init.EXIT_ALIAS_CONFLICT)
        self.assertIn("已指向 doghunt_memory_child_v1", output)
        self.assertIn("--force", output)
        self.assertEqual(client.aliases["doghunt_memory_child"], "doghunt_memory_child_v1")
        code, output = self.run_init(client, force=True, milvus_collection_version="v2")
        self.assertEqual(code, 0, output)
        self.assertEqual(client.aliases, {"doghunt_memory_child": "doghunt_memory_child_v2",
                                          "doghunt_memory_parent": "doghunt_memory_parent_v2"})
        self.assertIn("doghunt_memory_child_v1", client.collections)  # 旧集合保留

    def test_node_not_match_retried_then_succeeds(self):
        client = MockMilvusClient()
        client.failures["create_collection"] = [NodeNotMatch(), NodeNotMatch()]
        client.failures["create_alias"] = [NodeNotMatch()]
        code, output = self.run_init(client)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.sleeps, [0.01, 0.01, 0.01])
        self.assertIn("node not match", output)
        self.assertEqual(len(client.aliases), 2)

    def test_node_not_match_exhausted(self):
        client = MockMilvusClient()
        client.failures["has_collection"] = [NodeNotMatch() for _ in range(10)]
        code, output = self.run_init(client)
        self.assertEqual(code, vector_init.EXIT_FAILED)
        self.assertEqual(len(self.sleeps), 3)
        self.assertEqual(len(client.names("has_collection")), 4)  # 1 次 + 3 次重试
        self.assertIn("约 1 分钟后过期", output)
        self.assertFalse(client.collections)

    def test_other_errors_fail_fast(self):
        client = MockMilvusClient()
        client.failures["create_collection"] = [RuntimeError("permission denied")]
        code, output = self.run_init(client)
        self.assertEqual(code, vector_init.EXIT_FAILED)
        self.assertEqual(self.sleeps, [])
        self.assertIn("permission denied", output)
        self.assertFalse(client.names("create_alias"))  # 子块失败就停，不再继续父块

    def test_inverted_unsupported_falls_back_to_autoindex(self):
        client = MockMilvusClient()
        client.reject_inverted = True
        code, output = self.run_init(client)
        self.assertEqual(code, 0, output)
        indexes = client.collections["doghunt_memory_child_v1"]["indexes"]
        self.assertEqual((indexes["parent_id"]["index_type"], indexes["event_id"]["index_type"]),
                         ("AUTOINDEX", "AUTOINDEX"))
        self.assertIn("实际类型 AUTOINDEX", output)

    def test_connect_failure_and_no_token_in_output(self):
        applog.register_secret(TOKEN)

        def refuse(*_args, **_kwargs):
            raise ConnectionError(f"Fail connecting to server on 127.0.0.1:1 with {TOKEN}")
        out, err = io.StringIO(), io.StringIO()
        with use_env(milvus_uri=f"http://root:{TOKEN}@127.0.0.1:1", milvus_token=TOKEN), \
                patch.object(vs, "new_client", side_effect=refuse):
            code = vector_init.run_init(out=out, err=err, retries=1, sleep=lambda _s: None)
        output = out.getvalue() + err.getvalue()
        self.assertEqual(code, vector_init.EXIT_FAILED)
        self.assertIn("连接 Milvus（http://127.0.0.1:1）失败", output)
        self.assertIn("make storage-up", output)
        self.assertNotIn(TOKEN, output)

    def test_owned_client_is_closed(self):
        client = MockMilvusClient()
        with use_env(), patch.object(vs, "new_client", return_value=client):
            self.assertEqual(vector_init.run_init(out=io.StringIO(), err=io.StringIO()), 0)
        self.assertTrue(client.closed)

    def test_cli_passes_force(self):
        from app.services.memory import __main__ as cli
        with patch.object(vector_init, "run_init", return_value=4) as run:
            self.assertEqual(cli.main(["init", "--force"]), 4)
        run.assert_called_once_with(force=True)


# ---------------------------------------------------------------- 健康接口与启动检查


class StorageHealthMilvusTests(unittest.TestCase):
    def request(self, store) -> dict:
        with use_env(vectorstore_provider="milvus"), patch("app.api.routes.health.get_vector_store", return_value=store), \
                TestClient(create_app()) as client:
            response = client.get("/api/v1/health/storage")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(TOKEN, response.text)
        return response.json()

    def store(self, client=None, **overrides) -> MilvusVectorStore:
        return MilvusVectorStore(settings(milvus_token=SecretStr(TOKEN), **overrides),
                                 client_factory=lambda **_: client or initialized_client())

    def test_ok(self):
        body = self.request(self.store())
        self.assertEqual(body["status"], "ok")
        milvus = body["milvus"]
        self.assertEqual((milvus["status"], milvus["provider"], milvus["uri"], milvus["dim"]),
                         ("ok", "milvus", "http://127.0.0.1:19530", DIM))
        self.assertEqual(milvus["collections"]["doghunt_memory_child"], "doghunt_memory_child_v1")
        self.assertIsInstance(milvus["latency_ms"], int)
        self.assertEqual(body["embedding"]["status"], "not_implemented")

    def test_down_is_200_degraded_with_redacted_error(self):
        applog.register_secret(TOKEN)

        def refuse(**_):
            raise ConnectionError(f"server unavailable token={TOKEN}")
        body = self.request(MilvusVectorStore(settings(milvus_token=SecretStr(TOKEN)), client_factory=refuse))
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["database"]["status"], "ok")
        self.assertEqual(body["milvus"]["status"], "down")
        self.assertIn("ConnectionError", body["milvus"]["error"])

    def test_not_initialized_counts_as_degraded(self):
        body = self.request(self.store(MockMilvusClient()))
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["milvus"]["status"], "not_initialized")
        self.assertEqual(body["milvus"]["collections"], {"doghunt_memory_child": None, "doghunt_memory_parent": None})

    def test_fake_is_reported_but_not_degraded(self):
        with TestClient(create_app()) as client:  # 隔离配置：provider=fake
            body = client.get("/api/v1/health/storage").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual((body["milvus"]["status"], body["milvus"]["provider"]), ("fake", "fake"))


class MilvusStartupTests(unittest.TestCase):
    def run_lifespan(self) -> float:
        async def go() -> float:
            started = time.perf_counter()
            async with lifespan(create_app()):
                return time.perf_counter() - started
        return asyncio.run(go())

    def test_disabled_or_fake_skips(self):
        with use_env(vectorstore_provider="milvus", milvus_startup_check="false"), \
                patch.object(MilvusVectorStore, "ensure_loaded") as milvus_ensure:
            self.run_lifespan()
        milvus_ensure.assert_not_called()
        with use_env(vectorstore_provider="fake", milvus_startup_check="true"), \
                patch.object(FakeVectorStore, "ensure_loaded") as fake_ensure:
            self.run_lifespan()
        fake_ensure.assert_not_called()

    def test_unreachable_milvus_only_warns_and_app_serves(self):
        """真实 pymilvus 连本机端口 1：连不上，启动不失败，/health 200，/health/storage degraded。"""
        with use_env(vectorstore_provider="milvus", milvus_startup_check="true", milvus_uri="http://127.0.0.1:1",
                     milvus_timeout_seconds="0.5"), \
                self.assertLogs("app.main", level=logging.WARNING) as logs, TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            storage = client.get("/api/v1/health/storage")
        self.assertEqual(storage.status_code, 200)
        self.assertEqual(storage.json()["status"], "degraded")
        self.assertEqual(storage.json()["milvus"]["status"], "down")
        self.assertIn("先不用语义召回启动", "\n".join(logs.output))

    def test_slow_milvus_does_not_block_startup(self):
        def slow(self, *, timeout=None):
            time.sleep(1.5)
        with use_env(vectorstore_provider="milvus", milvus_startup_check="true"), \
                patch.object(MilvusVectorStore, "ensure_loaded", slow), \
                self.assertLogs("app.main", level=logging.WARNING) as logs:
            elapsed = self.run_lifespan()
        self.assertLess(elapsed, 1.4)
        self.assertIn("秒内没有响应", "\n".join(logs.output))

    def test_ok_logs_info_and_shutdown_closes(self):
        client = initialized_client(loaded=False)
        with use_env(vectorstore_provider="milvus", milvus_startup_check="true", embedding_dim=str(DIM)), \
                patch.object(vs, "new_client", return_value=client), \
                self.assertLogs("app.main", level=logging.INFO) as logs:
            self.run_lifespan()
            self.assertFalse(vs.vector_store_created())
        self.assertTrue(client.closed)
        self.assertTrue(all(c["loaded"] for c in client.collections.values()))  # 启动检查 load 了一次
        self.assertIn("Milvus 可用", "\n".join(logs.output))

    def test_token_registered_for_redaction(self):
        with use_env(milvus_token="RegisteredMilvusTok9"), TestClient(create_app()):
            pass
        self.assertNotIn("RegisteredMilvusTok9", applog.redact("x RegisteredMilvusTok9 y"))


class ImportBoundaryTests(unittest.TestCase):
    def test_import_guard_keeps_concurrent_env_changes(self):
        """用一个假的 pymilvus 包：导入时调 load_dotenv()（应被跳过），同时模拟别的线程改环境变量（应保留）。"""
        folder = Path(tempfile.mkdtemp(prefix="doghunt-fake-pymilvus-"))
        (folder / "pymilvus").mkdir()
        (folder / "pymilvus" / "__init__.py").write_text(
            "import os\nfrom dotenv import load_dotenv\nload_dotenv()\n"
            "os.environ['DOGHUNT_OTHER_THREAD'] = 'kept'\nos.environ['DOGHUNT_CHANGED'] = 'new'\n", encoding="utf-8")
        code = ("import os, dotenv; "
                "dotenv.load_dotenv = lambda *a, **k: os.environ.setdefault('DOGHUNT_LEAK_PROBE', 'from-dotenv') or True; "
                "from app.integrations import vectorstore as vs; vs._pymilvus(); "
                "print(os.environ.get('DOGHUNT_LEAK_PROBE'), os.environ.get('DOGHUNT_OTHER_THREAD'), "
                "os.environ.get('DOGHUNT_CHANGED'), dotenv.load_dotenv.__name__)")
        env = {**os.environ, "PYTHONPATH": f"{folder}{os.pathsep}{BACKEND_ROOT}", "DOGHUNT_CHANGED": "old"}
        env.pop("DOGHUNT_LEAK_PROBE", None)
        result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_ROOT, env=env, capture_output=True,
                                text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip().splitlines()[-1], "None kept new <lambda>")  # load_dotenv 已还原

    def test_pymilvus_import_does_not_leak_dotenv_into_environ(self):
        """pymilvus 导入时会 load_dotenv()；模拟它从 .env 读到一个变量，导入后环境里不能留下。"""
        code = ("import os, dotenv; "
                "dotenv.load_dotenv = lambda *a, **k: os.environ.setdefault('DOGHUNT_LEAK_PROBE', 'from-dotenv') or True; "
                "from app.integrations import vectorstore as vs; vs._pymilvus(); "
                "print(os.environ.get('DOGHUNT_LEAK_PROBE'))")
        env = {k: v for k, v in os.environ.items() if k != "DOGHUNT_LEAK_PROBE"}
        result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_ROOT, env=env, capture_output=True,
                                text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip().splitlines()[-1], "None")

    def test_only_vectorstore_imports_pymilvus(self):
        pattern = re.compile(r"^\s*(?:import\s+pymilvus|from\s+pymilvus\b)", re.MULTILINE)
        offenders = sorted(
            str(path.relative_to(BACKEND_ROOT))
            for path in (BACKEND_ROOT / "app").rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(offenders, [str(Path("app/integrations/vectorstore.py"))])

    def test_app_import_and_fake_health_do_not_load_pymilvus(self):
        code = ("import sys; from fastapi.testclient import TestClient; from app.main import app; "
                "c = TestClient(app); c.__enter__(); c.get('/api/v1/health/storage'); c.__exit__(None, None, None); "
                "print('pymilvus' in sys.modules)")
        env = {**os.environ, "DOGHUNT_VECTORSTORE_PROVIDER": "fake", "DOGHUNT_MILVUS_STARTUP_CHECK": "false"}
        result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_ROOT, env=env, capture_output=True,
                                text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip().splitlines()[-1], "False")


if __name__ == "__main__":
    unittest.main()
