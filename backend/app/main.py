"""应用入口：只组装配置和接口，业务处理放在 services 中。"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.router import api_router
from app.core import applog
from app.core.config import get_settings
from app.core.trace_middleware import TraceMiddleware
from app.db.session import check_database, dispose_engine, register_database_secrets

logger = logging.getLogger(__name__)
STARTUP_DB_CHECK_SECONDS = 1


def _register_known_secrets() -> None:
    """启动时把已配置的密钥值登记给脱敏过滤；读不到的跳过，之后各调用处读取时还会再登记。"""
    settings = get_settings()
    applog.register_secret(settings.model_api_key.get_secret_value())
    try:
        extra = json.loads(settings.model_extra_headers.get_secret_value())
        for value in extra.values() if isinstance(extra, dict) else []:
            applog.register_secret(value if isinstance(value, str) else None)
    except ValueError:
        pass
    register_database_secrets(settings)
    from app.integrations import balldontlie, dunkapi, local_keys
    for name in (balldontlie.KEY_NAME, dunkapi.KEY_NAME, "THE_ODDS_API_KEY"):
        try:
            applog.register_secret(local_keys.read_key(name))
        except ValueError:
            pass


async def _startup_database_check() -> None:
    """启动时做一次 SELECT 1，最多等 1 秒；连不上只记 warning，不阻止启动。"""
    if not get_settings().db_startup_check:
        return
    try:
        status = await asyncio.wait_for(asyncio.to_thread(check_database), timeout=STARTUP_DB_CHECK_SECONDS)
    except TimeoutError:
        applog.event(logger, "db.startup", f"数据库 {STARTUP_DB_CHECK_SECONDS} 秒内没有响应，先不连库启动",
                     level=logging.WARNING, ok=False, reason="timeout")
        return
    if status.ok:
        applog.event(logger, "db.startup", f"数据库可连接：{status.url}", ok=True, url=status.url,
                     revision=status.revision, ms=status.latency_ms)
    else:
        applog.event(logger, "db.startup", f"数据库连不上，先不连库启动：{status.error}", level=logging.WARNING,
                     ok=False, url=status.url, reason=status.error)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 只有后端真正启动时才写日志文件和聊天存档；离线测试用 ASGITransport 不触发这里。
    _register_known_secrets()
    applog.configure_files()
    await _startup_database_check()
    yield
    dispose_engine()
    applog.shutdown_files()


def create_app() -> FastAPI:
    applog.configure_console()
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="NBA 聊天助手后端：服务检查、中转模型配置状态与聊天。",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    application.add_middleware(TraceMiddleware)
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
