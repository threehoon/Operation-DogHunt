from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # 配置文件仅定位本项目 backend/.env，模型凭证由用户提供，不从其他应用导入。
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="DOGHUNT_",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Operation DogHunt"
    environment: Literal["development", "test", "production"] = "development"
    model_base_url: str = ""
    model_api_key: SecretStr = SecretStr("")
    model_name: str = ""
    model_reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] = "high"
    model_extra_headers: SecretStr = SecretStr("{}")
    model_timeout_seconds: int = Field(default=120, ge=5, le=300)
    model_max_output_tokens: int = Field(default=4096, ge=128, le=16384)

    # 本机 PostgreSQL（deploy/compose.dev.yml）。密码只在 backend/.env；完整连接串只在内存里拼。
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_user: str = "doghunt"
    db_password: SecretStr = SecretStr("")
    db_name: str = "doghunt"
    # 可选覆盖，主要给测试用 SQLite；填了就不再用上面五项拼接。
    database_url_override: SecretStr = Field(default=SecretStr(""), validation_alias="DOGHUNT_DATABASE_URL")
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_pool_timeout_seconds: int = Field(default=10, ge=1, le=60)
    db_statement_timeout_ms: int = Field(default=2000, ge=100, le=60000)
    db_echo: bool = False
    # 启动时做一次 SELECT 1（最多等 1 秒，失败只记 warning）；离线测试强制关闭。
    db_startup_check: bool = True

    # 本机 Milvus standalone（开发文档第 8、12.2 节）。代码只用别名，集合名由别名前缀 + 版本号拼出，只在初始化时用。
    # DOGHUNT_MILVUS_PORT / DOGHUNT_MILVUS_HEALTH_PORT 只给编排和 make storage-check 用，不进这里。
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: SecretStr = SecretStr("")  # 本机无认证；以后启用认证再填，启动时登记脱敏
    milvus_db_name: str = "default"
    milvus_child_alias: str = Field(default="doghunt_memory_child", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,200}$")
    milvus_parent_alias: str = Field(default="doghunt_memory_parent", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,200}$")
    milvus_timeout_seconds: float = Field(default=2, gt=0, le=60)
    milvus_collection_version: str = Field(default="v1", pattern=r"^[A-Za-z0-9_]{1,32}$")
    # 启动时连一次 Milvus 并按需 load（最多等 1 秒，失败只记 warning）；离线测试强制关闭。
    milvus_startup_check: bool = True
    vectorstore_provider: Literal["milvus", "fake"] = "milvus"
    # 向量维度，必须与 Milvus 集合一致（BGE-M3 为 1024）；不一致时初始化报错退出，不自动重建。
    embedding_dim: int = Field(default=1024, ge=1, le=32768)

    @property
    def database_url(self) -> SecretStr:
        override = self.database_url_override.get_secret_value().strip()
        if override:
            return SecretStr(override)
        # 用 quote(safe="") 而不是 quote_plus：SQLAlchemy 解析时用 unquote，"+" 不会还原成空格。
        user = quote(self.db_user, safe="")
        password = quote(self.db_password.get_secret_value(), safe="")
        name = quote(self.db_name, safe="")
        auth = f"{user}:{password}" if password else user
        return SecretStr(f"postgresql+psycopg://{auth}@{self.db_host}:{self.db_port}/{name}")

    @property
    def model_configured(self) -> bool:
        return bool(
            self.model_base_url.strip()
            and self.model_api_key.get_secret_value().strip()
            and self.model_name.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
