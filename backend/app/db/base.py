"""声明基类与约束命名规则；表模型放 app/models/，迁移只走 Alembic，不用 create_all 建正式库。"""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 固定命名，autogenerate 生成的索引、约束名稳定，SQLite 测试与 Postgres 一致。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
