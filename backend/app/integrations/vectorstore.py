"""向量库封装：业务代码只通过这里访问向量库，不直接 import pymilvus（开发文档第 8 节）。

- `get_vector_store()` 按 `DOGHUNT_VECTORSTORE_PROVIDER` 返回 Milvus 实现或内存假实现（离线测试强制 fake）。
- pymilvus 只在第一次真正连接或建集合时懒导入；没装、导入慢都不影响应用启动。
- 只用别名访问集合，不硬编码版本号；集合名 `<别名>_<版本>` 只在初始化（vector_init.py）里用。
- Milvus 只放 Postgres 里 `status=active` 的子块，不存状态（D13）；正文也不进 Milvus。
- 检索：COSINE、HNSW ef=64、一致性 Bounded；过滤只拼经过校验的值，防注入（第 8.3 节）。
- Milvus 的任何异常都包成 `VectorStoreError`（已脱敏），调用方据此降级；入参不合法抛 `ValueError`。
"""

from __future__ import annotations

import math
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from app.core import applog
from app.core.config import Settings, get_settings

NEVER_EXPIRES = 4102444800  # 2100-01-01T00:00:00Z，长期有效的子块写这个值，不留空
METRIC_TYPE = "COSINE"
HNSW_PARAMS = {"M": 16, "efConstruction": 200}
SEARCH_EF = 64
SEARCH_CONSISTENCY = "Bounded"  # 召回不要求立刻读到新写入；“忘记立即生效”靠回查 Postgres
LIST_CONSISTENCY = "Strong"  # 对账要准，用强一致
MAX_SEARCH_LIMIT = 100
MAX_FILTER_IDS = 100
LIST_BATCH_SIZE = 1000
DELETE_BATCH_SIZE = 100
CONNECT_COOLDOWN_SECONDS = 5.0  # 连不上后这段时间内直接报 down，不再让每个请求都等一次连接超时

# 字段定义（第 8.2 节）：(字段名, 类型, VARCHAR 的 max_length)。Milvus 不接受不定长 VARCHAR。
ID_MAX_LENGTH = 36
VARCHAR, INT64 = "VARCHAR", "INT64"
CHILD_FIELDS: tuple[tuple[str, str, int | None], ...] = (
    ("parent_id", VARCHAR, 64),
    ("kind", VARCHAR, 64),
    ("scope_kind", VARCHAR, 64),
    ("event_id", VARCHAR, 64),
    ("expires_at", INT64, None),
    ("updated_at", INT64, None),
    ("content_hash", VARCHAR, 64),
    ("embed_model", VARCHAR, 100),
)
PARENT_FIELDS: tuple[tuple[str, str, int | None], ...] = (
    ("key", VARCHAR, 64),
    ("content_hash", VARCHAR, 64),
    ("embed_model", VARCHAR, 100),
    ("updated_at", INT64, None),
)
SCALAR_INDEX_FIELDS: dict[str, tuple[str, ...]] = {"child": ("parent_id", "event_id"), "parent": ()}

# 拼进过滤表达式的字符串只允许这些字符（UUID、比赛 id 都满足）；引号、反斜杠、空格一律拒绝。
# 主键也用同一白名单（最长 36）：删除按 id 拼表达式，pymilvus 自己拼的 ids 表达式不转义单引号。
_SAFE_CHARS = r"[A-Za-z0-9_.:\-]"
_SAFE_VALUE = re.compile(rf"{_SAFE_CHARS}{{1,64}}")
_SAFE_ID = re.compile(rf"{_SAFE_CHARS}{{1,{ID_MAX_LENGTH}}}")

HealthState = Literal["ok", "down", "not_initialized", "fake"]


class VectorStoreError(RuntimeError):
    """向量库不可用或操作失败；消息已脱敏，可以直接记日志或放进健康接口。"""


# ---------------------------------------------------------------- 数据与校验


