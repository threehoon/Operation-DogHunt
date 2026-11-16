"""长期记忆的五张表（开发文档第 7.3 节，v0.4）：父块目录、子块条目、使用记录、维护记录、抽取结果。

- 主键：父块、子块是 UUID4 字符串（Milvus 用同一个 id）；其余三张表是自增 bigint（SQLite 下用 INTEGER 才会自增）。
- 枚举值用 CHECK，不用 PostgreSQL ENUM，后续加值不用改类型。取值与前端 types/memory.ts 一致。
- 时间列全部 UTCDateTime；updated_at 由应用层更新。
- 只给离线测试用 create_all；正式库只走 Alembic（migrations/versions/0002_memory_tables.py），两边结构要保持一致。
- 不建 ORM relationship：级联删除和 SET NULL 交给数据库外键，避免 ORM 在删除前自己改子行。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

DEFAULT_OWNER = "local"
DEFAULT_SCOPE_LABEL = "所有聊天"

BLOCK_KINDS = ("explicit", "inferred", "hypothesis", "ai", "observation")
BLOCK_STATUSES = ("active", "candidate", "superseded", "expired", "disabled", "archived")
LIVE_STATUSES = ("active", "candidate")  # 部分唯一索引覆盖的“生效或候选”状态
EMBED_STATES = ("pending", "indexed", "failed")
SCOPE_KINDS = ("global", "league", "event", "market")
SOURCE_KINDS = ("chat", "record", "maintenance")
EVENT_ACTIONS = ("merge", "expire", "conflict", "archive", "split", "forget", "rebuild", "restore")
EXTRACTION_OUTCOMES = ("pending", "none", "created", "rejected", "failed")

# 同一父块下，同内容只能有一条生效或候选条目；历史行不受限（第 11.4 节）。两种库用同一个条件。
LIVE_WHERE = text("status IN ('active', 'candidate')")

# bigserial：Postgres 用 BIGINT 自增；SQLite 只有 INTEGER PRIMARY KEY 会自增。
AutoBigInt = BigInteger().with_variant(Integer(), "sqlite")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class MemoryParent(Base):
    __tablename__ = "memory_parents"
    __table_args__ = (
        UniqueConstraint("owner_id", "key"),
        CheckConstraint(_in("embed_state", EMBED_STATES), name="embed_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_OWNER, server_default=DEFAULT_OWNER)
    key: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    content_hash: Mapped[str] = mapped_column(String(64))
    embed_state: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    embed_model: Mapped[str] = mapped_column(String(100), default="", server_default="")
    embed_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MemoryChild(Base):
    __tablename__ = "memory_children"
    __table_args__ = (
        CheckConstraint(_in("kind", BLOCK_KINDS), name="kind"),
        CheckConstraint(_in("status", BLOCK_STATUSES), name="status"),
        CheckConstraint(_in("embed_state", EMBED_STATES), name="embed_state"),
        CheckConstraint(_in("scope_kind", SCOPE_KINDS), name="scope_kind"),
        CheckConstraint(_in("source_kind", SOURCE_KINDS), name="source_kind"),
        CheckConstraint("length(title) <= 100", name="title_length"),
        CheckConstraint("length(body) <= 300", name="body_length"),
        # 忘记只是 disabled 的一种：已忘记的行不能是别的状态，恢复时要同时清空 forgotten_at
        CheckConstraint("forgotten_at IS NULL OR status = 'disabled'", name="forgotten_disabled"),
        Index("uq_memory_children_live_content", "parent_id", "content_hash", unique=True,
              postgresql_where=LIVE_WHERE, sqlite_where=LIVE_WHERE),
        Index("ix_memory_children_parent_id_status", "parent_id", "status"),
        Index("ix_memory_children_status_expires_at", "status", "expires_at"),
        Index("ix_memory_children_event_id_status", "event_id", "status"),
        Index("ix_memory_children_source_kind_source_ref", "source_kind", "source_ref"),
        Index("ix_memory_children_parent_id_content_hash", "parent_id", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    parent_id: Mapped[str] = mapped_column(String(36), ForeignKey("memory_parents.id", ondelete="CASCADE"))
    owner_id: Mapped[str] = mapped_column(String(36), default=DEFAULT_OWNER, server_default=DEFAULT_OWNER)
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    scope_kind: Mapped[str] = mapped_column(String(16), default="global", server_default="global")
    scope_key: Mapped[str | None] = mapped_column(String(64))
    scope_label: Mapped[str] = mapped_column(String(100), default=DEFAULT_SCOPE_LABEL, server_default=DEFAULT_SCOPE_LABEL)
    event_id: Mapped[str | None] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(16))
    source_ref: Mapped[str] = mapped_column(String(100))
    source_label: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    superseded_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("memory_children.id", ondelete="SET NULL"))
    recalls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    content_hash: Mapped[str] = mapped_column(String(64))
    forgotten_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    embed_state: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    embed_model: Mapped[str] = mapped_column(String(100), default="", server_default="")
    embed_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MemoryUsage(Base):
    __tablename__ = "memory_usage"
    __table_args__ = (
        UniqueConstraint("child_id", "trace_id"),  # 重试不重复计数
        Index("ix_memory_usage_trace_id", "trace_id"),  # 查“某次回答用了哪些记忆”
    )

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    child_id: Mapped[str] = mapped_column(String(36), ForeignKey("memory_children.id", ondelete="CASCADE"))
    trace_id: Mapped[str] = mapped_column(String(32))
    session_ref: Mapped[str | None] = mapped_column(String(100))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MemoryEvent(Base):
    __tablename__ = "memory_events"
    __table_args__ = (
        CheckConstraint(_in("action", EVENT_ACTIONS), name="action"),
        Index("ix_memory_events_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text)
    block_ids: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class MemoryExtraction(Base):
    __tablename__ = "memory_extractions"
    __table_args__ = (
        UniqueConstraint("trace_id"),  # 一次聊天只有一行（第 11.5 节）
        CheckConstraint(_in("outcome", EXTRACTION_OUTCOMES), name="outcome"),
    )

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32))
    extractor_model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    outcome: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
