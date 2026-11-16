"""Milvus 集合与别名初始化（开发文档第 8.4 节）：`python -m app.services.memory init [--force]`。

每个集合（子块、父块）依次：
1. 集合 `<别名>_<版本>` 不存在就按第 8.2 节建（主键 id，auto_id=False）；
2. 补齐缺少的索引：vector 用 HNSW(M=16, efConstruction=200, COSINE)，子块的 parent_id / event_id 用 INVERTED，
   服务端不支持 INVERTED 时改 AUTOINDEX 并打印实际类型；
3. 检查向量维度与 DOGHUNT_EMBEDDING_DIM 一致，不一致打印 "collection dim=X, config dim=Y" 并失败，不自动重建；
4. 未 load 就 load；
5. 别名不存在就创建；已指向本集合不动；指向别的集合时不动并失败，加 --force 才 alter_alias 切过去。
每一步都先查再做，重复执行结果一致。Milvus 刚重启时 etcd 里旧节点登记未过期，会报 `node not match`，
这类错误按 retries × retry_interval 有限重试；其他错误直接报告。输出不含 token（地址去掉账号密码）。

退出码：0 成功；1 Milvus 不可用或操作失败；3 维度或字段与配置不一致；4 别名已指向别的集合（未加 --force）。
两个集合依次处理：遇到 1 立即停止并返回 1（后面的集合不再处理）；3、4 只记下这个集合的结果，另一个集合照常
检查，最后返回两者中较大的退出码（例如子块 3、父块 4 时返回 4；一个 0、一个 3 时返回 3）。
"""

import sys
import time
from collections.abc import Callable
from typing import Any, Literal, TextIO, TypeVar

from app.core.config import Settings, get_settings
from app.integrations import vectorstore as vs

EXIT_OK, EXIT_FAILED, EXIT_MISMATCH, EXIT_ALIAS_CONFLICT = 0, 1, 3, 4
DEFAULT_RETRIES = 6
DEFAULT_RETRY_INTERVAL_SECONDS = 10.0
MIN_INIT_TIMEOUT_SECONDS = 30.0  # 建集合、建索引、load 比普通查询慢，不用 2 秒的检索超时
TRANSIENT_MARKERS = ("node not match",)

T = TypeVar("T")