@dataclass(frozen=True)
class ChildVector:
    """一条子块向量。event_id 为 None 或空串表示全局记忆；expires_at 为 None 表示长期有效。"""

    id: str
    vector: Sequence[float]
    parent_id: str
    kind: str
    scope_kind: str
    event_id: str | None = None
    expires_at: int | None = None
    updated_at: int = 0
    content_hash: str = ""
    embed_model: str = ""

    def to_row(self, dim: int) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": _check_id(self.id),
            "vector": _check_vector(self.vector, dim),
            "parent_id": _check_safe(self.parent_id, "parent_id"),
            "kind": self.kind,
            "scope_kind": self.scope_kind,
            "event_id": "" if not self.event_id else _check_safe(self.event_id, "event_id"),
            "expires_at": NEVER_EXPIRES if self.expires_at is None else self.expires_at,
            "updated_at": self.updated_at,
            "content_hash": self.content_hash,
            "embed_model": self.embed_model,
        }
        _check_fields(row, CHILD_FIELDS)
        return row


@dataclass(frozen=True)
class ParentVector:
    id: str
    vector: Sequence[float]
    key: str
    content_hash: str = ""
    embed_model: str = ""
    updated_at: int = 0

    def to_row(self, dim: int) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": _check_id(self.id),
            "vector": _check_vector(self.vector, dim),
            "key": self.key,
            "content_hash": self.content_hash,
            "embed_model": self.embed_model,
            "updated_at": self.updated_at,
        }
        _check_fields(row, PARENT_FIELDS)
        return row


def _check_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"id 只能包含字母、数字和 _ . : -，长度 1–{ID_MAX_LENGTH}")
    return value


def _check_safe(value: object, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_VALUE.fullmatch(value):
        raise ValueError(f"{name} 只能包含字母、数字和 _ . : -，长度 1–64")
    return value


def _check_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数（epoch 秒）")
    return value


def _check_vector(vector: Sequence[float], dim: int) -> list[float]:
    values = [float(v) for v in vector]
    if len(values) != dim:
        raise ValueError(f"向量维度 {len(values)} 与配置 {dim} 不一致")
    if not all(math.isfinite(v) for v in values):
        raise ValueError("向量里有 NaN 或无穷大")
    return values


def _check_fields(row: dict[str, Any], spec: Iterable[tuple[str, str, int | None]]) -> None:
    for name, kind, max_length in spec:
        value = row[name]
        if kind == INT64:
            _check_int(value, name)
        elif not isinstance(value, str) or len(value) > (max_length or 0):
            raise ValueError(f"{name} 必须是不超过 {max_length} 个字符的字符串")


def _check_ids(ids: Iterable[str]) -> list[str]:
    return [_check_id(i) for i in dict.fromkeys(ids)]  # 去重，保持顺序


def _check_search(vector: Sequence[float], limit: int, dim: int) -> list[float]:
    _check_int(limit, "limit")
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"limit 必须在 1–{MAX_SEARCH_LIMIT} 之间")
    return _check_vector(vector, dim)


def quote_value(value: str) -> str:
    """校验后加双引号。校验已拒绝引号和反斜杠，这里仍做转义，防止以后放宽字符集时漏掉。"""
    _check_safe(value, "过滤值")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_id_filter(ids: Sequence[str]) -> str:
    """按主键删除用的表达式 `id in ["a", "b"]`；每个 id 先过 _check_id 白名单。"""
    checked = [_check_id(i) for i in ids]
    if not checked:
        raise ValueError("ids 不能为空")
    return f"id in [{', '.join(quote_value(i) for i in checked)}]"


