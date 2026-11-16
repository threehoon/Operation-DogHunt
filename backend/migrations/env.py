"""Alembic 运行环境：连接串从 get_settings() 读，不读 alembic.ini；导入 app.models 让 autogenerate 看到所有表。"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

import app.models  # noqa: F401  注册表模型
from app.core.config import get_settings
from app.db.base import Base

config = context.config
# 命令行跑迁移时按 alembic.ini 配日志；在后端或测试进程里调用时传 attributes["configure_logger"]=False，
# 否则 fileConfig 会改掉 root、uvicorn、sqlalchemy 已有的日志设置。
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    return get_settings().database_url.get_secret_value()


def _is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def run_migrations_offline() -> None:
    """只生成 SQL（alembic upgrade head --sql），不连库。"""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(url),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _database_url()
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite(url),  # SQLite 不支持大部分 ALTER，只在离线测试里用
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
