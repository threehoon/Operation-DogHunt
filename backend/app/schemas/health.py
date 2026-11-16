from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app: str
    environment: Literal["development", "test", "production"]
    version: str


class DatabaseHealth(BaseModel):
    status: Literal["ok", "down"]
    url: str = Field(description="去掉密码的连接串")
    latency_ms: int
    revision: str | None = Field(default=None, description="Alembic 当前版本；还没跑迁移时为空")
    error: str | None = Field(default=None, description="连接失败原因（已脱敏）")


class ComponentHealth(BaseModel):
    status: Literal["ok", "down", "not_implemented"]
    detail: str | None = None


class StorageHealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: DatabaseHealth
    milvus: ComponentHealth
    embedding: ComponentHealth