def build_child_filter(now_epoch: int, event_id: str | None = None, parent_ids: Sequence[str] | None = None) -> str:
    """子块检索的过滤表达式（第 8.3 节）。

    - 总是加 `expires_at > now`；
    - event_id 为 None 不按比赛过滤；给了比赛 id 时只取该场或全局（空串）；传空串表示只取全局；
    - parent_ids 为 None 不按父块过滤；给了列表时只在这些父块里搜（调用方先处理空列表）。
    """
    parts = [f"expires_at > {_check_int(now_epoch, 'now_epoch')}"]
    if event_id is not None:
        parts.append('event_id == ""' if event_id == "" else f'(event_id == {quote_value(event_id)} or event_id == "")')
    if parent_ids is not None:
        unique = list(dict.fromkeys(parent_ids))
        if not unique or len(unique) > MAX_FILTER_IDS:
            raise ValueError(f"parent_ids 需要 1–{MAX_FILTER_IDS} 个")
        parts.append(f"parent_id in [{', '.join(quote_value(p) for p in unique)}]")
    return " and ".join(parts)


# ---------------------------------------------------------------- 健康状态


@dataclass(frozen=True)
class VectorStoreStatus:
    """健康检查结果。collections 是 别名 → 实际集合名（别名不存在时为 None）。"""

    status: HealthState
    provider: str
    uri: str
    latency_ms: int
    collections: dict[str, str | None] = field(default_factory=dict)
    dim: int | None = None
    error: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "fake")


def safe_uri(uri: str) -> str:
    """去掉账号、密码和查询参数的地址，只给日志和健康接口显示。"""
    try:
        parts = urlsplit(uri)
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        return "（地址格式无效）"
    if not parts.scheme or not host:
        return "（地址格式无效）"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def short_error(exc: BaseException) -> str:
    """异常只留类名和第一行并脱敏；pymilvus 异常优先用它的 message 字段。"""
    message = getattr(exc, "message", None)
    text = message if isinstance(message, str) and message.strip() else str(exc)
    first_line = (text.strip().splitlines() or [""])[0] or "没有详细信息（多为连不上或超时）"
    return applog.redact(f"{type(exc).__name__}: {first_line}")[:300]


