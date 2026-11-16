from typing import Annotated

from fastapi import APIRouter, Depends

from app import __version__
from app.core.config import Settings, get_settings
from app.db.session import check_database
from app.schemas.health import ComponentHealth, DatabaseHealth, HealthResponse, StorageHealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="检查后端是否运行")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    # 只说明应用进程可响应，不代表数据库、盘口或 GPT 已连通。
    return HealthResponse(
        app=settings.app_name,
        environment=settings.environment,
        version=__version__,
    )


@router.get("/health/storage", response_model=StorageHealthResponse, summary="检查数据库、向量库与嵌入状态")
def storage_health() -> StorageHealthResponse:
    # 就绪检查：存储断开时仍返回 200，status 为 degraded；连接串不带密码。同步路由，由 FastAPI 放进线程池。
    db = check_database()
    database = DatabaseHealth(
        status="ok" if db.ok else "down",
        url=db.url,
        latency_ms=db.latency_ms,
        revision=db.revision,
        error=db.error,
    )
    return StorageHealthResponse(
        status="ok" if db.ok else "degraded",
        database=database,
        # Milvus（P4）和嵌入（P5）还没接入，如实标明，不计入整体状态。
        milvus=ComponentHealth(status="not_implemented", detail="Milvus 接入在开发文档 P4"),
        embedding=ComponentHealth(status="not_implemented", detail="嵌入接入在开发文档 P5"),
    )
