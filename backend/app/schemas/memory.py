"""记忆接口的输出格式，字段与前端 frontend/src/types/memory.ts 对齐（camelCase）。

前端已有字段名不变；额外字段（key、scopeKind、scopeKey、eventId、createdAt、forgottenAt）只增不改。
时间一律输出带偏移的 ISO 8601（UTC，例如 2026-11-16T15:30:00+00:00）。
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel

from app.models.memory import MemoryChild as MemoryChildRow
from app.models.memory import MemoryEvent as MemoryEventRow
from app.models.memory import MemoryParent as MemoryParentRow

BlockStatus = Literal["active", "candidate", "superseded", "expired", "disabled", "archived"]
BlockKind = Literal["explicit", "inferred", "hypothesis", "ai", "observation"]
SourceKind = Literal["chat", "record", "maintenance"]
ScopeKind = Literal["global", "league", "event", "market"]
# 前端现有 MaintenanceAction 只有前五个，forget/rebuild/restore 由 P7.1 补到前端类型（第 7.3 节）。
MaintenanceAction = Literal["merge", "expire", "conflict", "archive", "split", "forget", "rebuild", "restore"]

IsoTime = Annotated[datetime, PlainSerializer(lambda value: value.isoformat(), return_type=str)]


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class MemoryParentOut(_CamelModel):
    id: str
    key: str = Field(description="固定目录里的稳定键，如 output_pref")
    title: str
    summary: str

    @classmethod
    def from_row(cls, row: MemoryParentRow) -> "MemoryParentOut":
        return cls(id=row.id, key=row.key, title=row.title, summary=row.summary)


class MemorySource(_CamelModel):
    kind: SourceKind
    label: str


class MemoryChildOut(_CamelModel):
    id: str
    parent_id: str
    title: str
    body: str
    kind: BlockKind
    status: BlockStatus
    source: MemorySource
    scope: str = Field(description="展示用的适用范围（scope_label）")
    updated_at: IsoTime
    expires_at: IsoTime | None = Field(description="长期有效为 null")
    version: int
    superseded_by: str | None
    recalls: int
    scope_kind: ScopeKind
    scope_key: str | None
    event_id: str | None
    created_at: IsoTime
    forgotten_at: IsoTime | None = Field(description="用户选“忘记”的时间；停用的条目为 null")

    @classmethod
    def from_row(cls, row: MemoryChildRow) -> "MemoryChildOut":
        return cls(
            id=row.id, parent_id=row.parent_id, title=row.title, body=row.body, kind=row.kind, status=row.status,
            source=MemorySource(kind=row.source_kind, label=row.source_label), scope=row.scope_label,
            updated_at=row.updated_at, expires_at=row.expires_at, version=row.version,
            superseded_by=row.superseded_by, recalls=row.recalls, scope_kind=row.scope_kind,
            scope_key=row.scope_key, event_id=row.event_id, created_at=row.created_at,
            forgotten_at=row.forgotten_at,
        )


class MaintenanceEntryOut(_CamelModel):
    id: str = Field(description="数据库自增 id 转成的字符串")
    time: IsoTime
    action: MaintenanceAction
    detail: str
    block_ids: list[str]

    @classmethod
    def from_row(cls, row: MemoryEventRow) -> "MaintenanceEntryOut":
        return cls(id=str(row.id), time=row.created_at, action=row.action, detail=row.detail,
                   block_ids=list(row.block_ids or []))


class MemoryTreeResponse(_CamelModel):
    parents: list[MemoryParentOut]
    children: list[MemoryChildOut]
    log: list[MaintenanceEntryOut]