class _Abort(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def is_transient(exc: BaseException) -> bool:
    text = f"{getattr(exc, 'message', '')} {exc}".lower()
    return any(marker in text for marker in TRANSIENT_MARKERS)


class _Runner:
    def __init__(self, settings: Settings, client: Any, *, force: bool, retries: int, retry_interval: float,
                 sleep: Callable[[float], None], out: TextIO, err: TextIO) -> None:
        self.settings, self.client, self.force = settings, client, force
        self.retries, self.retry_interval, self.sleep = retries, retry_interval, sleep
        self.out, self.err = out, err
        # int：pymilvus 2.5 的内部重试只有 int 超时才按时间停止（见 vectorstore.rpc_timeout）。
        self.timeout = vs.rpc_timeout(max(settings.milvus_timeout_seconds, MIN_INIT_TIMEOUT_SECONDS))

    def say(self, message: str) -> None:
        print(message, file=self.out)

    def fail(self, message: str, code: int) -> _Abort:
        print(message, file=self.err)
        return _Abort(code)

    def retry(self, what: str, fn: Callable[[], T]) -> T:
        """执行一次 Milvus 操作；`node not match` 有限重试，用完或遇到其他错误把原异常抛给调用方。"""
        for attempt in range(self.retries + 1):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                if not is_transient(exc) or attempt == self.retries:
                    raise
                self.say(f"  {what}：Milvus 节点还没就绪（node not match），{self.retry_interval:g} 秒后重试"
                         f"（{attempt + 1}/{self.retries}）")
                self.sleep(self.retry_interval)
        raise AssertionError("unreachable")  # pragma: no cover

    def call(self, what: str, fn: Callable[[], T]) -> T:
        """同 retry，失败时打印原因并以退出码 1 结束。"""
        try:
            return self.retry(what, fn)
        except Exception as exc:  # noqa: BLE001
            hint = (f"重试 {self.retries} 次后仍是节点未就绪。Milvus 刚重启时 etcd 里的旧节点登记约 1 分钟后过期，"
                    "稍后再执行。" if is_transient(exc) else "")
            raise self.fail(f"{what}失败：{vs.short_error(exc)}。{hint}", EXIT_FAILED) from exc

    def ensure(self, kind: Literal["child", "parent"], alias: str) -> None:
        c, t, dim = self.client, self.timeout, self.settings.embedding_dim
        name = vs.collection_name(alias, self.settings.milvus_collection_version)
        self.say(f"[{kind}] 集合 {name}，别名 {alias}")

        if self.call("检查集合", lambda: c.has_collection(name, timeout=t)):
            self.say("  集合已存在")
        else:
            schema = vs.build_schema(kind, dim)
            self.call("建集合", lambda: c.create_collection(name, schema=schema, timeout=t))
            self.say(f"  已建集合（dim={dim}，主键 id VARCHAR(36)，auto_id=False）")

        description = self.call("读取集合结构", lambda: c.describe_collection(name, timeout=t))
        actual_dim = vs.vector_dim(description)
        if actual_dim != dim:
            raise self.fail(f"  维度不一致：collection dim={actual_dim}, config dim={dim}。"
                            "不会自动重建；换模型请新建版本集合（改 DOGHUNT_MILVUS_COLLECTION_VERSION）后再切别名。",
                            EXIT_MISMATCH)
        expected = {"id", "vector", *(f[0] for f in (vs.CHILD_FIELDS if kind == "child" else vs.PARENT_FIELDS))}
        actual = {item.get("name") for item in description.get("fields", [])}
        if actual != expected:
            raise self.fail(f"  字段不一致：缺少 {sorted(expected - actual)}，多出 {sorted(actual - expected)}；"
                            "改字段要建新版本集合。", EXIT_MISMATCH)

        self.ensure_indexes(name, kind)

        state = vs.load_state_name(self.call("读取加载状态", lambda: c.get_load_state(name, timeout=t)))
        if state == "Loaded":
            self.say("  已加载")
        else:
            self.call("加载集合", lambda: c.load_collection(name, timeout=t))
            self.say(f"  已加载（原状态 {state}）")

        self.ensure_alias(name, alias)

    def ensure_indexes(self, name: str, kind: Literal["child", "parent"]) -> None:
        c, t = self.client, self.timeout
        indexed = {
            self.call("读取索引", lambda i=index: c.describe_index(name, i, timeout=t)).get("field_name", index)
            for index in self.call("列出索引", lambda: c.list_indexes(name))
        }
        if "vector" in indexed:
            self.say("  向量索引已存在")
        else:
            params = vs.build_index_params(c, "vector", "HNSW", metric_type=vs.METRIC_TYPE, params=vs.HNSW_PARAMS)
            self.call("建向量索引", lambda: c.create_index(name, params, timeout=t))
            self.say("  已建向量索引 HNSW（M=16, efConstruction=200, COSINE）")
        for field_name in vs.SCALAR_INDEX_FIELDS[kind]:
            if field_name in indexed:
                self.say(f"  {field_name} 标量索引已存在")
                continue
            try:
                self.retry(f"建 {field_name} 索引", lambda f=field_name: c.create_index(
                    name, vs.build_index_params(c, f, "INVERTED"), timeout=t))
                actual = "INVERTED"
            except Exception as exc:  # noqa: BLE001  不支持 INVERTED 时退回 AUTOINDEX（第 8.3 节）
                if is_transient(exc):
                    raise self.fail(f"建 {field_name} 索引失败：{vs.short_error(exc)}", EXIT_FAILED) from exc
                self.say(f"  {field_name} 不能建 INVERTED（{vs.short_error(exc)}），改用 AUTOINDEX")
                self.call(f"建 {field_name} 索引", lambda f=field_name: c.create_index(
                    name, vs.build_index_params(c, f, "AUTOINDEX"), timeout=t))
                actual = "AUTOINDEX"
            self.say(f"  已建 {field_name} 标量索引，实际类型 {actual}")

    def ensure_alias(self, name: str, alias: str) -> None:
        c, t = self.client, self.timeout
        existing = self.call("列出别名", lambda: c.list_aliases(timeout=t)) or {}
        if alias not in existing.get("aliases", []):
            self.call("建别名", lambda: c.create_alias(name, alias, timeout=t))
            self.say(f"  已建别名 {alias} → {name}")
            return
        target = self.call("读取别名", lambda: c.describe_alias(alias, timeout=t))["collection_name"]
        if target == name:
            self.say(f"  别名 {alias} → {name}，未变")
        elif self.force:
            self.call("切换别名", lambda: c.alter_alias(name, alias, timeout=t))
            self.say(f"  已切换别名（--force）：{alias}：{target} → {name}")
        else:
            raise self.fail(f"  别名 {alias} 已指向 {target}，不是 {name}；没有改动。确认要切换时加 --force。",
                            EXIT_ALIAS_CONFLICT)


def run_init(force: bool = False, *, client: Any = None, retries: int = DEFAULT_RETRIES,
             retry_interval: float = DEFAULT_RETRY_INTERVAL_SECONDS, sleep: Callable[[float], None] = time.sleep,
             out: TextIO | None = None, err: TextIO | None = None) -> int:
    """初始化子块、父块两个集合与别名，返回退出码。force=True 时把已指向别处的别名切到当前版本集合。

    总是针对 Milvus（不看 DOGHUNT_VECTORSTORE_PROVIDER）。client 只给测试注入；默认按配置连接，
    连接也对 node not match 有限重试。
    """
    settings = get_settings()
    out, err = out or sys.stdout, err or sys.stderr
    uri = vs.safe_uri(settings.milvus_uri)
    print(f"初始化 Milvus：{uri}（db={settings.milvus_db_name}，版本 {settings.milvus_collection_version}，"
          f"dim={settings.embedding_dim}）", file=out)
    owns_client = client is None
    runner = _Runner(settings, client, force=force, retries=retries, retry_interval=retry_interval,
                     sleep=sleep, out=out, err=err)
    codes: list[int] = []
    try:
        if owns_client:
            runner.client = runner.call(f"连接 Milvus（{uri}）", lambda: vs.new_client(settings))
        for kind, alias in (("child", settings.milvus_child_alias), ("parent", settings.milvus_parent_alias)):
            try:
                runner.ensure(kind, alias)
                codes.append(EXIT_OK)
            except _Abort as abort:
                if abort.code == EXIT_FAILED:
                    raise
                codes.append(abort.code)  # 维度或别名问题只影响这一个集合，另一个照常检查
    except _Abort as abort:
        print("先确认 make storage-up 已执行、Milvus 健康（make storage-check）。", file=err)
        return abort.code
    finally:
        if owns_client and runner.client is not None:
            try:
                runner.client.close()
            except Exception:  # noqa: BLE001
                pass
    code = max(codes, default=EXIT_OK)
    print("初始化完成（可以重复执行，结果不变）。" if code == EXIT_OK else f"初始化未全部完成，退出码 {code}。",
          file=out if code == EXIT_OK else err)
    return code
