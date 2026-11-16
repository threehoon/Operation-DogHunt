from typing import Annotated

from fastapi import APIRouter, Depends

from app import __version__
from app.core.config import Settings, get_settings
from app.db.session import check_database
from app.integrations.vectorstore import get_vector_store
from app.schemas.health import ComponentHealth, DatabaseHealth, HealthResponse, MilvusHealth, StorageHealthResponse

router = APIRouter(tags=["health"])

# 计入整体 degraded 的 Milvus 状态。fake 是配置选择（离线测试、本机不用向量库），不算故障。
MILVUS_DEGRADED = frozenset({"down", "not_initialized"})


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
    """就绪检查：存储断开时仍返回 200，用 status=degraded 表示；连接串、地址都不带密码或 token。

    整体 degraded 当且仅当：database=down，或 milvus 为 down（连不上、超时、维度不符、集合未加载）
    或 not_initialized（别名不存在，要先 make memory-init）。milvus=fake（DOGHUNT_VECTORSTORE_PROVIDER=fake）
    如实标注但不计入；embedding 在 P5 前是 not_implemented，也不计入。
    同步路由，由 FastAPI 放进线程池；Milvus 连不上时最多等 DOGHUNT_MILVUS_TIMEOUT_SECONDS。
    """
    db = check_database()
    database = DatabaseHealth(
        status="ok" if db.ok else "down",
        url=db.url,
        latency_ms=db.latency_ms,
        revision=db.revision,
        error=db.error,
    )
    vector = get_vector_store().check()
    milvus = MilvusHealth(
        status=vector.status,
        provider=vector.provider,
        uri=vector.uri,
        latency_ms=vector.latency_ms,
        collections=vector.collections,
        dim=vector.dim,
        error=vector.error,
        detail=vector.detail,
    )
    degraded = not db.ok or vector.status in MILVUS_DEGRADED
    return StorageHealthResponse(
        status="degraded" if degraded else "ok",
        database=database,
        milvus=milvus,
        embedding=ComponentHealth(status="not_implemented", detail="嵌入接入在开发文档 P5"),
    )
