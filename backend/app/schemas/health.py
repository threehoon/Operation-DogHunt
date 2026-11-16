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


class MilvusHealth(BaseModel):
    status: Literal["ok", "down", "not_initialized", "fake"] = Field(
        description="ok 可用；down 连不上、超时、维度不符或集合未加载；not_initialized 别名不存在（先 make memory-init）；"
                    "fake 用的是内存假实现（DOGHUNT_VECTORSTORE_PROVIDER=fake）")
    provider: Literal["milvus", "fake"]
    uri: str = Field(description="去掉账号、密码和查询参数的地址；不含 token")
    latency_ms: int
    collections: dict[str, str | None] = Field(default_factory=dict, description="别名 → 实际集合名；别名不存在时为空")
    dim: int | None = Field(default=None, description="配置的向量维度（DOGHUNT_EMBEDDING_DIM）；连上 Milvus 并读到两个集合后返回，"
                                                      "集合维度不一致时 status=down、error 里写集合的实际维度；连不上或未初始化时为空；fake 时为配置值")
    error: str | None = Field(default=None, description="失败原因（已脱敏）")
    detail: str | None = None


class StorageHealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = Field(
        description="database 为 down，或 milvus 为 down / not_initialized 时为 degraded；milvus=fake 与 embedding 不计入")
    database: DatabaseHealth
    milvus: MilvusHealth
    embedding: ComponentHealth
