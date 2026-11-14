"""应用入口：只组装配置和接口，业务处理放在 services 中。"""

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.router import api_router
from app.core import applog
from app.core.config import get_settings
from app.core.trace_middleware import TraceMiddleware


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
    from app.integrations import balldontlie, dunkapi, local_keys
    for name in (balldontlie.KEY_NAME, dunkapi.KEY_NAME, "THE_ODDS_API_KEY"):
        try:
            applog.register_secret(local_keys.read_key(name))
        except ValueError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 只有后端真正启动时才写日志文件和聊天存档；离线测试用 ASGITransport 不触发这里。
    _register_known_secrets()
    applog.configure_files()
    yield
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
