"""记忆表：父块目录、子块条目、使用记录、维护记录、抽取结果（开发文档第 7.3 节，v0.4）。

手写，与 app/models/memory.py 保持一致；约束名按 app/db/base.py 的命名约定显式写出，并用 op.f() 标成最终名字，
否则 Alembic 会对 CHECK 名再套一次约定（变成 ck_memory_children_ck_memory_children_kind）。
部分唯一索引 uq_memory_children_live_content 只覆盖 active/candidate，Postgres 与 SQLite 都支持。

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.types import UTCDateTime

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 写死在迁移里，不从模型导入：以后模型改了取值，这个历史版本的结构也不能跟着变。
BLOCK_KINDS = ("explicit", "inferred", "hypothesis", "ai", "observation")
BLOCK_STATUSES = ("active", "candidate", "superseded", "expired", "disabled", "archived")
EMBED_STATES = ("pending", "indexed", "failed")
SCOPE_KINDS = ("global", "league", "event", "market")
SOURCE_KINDS = ("chat", "record", "maintenance")
EVENT_ACTIONS = ("merge", "expire", "conflict", "archive", "split", "forget", "rebuild", "restore")
EXTRACTION_OUTCOMES = ("pending", "none", "created", "rejected", "failed")
LIVE_WHERE = sa.text("status IN ('active', 'candidate')")

AutoBigInt = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "memory_parents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), server_default="local", nullable=False),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embed_state", sa.String(16), server_default="pending", nullable=False),
        sa.Column("embed_model", sa.String(100), server_default="", nullable=False),
        sa.Column("embed_updated_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(_in("embed_state", EMBED_STATES), name=op.f("ck_memory_parents_embed_state")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_parents")),
        sa.UniqueConstraint("owner_id", "key", name=op.f("uq_memory_parents_owner_id_key")),
    )

    op.create_table(
        "memory_children",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("parent_id", sa.String(36), nullable=False),
        sa.Column("owner_id", sa.String(36), server_default="local", nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("scope_kind", sa.String(16), server_default="global", nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=True),
        sa.Column("scope_label", sa.String(100), server_default="所有聊天", nullable=False),
        sa.Column("event_id", sa.String(64), nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_ref", sa.String(100), nullable=False),
        sa.Column("source_label", sa.String(100), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("superseded_by", sa.String(36), nullable=True),
        sa.Column("recalls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("forgotten_at", UTCDateTime(), nullable=True),
        sa.Column("embed_state", sa.String(16), server_default="pending", nullable=False),
        sa.Column("embed_model", sa.String(100), server_default="", nullable=False),
        sa.Column("embed_updated_at", UTCDateTime(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(_in("kind", BLOCK_KINDS), name=op.f("ck_memory_children_kind")),
        sa.CheckConstraint(_in("status", BLOCK_STATUSES), name=op.f("ck_memory_children_status")),
        sa.CheckConstraint(_in("embed_state", EMBED_STATES), name=op.f("ck_memory_children_embed_state")),
        sa.CheckConstraint(_in("scope_kind", SCOPE_KINDS), name=op.f("ck_memory_children_scope_kind")),
        sa.CheckConstraint(_in("source_kind", SOURCE_KINDS), name=op.f("ck_memory_children_source_kind")),
        sa.CheckConstraint("length(title) <= 100", name=op.f("ck_memory_children_title_length")),
        sa.CheckConstraint("length(body) <= 300", name=op.f("ck_memory_children_body_length")),
        sa.CheckConstraint("forgotten_at IS NULL OR status = 'disabled'", name=op.f("ck_memory_children_forgotten_disabled")),
        sa.ForeignKeyConstraint(["parent_id"], ["memory_parents.id"], ondelete="CASCADE",
                                name=op.f("fk_memory_children_parent_id_memory_parents")),
        sa.ForeignKeyConstraint(["superseded_by"], ["memory_children.id"], ondelete="SET NULL",
                                name=op.f("fk_memory_children_superseded_by_memory_children")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_children")),
    )
    op.create_index("uq_memory_children_live_content", "memory_children", ["parent_id", "content_hash"], unique=True,
                    postgresql_where=LIVE_WHERE, sqlite_where=LIVE_WHERE)
    op.create_index("ix_memory_children_parent_id_status", "memory_children", ["parent_id", "status"])
    op.create_index("ix_memory_children_status_expires_at", "memory_children", ["status", "expires_at"])
    op.create_index("ix_memory_children_event_id_status", "memory_children", ["event_id", "status"])
    op.create_index("ix_memory_children_source_kind_source_ref", "memory_children", ["source_kind", "source_ref"])
    op.create_index("ix_memory_children_parent_id_content_hash", "memory_children", ["parent_id", "content_hash"])

    op.create_table(
        "memory_usage",
        sa.Column("id", AutoBigInt, autoincrement=True, nullable=False),
        sa.Column("child_id", sa.String(36), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("session_ref", sa.String(100), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column("used_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["child_id"], ["memory_children.id"], ondelete="CASCADE",
                                name=op.f("fk_memory_usage_child_id_memory_children")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_usage")),
        sa.UniqueConstraint("child_id", "trace_id", name=op.f("uq_memory_usage_child_id_trace_id")),
    )
    op.create_index("ix_memory_usage_trace_id", "memory_usage", ["trace_id"])

    op.create_table(
        "memory_events",
        sa.Column("id", AutoBigInt, autoincrement=True, nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("block_ids", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(_in("action", EVENT_ACTIONS), name=op.f("ck_memory_events_action")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_events")),
    )
    op.create_index("ix_memory_events_created_at", "memory_events", ["created_at"])

    op.create_table(
        "memory_extractions",
        sa.Column("id", AutoBigInt, autoincrement=True, nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("extractor_model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.CheckConstraint(_in("outcome", EXTRACTION_OUTCOMES), name=op.f("ck_memory_extractions_outcome")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_extractions")),
        sa.UniqueConstraint("trace_id", name=op.f("uq_memory_extractions_trace_id")),
    )


def downgrade() -> None:
    # 按外键依赖倒序删除；索引随表一起删。
    op.drop_table("memory_extractions")
    op.drop_table("memory_events")
    op.drop_table("memory_usage")
    op.drop_table("memory_children")
    op.drop_table("memory_parents")
