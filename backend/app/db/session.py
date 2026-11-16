"""数据库连接：engine 懒加载，第一次用时才创建，import 阶段不要求数据库存在。

仓储函数用同步写法；async 路由用 asyncio.to_thread 调用，或直接写成同步路由交给 FastAPI 线程池。
测试改了环境变量后，先 get_settings.cache_clear() 再 dispose_engine()。
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from app.core import applog
from app.core.config import Settings, get_settings

CONNECT_TIMEOUT_SECONDS = 2  # libpq 的最小有效值是 2 秒
# make_url 遇到格式错误抛 ArgumentError，端口不是数字时抛 ValueError；两种都算“连接串无效”。
URL_ERRORS = (ArgumentError, ValueError)
_engine_lock = threading.RLock()  # get_sessionmaker 持锁时还会调 get_engine


def safe_url(url: str) -> str:
    """去掉密码的连接串，只给日志和 /health/storage 显示，例如 postgresql://doghunt@127.0.0.1:5432/doghunt。"""
    try:
        parsed = make_url(url)
    except URL_ERRORS:
        return "（连接串格式无效）"
    # URL.set(password=None) 表示“不修改”，会把密码留下；URL 是 NamedTuple，用 _replace 真正清掉。
    return parsed._replace(drivername=parsed.get_backend_name(), password=None).render_as_string(hide_password=False)


def register_database_secrets(settings: Settings | None = None) -> None:
    """把数据库密码登记给日志脱敏：单独配置的密码，以及覆盖连接串里带的密码。"""
    settings = settings or get_settings()
    applog.register_secret(settings.db_password.get_secret_value())
    url = settings.database_url.get_secret_value()
    try:
        password = make_url(url).password
    except URL_ERRORS:
        applog.register_secret(url)  # 解析不了就把整条串当秘密，避免其中的密码原样出现在日志里
        return
    applog.register_secret(password)


def _build_engine(settings: Settings) -> Engine:
    url = make_url(settings.database_url.get_secret_value())
    if url.get_backend_name() == "sqlite":
        # SQLite 只给离线测试用；外键默认关闭，必须每个连接打开，否则级联和自引用外键测不出来。
        engine = create_engine(url, echo=settings.db_echo, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_foreign_keys(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(
        url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=5,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
            "application_name": "doghunt-backend",
        },
    )


_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """第一次调用时创建。加锁：启动检查的线程和第一个请求可能同时进来，只能建一个连接池，否则多出来的没人释放。"""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = _build_engine(get_settings())
        return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _sessionmaker
    with _engine_lock:
        if _sessionmaker is None:
            _sessionmaker = sessionmaker(bind=get_engine(), expire_on_commit=False)
        return _sessionmaker


def engine_created() -> bool:
    return _engine is not None


@contextmanager
def session_scope() -> Iterator[Session]:
    """每次请求或每个后台任务一个会话：正常结束提交，出异常回滚，最后关闭。"""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine() -> None:
    """释放连接池并清掉缓存，下次使用时按当前配置重建；还没创建过 engine 时什么也不做。"""
    global _engine, _sessionmaker
    with _engine_lock:
        engine, _engine, _sessionmaker = _engine, None, None
    if engine is not None:
        engine.dispose()


@dataclass(frozen=True)
class DatabaseStatus:
    ok: bool
    url: str
    latency_ms: int
    revision: str | None = None
    error: str | None = None


def _short_error(exc: BaseException) -> str:
    """异常只留类名和第一行，先脱敏；不把完整连接串或堆栈交给接口。"""
    original = getattr(exc, "orig", None) or exc
    first_line = (str(original).strip().splitlines() or [""])[0]
    return applog.redact(f"{type(original).__name__}: {first_line}")[:300]


def check_database() -> DatabaseStatus:
    """执行 SELECT 1 并读取 Alembic 版本；失败返回原因，不抛异常。"""
    url = safe_url(get_settings().database_url.get_secret_value())  # safe_url 自己兜住格式错误
    started = time.perf_counter()
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
            # 先看表在不在（还没跑迁移时没有，不算失败）；其他数据库错误照常报 down，不当成“没迁移”。
            revision = None
            if inspect(connection).has_table("alembic_version"):
                revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:  # noqa: BLE001  任何连接问题都只报告状态
        return DatabaseStatus(ok=False, url=url, latency_ms=_elapsed_ms(started), error=_short_error(exc))
    return DatabaseStatus(ok=True, url=url, latency_ms=_elapsed_ms(started), revision=revision)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