def register_vectorstore_secrets(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    applog.register_secret(settings.milvus_token.get_secret_value())
    try:
        password = urlsplit(settings.milvus_uri).password
    except ValueError:
        applog.register_secret(settings.milvus_uri)  # 解析不了就把整条地址当秘密，避免其中的密码原样进日志
        return
    applog.register_secret(password)


def rpc_timeout(seconds: float) -> int:
    """传给 pymilvus、且该调用不能关掉内部重试时用的超时：向上取整成 int（至少 1 秒）。

    pymilvus 2.5.18 的重试装饰器（decorators.retry_on_rpc_failure）只有 timeout 是 int 时才按时间停止重试；
    float 时按次数重试 75 次（退避最长 3 秒），服务断开后一次调用可能卡 3 分钟以上。
    """
    return max(1, math.ceil(seconds))


def single_attempt(seconds: float) -> dict[str, Any]:
    """普通调用的超时参数：float 超时 + retry_times=0，只发一次请求，总时长不超过 gRPC 截止时间。"""
    return {"timeout": float(seconds), "retry_times": 0}


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 0.0 if norm == 0 else sum(x * y for x, y in zip(a, b, strict=True)) / norm


# ---------------------------------------------------------------- 协议


class VectorStore(Protocol):
    provider: str

    def upsert_children(self, rows: Sequence[ChildVector], *, timeout: float | None = None) -> int: ...
    def delete_children(self, ids: Iterable[str], *, timeout: float | None = None) -> None: ...
    def search_children(self, vector: Sequence[float], limit: int, *, now_epoch: int, event_id: str | None = None,
                        parent_ids: Sequence[str] | None = None,
                        timeout: float | None = None) -> list[tuple[str, float]]: ...
    def list_child_ids(self, *, timeout: float | None = None) -> set[str]: ...
    def upsert_parents(self, rows: Sequence[ParentVector], *, timeout: float | None = None) -> int: ...
    def delete_parents(self, ids: Iterable[str], *, timeout: float | None = None) -> None: ...
    def search_parents(self, vector: Sequence[float], limit: int, *,
                       timeout: float | None = None) -> list[tuple[str, float]]: ...
    def list_parent_ids(self, *, timeout: float | None = None) -> set[str]: ...
    def check(self, *, timeout: float | None = None) -> VectorStoreStatus: ...
    def ensure_loaded(self, *, timeout: float | None = None) -> VectorStoreStatus: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------- 假实现


class FakeVectorStore:
    """内存假实现：COSINE、过滤语义与 Milvus 实现一致（同一套校验），只用于离线测试和本机无 Milvus 时。"""

    provider = "fake"

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.dim = settings.embedding_dim
        self.child_alias = settings.milvus_child_alias
        self.parent_alias = settings.milvus_parent_alias
        self.children: dict[str, dict[str, Any]] = {}
        self.parents: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def upsert_children(self, rows: Sequence[ChildVector], *, timeout: float | None = None) -> int:
        prepared = [row.to_row(self.dim) for row in rows]  # 先全部校验，避免写一半
        with self._lock:
            self.children.update((row["id"], row) for row in prepared)
        return len(prepared)

    def delete_children(self, ids: Iterable[str], *, timeout: float | None = None) -> None:
        with self._lock:
            for child_id in _check_ids(ids):
                self.children.pop(child_id, None)

    def search_children(self, vector: Sequence[float], limit: int, *, now_epoch: int, event_id: str | None = None,
                        parent_ids: Sequence[str] | None = None,
                        timeout: float | None = None) -> list[tuple[str, float]]:
        query = _check_search(vector, limit, self.dim)
        if parent_ids is not None and not parent_ids:
            return []
        build_child_filter(now_epoch, event_id, parent_ids)  # 与 Milvus 实现同样校验，非法值同样抛 ValueError
        allowed_parents = set(parent_ids) if parent_ids is not None else None
        with self._lock:
            rows = list(self.children.values())
        hits = [
            (row["id"], _cosine(query, row["vector"]))
            for row in rows
            if row["expires_at"] > now_epoch
            and (event_id is None or row["event_id"] in ({event_id, ""} if event_id else {""}))
            and (allowed_parents is None or row["parent_id"] in allowed_parents)
        ]
        return sorted(hits, key=lambda hit: (-hit[1], hit[0]))[:limit]

    def list_child_ids(self, *, timeout: float | None = None) -> set[str]:
        with self._lock:
            return set(self.children)

    def upsert_parents(self, rows: Sequence[ParentVector], *, timeout: float | None = None) -> int:
        prepared = [row.to_row(self.dim) for row in rows]
        with self._lock:
            self.parents.update((row["id"], row) for row in prepared)
        return len(prepared)

    def delete_parents(self, ids: Iterable[str], *, timeout: float | None = None) -> None:
        with self._lock:
            for parent_id in _check_ids(ids):
                self.parents.pop(parent_id, None)

    def search_parents(self, vector: Sequence[float], limit: int, *,
                       timeout: float | None = None) -> list[tuple[str, float]]:
        query = _check_search(vector, limit, self.dim)
        with self._lock:
            rows = list(self.parents.values())
        hits = [(row["id"], _cosine(query, row["vector"])) for row in rows]
        return sorted(hits, key=lambda hit: (-hit[1], hit[0]))[:limit]

    def list_parent_ids(self, *, timeout: float | None = None) -> set[str]:
        with self._lock:
            return set(self.parents)

    def check(self, *, timeout: float | None = None) -> VectorStoreStatus:
        return VectorStoreStatus(
            status="fake", provider=self.provider, uri="fake://memory", latency_ms=0,
            collections={self.child_alias: "fake", self.parent_alias: "fake"}, dim=self.dim,
            detail="内存假实现（DOGHUNT_VECTORSTORE_PROVIDER=fake），不连 Milvus，重启后数据清空",
        )

    def ensure_loaded(self, *, timeout: float | None = None) -> VectorStoreStatus:
        return self.check(timeout=timeout)

    def close(self) -> None:
        with self._lock:
            self.children.clear()
            self.parents.clear()


# ---------------------------------------------------------------- pymilvus（只在这里导入）


_import_lock = threading.Lock()


def _pymilvus():
    """懒导入 pymilvus：应用启动和离线测试不需要它；没装时给出清楚的 VectorStoreError。

    pymilvus 2.5 在 import 时执行无参数的 load_dotenv()（pymilvus/settings.py），会从自己的安装目录往上
    找到 backend/.env，把数据库密码、模型 Key 等全部写进 os.environ（子进程会继承，离线测试的隔离也会被破坏）。
    这里在导入期间把 dotenv.load_dotenv 换成什么也不做的函数，让它根本不写环境变量；
    不做“导入前后快照再还原”，那样会把导入期间其他线程对环境变量的修改一起抹掉。
    """
    with _import_lock:
        if "pymilvus" in sys.modules:
            return sys.modules["pymilvus"]
        try:
            import dotenv
        except ImportError:  # pragma: no cover - pymilvus 依赖 python-dotenv
            dotenv = None
        original = dotenv.load_dotenv if dotenv else None
        if dotenv:
            dotenv.load_dotenv = _skip_load_dotenv
        try:
            import pymilvus
        except ImportError as exc:  # pragma: no cover - 依赖已锁在 uv.lock
            raise VectorStoreError("没有安装 pymilvus：在 backend/ 下执行 uv sync --locked") from exc
        finally:
            if dotenv:
                dotenv.load_dotenv = original
    return pymilvus


def _skip_load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
    return False


def new_client(settings: Settings | None = None, *, timeout: float | None = None) -> Any:
    """建立一个 MilvusClient（构造时就会连接，连不上在 timeout 秒后抛异常）。

    每个客户端用独立的连接名：pymilvus 默认按 uri+db 共用一条全局连接，任何一个客户端 close()
    都会把别人的连接一起断掉（初始化命令、测试和应用里的实例会互相影响）；独立名字也避免 pymilvus
    在连接失败日志里打印完整地址。
    """
    settings = settings or get_settings()
    register_vectorstore_secrets(settings)
    pymilvus = _pymilvus()
    connection_name = f"doghunt-{uuid.uuid4().hex}"
    try:
        return pymilvus.MilvusClient(
            uri=settings.milvus_uri,
            token=settings.milvus_token.get_secret_value(),
            db_name=settings.milvus_db_name,
            timeout=settings.milvus_timeout_seconds if timeout is None else timeout,
            alias=connection_name,
        )
    except Exception:
        try:  # 连接失败时清掉登记，Milvus 长时间不可用时不会越积越多
            pymilvus.connections.remove_connection(connection_name)
        except Exception:  # noqa: BLE001
            pass
        raise


def collection_name(alias: str, version: str) -> str:
    """版本化集合名，例如 doghunt_memory_child_v1；只在初始化和集成测试里用。"""
    return f"{alias}_{version}"


def build_schema(kind: Literal["child", "parent"], dim: int) -> Any:
    """按第 8.2 节建字段：主键 id（VARCHAR 36，auto_id=False）、vector（FLOAT_VECTOR）和标量字段。"""
    pymilvus = _pymilvus()
    data_type = pymilvus.DataType
    schema = pymilvus.MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", data_type.VARCHAR, is_primary=True, max_length=ID_MAX_LENGTH)
    schema.add_field("vector", data_type.FLOAT_VECTOR, dim=dim)
    for name, field_type, max_length in CHILD_FIELDS if kind == "child" else PARENT_FIELDS:
        if field_type == VARCHAR:
            schema.add_field(name, data_type.VARCHAR, max_length=max_length)
        else:
            schema.add_field(name, data_type.INT64)
    return schema


def build_index_params(client: Any, field_name: str, index_type: str, *, metric_type: str | None = None,
                       params: dict[str, Any] | None = None) -> Any:
    index_params = client.prepare_index_params()
    extra: dict[str, Any] = {"metric_type": metric_type} if metric_type else {}
    index_params.add_index(field_name=field_name, index_type=index_type, params=params or {}, **extra)
    return index_params


def vector_dim(description: dict[str, Any]) -> int | None:
    """从 describe_collection 的结果里取 vector 字段的维度。"""
    for item in description.get("fields", []):
        if item.get("name") == "vector":
            dim = (item.get("params") or {}).get("dim")
            return int(dim) if dim is not None else None
    return None


def load_state_name(state: Any) -> str:
    """get_load_state 返回 {"state": LoadState.Loaded}；统一成 "Loaded" / "NotLoad" / "NotExist" 这样的名字。"""
    value = state.get("state") if isinstance(state, dict) else state
    return getattr(value, "name", None) or str(value)


# ---------------------------------------------------------------- Milvus 实现


class MilvusVectorStore:
    """Milvus 实现：一个进程一个连接，第一次使用时才连。

    超时：每次调用最长等调用方给的 timeout（默认 DOGHUNT_MILVUS_TIMEOUT_SECONDS），pymilvus 内部重试关掉
    （见 single_attempt / rpc_timeout）。建连接时持锁，但等锁也只等 timeout；连不上后 CONNECT_COOLDOWN_SECONDS
    内直接报 down，之后的调用再重连。服务断开再恢复时，已建的 gRPC 通道会自己重连，不用重建客户端。
    """

    provider = "milvus"

    def __init__(self, settings: Settings | None = None, *, client_factory: Callable[..., Any] | None = None,
                 connect_cooldown: float = CONNECT_COOLDOWN_SECONDS) -> None:
        self._settings = settings or get_settings()
        register_vectorstore_secrets(self._settings)
        self._factory = client_factory or (lambda *, timeout: new_client(self._settings, timeout=timeout))
        self._client: Any = None
        self._closed = False
        self._lock = threading.Lock()
        self._cooldown = connect_cooldown
        self._failed_at: float | None = None
        self._failed_error = ""
        self.dim = self._settings.embedding_dim
        self.child_alias = self._settings.milvus_child_alias
        self.parent_alias = self._settings.milvus_parent_alias
        self.uri = safe_uri(self._settings.milvus_uri)

    def _timeout(self, timeout: float | None) -> float:
        return self._settings.milvus_timeout_seconds if timeout is None else timeout

    def _get_client(self, timeout: float) -> Any:
        """持锁建连接：启动检查线程和第一个请求可能同时进来，只建一个，否则多出来的没人关闭。

        别的线程正在建连接时最多等 timeout；刚连失败过就在冷却期内直接报错，不重复等连接超时。
        """
        client = self._client
        if client is not None and not self._closed:
            return client
        if not self._lock.acquire(timeout=timeout):
            raise VectorStoreError(f"等待 Milvus 建连接超过 {timeout:g} 秒")
        try:
            if self._closed:
                raise VectorStoreError("向量库连接已关闭")
            if self._client is None:
                if self._failed_at is not None and time.monotonic() - self._failed_at < self._cooldown:
                    raise VectorStoreError(f"Milvus 刚刚连不上（{self._cooldown:g} 秒内不重试）：{self._failed_error}")
                try:
                    self._client = self._factory(timeout=timeout)
                except Exception as exc:  # noqa: BLE001
                    self._failed_at, self._failed_error = time.monotonic(), short_error(exc)
                    raise
                self._failed_at = None
            return self._client
        finally:
            self._lock.release()

    def _call(self, operation: str, timeout: float | None, fn: Callable[[Any, float], Any]) -> Any:
        t = self._timeout(timeout)
        try:
            return fn(self._get_client(t), t)
        except VectorStoreError:
            raise
        except Exception as exc:  # noqa: BLE001  任何 Milvus/网络异常都统一成可降级的错误
            raise VectorStoreError(f"Milvus {operation} 失败：{short_error(exc)}") from exc

    # ---- 写入与删除

    def _upsert(self, alias: str, rows: list[dict[str, Any]], timeout: float | None) -> int:
        if rows:
            # upsert 在 pymilvus 内部还会读一次集合结构，那一步不接收 retry_times，只能用 int 超时限住重试时间。
            self._call("upsert", timeout, lambda c, t: c.upsert(alias, rows, timeout=rpc_timeout(t)))
        return len(rows)

    def _delete(self, alias: str, ids: Iterable[str], timeout: float | None) -> None:
        # 不用 delete(ids=...)：pymilvus 用单引号直接拼 ids，不转义。这里自己按白名单拼表达式，也省掉一次读结构。
        checked = _check_ids(ids)
        for start in range(0, len(checked), DELETE_BATCH_SIZE):
            expr = build_id_filter(checked[start:start + DELETE_BATCH_SIZE])
            self._call("delete", timeout, lambda c, t, e=expr: c.delete(alias, filter=e, **single_attempt(t)))

    def upsert_children(self, rows: Sequence[ChildVector], *, timeout: float | None = None) -> int:
        return self._upsert(self.child_alias, [row.to_row(self.dim) for row in rows], timeout)

    def delete_children(self, ids: Iterable[str], *, timeout: float | None = None) -> None:
        self._delete(self.child_alias, ids, timeout)

    def upsert_parents(self, rows: Sequence[ParentVector], *, timeout: float | None = None) -> int:
        return self._upsert(self.parent_alias, [row.to_row(self.dim) for row in rows], timeout)

    def delete_parents(self, ids: Iterable[str], *, timeout: float | None = None) -> None:
        self._delete(self.parent_alias, ids, timeout)

    # ---- 检索与列出

    def _search(self, alias: str, query: list[float], limit: int, expr: str,
                timeout: float | None) -> list[tuple[str, float]]:
        result = self._call("search", timeout, lambda c, t: c.search(
            alias, data=[query], filter=expr, limit=limit, anns_field="vector",
            search_params={"metric_type": METRIC_TYPE, "params": {"ef": max(SEARCH_EF, limit)}},
            consistency_level=SEARCH_CONSISTENCY, **single_attempt(t),
        ))
        hits = result[0] if result else []
        return [(str(hit["id"]), float(hit["distance"])) for hit in hits]

    def search_children(self, vector: Sequence[float], limit: int, *, now_epoch: int, event_id: str | None = None,
                        parent_ids: Sequence[str] | None = None,
                        timeout: float | None = None) -> list[tuple[str, float]]:
        query = _check_search(vector, limit, self.dim)
        if parent_ids is not None and not parent_ids:
            return []
        return self._search(self.child_alias, query, limit, build_child_filter(now_epoch, event_id, parent_ids), timeout)

    def search_parents(self, vector: Sequence[float], limit: int, *,
                       timeout: float | None = None) -> list[tuple[str, float]]:
        return self._search(self.parent_alias, _check_search(vector, limit, self.dim), limit, "", timeout)

    def _list_ids(self, alias: str, timeout: float | None) -> set[str]:
        def run(client: Any, t: float) -> set[str]:
            iterator = client.query_iterator(alias, batch_size=LIST_BATCH_SIZE, filter='id != ""', output_fields=["id"],
                                             consistency_level=LIST_CONSISTENCY, **single_attempt(t))
            ids: set[str] = set()
            try:
                while batch := iterator.next():
                    ids.update(str(row["id"]) for row in batch)
            finally:
                iterator.close()
            return ids
        return self._call("query", timeout, run)

    def list_child_ids(self, *, timeout: float | None = None) -> set[str]:
        return self._list_ids(self.child_alias, timeout)

    def list_parent_ids(self, *, timeout: float | None = None) -> set[str]:
        return self._list_ids(self.parent_alias, timeout)

    # ---- 健康检查与加载

    def _inspect(self, timeout: float | None) -> tuple[VectorStoreStatus, list[str]]:
        """返回（状态, 未 load 的集合名）。别名缺失=not_initialized；维度不符、未 load、连不上=down。"""
        started = time.perf_counter()
        t = self._timeout(timeout)
        aliases = (self.child_alias, self.parent_alias)
        collections: dict[str, str | None] = dict.fromkeys(aliases)

        def status(state: HealthState, **kwargs: Any) -> VectorStoreStatus:
            return VectorStoreStatus(status=state, provider=self.provider, uri=self.uri,
                                     latency_ms=_elapsed_ms(started), collections=dict(collections), **kwargs)
        rpc = single_attempt(t)
        try:
            client = self._get_client(t)
            # 先列别名再 describe：直接 describe 不存在的别名会让 pymilvus 每次打一条 ERROR 日志。
            existing = set((client.list_aliases(**rpc) or {}).get("aliases", []))
            for alias in aliases:
                if alias in existing:
                    collections[alias] = client.describe_alias(alias, **rpc)["collection_name"]
            missing = [alias for alias in aliases if collections[alias] is None]
            if missing:
                return status("not_initialized", detail=f"别名不存在：{', '.join(missing)}；先执行 make memory-init"), []
            problems: list[str] = []
            unloaded: list[str] = []
            for name in collections.values():
                dim = vector_dim(client.describe_collection(name, **rpc))
                if dim != self.dim:
                    problems.append(f"{name}: collection dim={dim}, config dim={self.dim}")
                    continue
                state = load_state_name(client.get_load_state(name, **rpc))
                if state != "Loaded":
                    problems.append(f"{name}: 未加载（state={state}）")
                    unloaded.append(name)
        except Exception as exc:  # noqa: BLE001  健康检查只报告状态，不抛异常
            return status("down", error=short_error(exc)), []
        if problems:
            # 只有“未加载”一种问题时才把列表交给 ensure_loaded 去 load；有维度不符就不碰集合。
            only_unloaded = len(problems) == len(unloaded)
            return status("down", dim=self.dim, error=applog.redact("；".join(problems))[:300]), \
                unloaded if only_unloaded else []
        return status("ok", dim=self.dim), []

    def check(self, *, timeout: float | None = None) -> VectorStoreStatus:
        return self._inspect(timeout)[0]

    def ensure_loaded(self, *, timeout: float | None = None) -> VectorStoreStatus:
        """启动检查用（第 8.5 节）：集合存在但未 load 时尝试 load 一次，失败就保持 down，由调用方降级。"""
        current, unloaded = self._inspect(timeout)
        if not unloaded:
            return current  # 正常、未初始化、连不上、维度不符（_inspect 此时不返回未加载列表）都不尝试 load
        t = self._timeout(timeout)
        try:
            client = self._get_client(t)
            for name in unloaded:
                client.load_collection(name, timeout=rpc_timeout(t))  # load 内部会等加载进度，用 int 超时限住
        except Exception as exc:  # noqa: BLE001
            return VectorStoreStatus(status="down", provider=self.provider, uri=self.uri,
                                     latency_ms=current.latency_ms, collections=current.collections, dim=self.dim,
                                     error=f"load 失败：{short_error(exc)}")
        return self._inspect(timeout)[0]

    def close(self) -> None:
        with self._lock:
            client, self._client, self._closed = self._client, None, True
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001  关闭失败不影响退出
                pass


# ---------------------------------------------------------------- 进程内唯一实例

_store: VectorStore | None = None
_store_settings: Settings | None = None
_store_lock = threading.Lock()


def get_vector_store() -> VectorStore:
    """按当前配置返回进程内唯一的向量库实例；配置缓存被清（测试改环境变量）后按新配置重建。"""
    global _store, _store_settings
    settings = get_settings()
    old: VectorStore | None = None
    with _store_lock:
        if _store is None or _store_settings is not settings:
            old = _store
            _store = FakeVectorStore(settings) if settings.vectorstore_provider == "fake" else MilvusVectorStore(settings)
            _store_settings = settings
        store = _store
    if old is not None:
        old.close()
    return store


def close_vector_store() -> None:
    """应用退出时关闭连接；还没创建过实例时什么也不做。"""
    global _store, _store_settings
    with _store_lock:
        store, _store, _store_settings = _store, None, None
    if store is not None:
        store.close()


def vector_store_created() -> bool:
    return _store is not None
