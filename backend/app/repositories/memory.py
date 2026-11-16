"""长期记忆的集中读写；路由和命令行不写 SQL。

事务边界：每个函数第一个参数是调用方传入的 Session，函数内只 flush 不 commit；
调用方用 app.db.session.session_scope() 包一次，正常结束提交、出错回滚。这样多步操作（例如去重判断加插入）
可以放在同一个事务里（第 11.4 节）。

停用与忘记（第 11.6 节）：两者 status 都是 disabled，忘记另写 forgotten_at；列表默认不返回已忘记条目。
状态或内容变化时 embed_state 回到 pending，表示向量库还要跟着写入或删除（第 8.2 节）。
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, select
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app.models.memory import (
    BLOCK_STATUSES,
    DEFAULT_OWNER,
    DEFAULT_SCOPE_LABEL,
    MemoryChild,
    MemoryEvent,
    MemoryParent,
    utcnow,
)
from app.services.memory.parents import PARENT_CATALOG, PARENT_KEYS, ParentSpec

# “数据库不可用”类错误：连不上、断开、连接池等待超时、表不存在（还没迁移）。只有这些映射为 503 或命令行退出码 1；
# 数据校验、序列化等其他错误照常抛出，不要伪装成数据库不可用。连接串格式错误在建 engine 时另行处理（URL_ERRORS）。
DATABASE_UNAVAILABLE_ERRORS = (OperationalError, InterfaceError, ProgrammingError, PoolTimeoutError)

DEFAULT_CHILD_LIMIT = 200
MAX_CHILD_LIMIT = 500
DEFAULT_EVENT_LIMIT = 50
MAX_EVENT_LIMIT = 200

# update_child 允许改的字段；改动其中的内容字段会重算 content_hash。
_CONTENT_FIELDS = ("title", "body", "scope_kind", "scope_key")
# 改了会让 Milvus 里的那一行过时的字段（第 8.2 节子块集合：向量由标题+正文生成，过滤字段 parent_id、kind、
# scope_kind、event_id、expires_at、content_hash），加上决定“该不该有向量”的 status。parent_id 不允许改。
# Milvus 的 updated_at 只用于排序和排查，单独改展示字段（scope_label、来源、版本、superseded_by）不重写向量。
_EMBED_FIELDS = frozenset({*_CONTENT_FIELDS, "kind", "event_id", "expires_at", "status"})
_UPDATABLE_FIELDS = frozenset({
    *_CONTENT_FIELDS, "kind", "status", "scope_label", "event_id", "expires_at", "version", "superseded_by",
    "source_kind", "source_ref", "source_label",
})


def normalize(text: str | None) -> str:
    """压缩空白：首尾去掉，中间连续空白（含换行、全角空格）换成一个空格。None 当空串。"""
    return " ".join((text or "").split())


def _sha256(*parts: str | None) -> str:
    return hashlib.sha256("\n".join(normalize(p) for p in parts).encode("utf-8")).hexdigest()


def child_content_hash(title: str, body: str, scope_kind: str, scope_key: str | None) -> str:
    """sha256(title + "\\n" + body + "\\n" + scope_kind + "\\n" + scope_key)，各段先压缩空白（第 7.3 节）。"""
    return _sha256(title, body, scope_kind, scope_key)


def parent_content_hash(title: str, summary: str) -> str:
    """sha256(title + "\\n" + summary)，各段先压缩空白。"""
    return _sha256(title, summary)


# ---------- 父块 ----------

def list_parents(session: Session, *, owner_id: str = DEFAULT_OWNER) -> list[MemoryParent]:
    """按固定目录的顺序返回；目录外的旧父块（目录删过键时）排在后面，按 key 排。"""
    order = case({key: index for index, key in enumerate(PARENT_KEYS)}, value=MemoryParent.key, else_=len(PARENT_KEYS))
    stmt = select(MemoryParent).where(MemoryParent.owner_id == owner_id).order_by(order, MemoryParent.key)
    return list(session.scalars(stmt))


def get_parent_by_key(session: Session, key: str, *, owner_id: str = DEFAULT_OWNER) -> MemoryParent | None:
    return session.scalar(select(MemoryParent).where(MemoryParent.owner_id == owner_id, MemoryParent.key == key))


@dataclass(frozen=True)
class SeedResult:
    created: list[str]
    updated: list[str]
    unchanged: list[str]


def seed_parents(session: Session, catalog: Sequence[ParentSpec] = PARENT_CATALOG, *,
                 owner_id: str = DEFAULT_OWNER) -> SeedResult:
    """按 (owner_id, key) 幂等写入父块目录。

    - 没有：新建，summary 用目录里的“收什么”说明，embed_state=pending。
    - 已有且标题改了：只更新标题，重算 content_hash、version+1、embed_state 回到 pending。
      summary 由程序按子块维护（P6），种子不覆盖。
    - 已有且标题相同：不动。
    重复执行不会重复插入；并发时由 UNIQUE(owner_id, key) 兜底。
    """
    created, updated, unchanged = [], [], []
    for spec in catalog:
        parent = get_parent_by_key(session, spec.key, owner_id=owner_id)
        if parent is None:
            summary = spec.collects
            session.add(MemoryParent(owner_id=owner_id, key=spec.key, title=spec.title, summary=summary,
                                     content_hash=parent_content_hash(spec.title, summary)))
            created.append(spec.key)
        elif parent.title != spec.title:
            parent.title = spec.title
            parent.content_hash = parent_content_hash(parent.title, parent.summary)
            parent.version += 1
            parent.embed_state = "pending"
            parent.updated_at = utcnow()
            updated.append(spec.key)
        else:
            unchanged.append(spec.key)
    session.flush()
    return SeedResult(created=created, updated=updated, unchanged=unchanged)


# ---------- 子块 ----------

def _check_statuses(statuses: Iterable[str]) -> list[str]:
    values = list(statuses)
    unknown = sorted(set(values) - set(BLOCK_STATUSES))
    if unknown:
        raise ValueError(f"未知的记忆状态：{', '.join(unknown)}")
    return values


def list_children(session: Session, *, owner_id: str = DEFAULT_OWNER, parent_id: str | None = None,
                  statuses: Iterable[str] | None = None, include_forgotten: bool = False,
                  limit: int = DEFAULT_CHILD_LIMIT, offset: int = 0) -> list[MemoryChild]:
    """列子块：默认排除已忘记（forgotten_at 非空），停用的仍返回。

    排序：updated_at 新的在前，相同时按 id，分页稳定。limit 限在 1..MAX_CHILD_LIMIT，offset 不小于 0。
    """
    stmt = select(MemoryChild).where(MemoryChild.owner_id == owner_id)
    if parent_id is not None:
        stmt = stmt.where(MemoryChild.parent_id == parent_id)
    if statuses is not None:
        stmt = stmt.where(MemoryChild.status.in_(_check_statuses(statuses)))
    if not include_forgotten:
        stmt = stmt.where(MemoryChild.forgotten_at.is_(None))
    limit = max(1, min(limit, MAX_CHILD_LIMIT))
    stmt = stmt.order_by(MemoryChild.updated_at.desc(), MemoryChild.id).limit(limit).offset(max(0, offset))
    return list(session.scalars(stmt))


def get_child(session: Session, child_id: str) -> MemoryChild | None:
    """按 id 取一条，已忘记的也返回（详情和维护记录要能查到）。"""
    return session.get(MemoryChild, child_id)


def find_by_content_hash(session: Session, parent_id: str, content_hash: str) -> list[MemoryChild]:
    """同父块、同内容哈希的全部行（含历史行），新的在前；第 11.4 节“取最新的一条”用第一个。"""
    stmt = (select(MemoryChild)
            .where(MemoryChild.parent_id == parent_id, MemoryChild.content_hash == content_hash)
            .order_by(MemoryChild.updated_at.desc(), MemoryChild.created_at.desc(), MemoryChild.id))
    return list(session.scalars(stmt))


def insert_child(session: Session, *, parent_id: str, title: str, body: str, kind: str, status: str,
                 source_kind: str, source_ref: str, source_label: str, scope_kind: str = "global",
                 scope_key: str | None = None, scope_label: str = DEFAULT_SCOPE_LABEL, event_id: str | None = None,
                 expires_at: datetime | None = None, version: int = 1, owner_id: str = DEFAULT_OWNER,
                 child_id: str | None = None) -> MemoryChild:
    """新建一条子块并 flush；content_hash 由这里算，embed_state 为 pending。

    同父块已有同内容的 active/candidate 行时，flush 抛 IntegrityError（部分唯一索引），由调用方按第 11.4 节处理。
    长度、密钥等写入校验在 P6 的写入服务里做；这里只靠数据库 CHECK 兜底。
    """
    now = utcnow()
    child = MemoryChild(
        parent_id=parent_id, owner_id=owner_id, title=title, body=body, kind=kind, status=status,
        scope_kind=scope_kind, scope_key=scope_key, scope_label=scope_label, event_id=event_id,
        source_kind=source_kind, source_ref=source_ref, source_label=source_label, expires_at=expires_at,
        version=version, content_hash=child_content_hash(title, body, scope_kind, scope_key),
        embed_state="pending", created_at=now, updated_at=now,
    )
    if child_id is not None:
        child.id = child_id
    session.add(child)
    session.flush()
    return child


def update_child(session: Session, child: MemoryChild, **changes) -> MemoryChild:
    """改子块的字段并 flush。

    改了 title/body/scope_kind/scope_key 会重算 content_hash；_EMBED_FIELDS 里的字段有变化时 embed_state 回到
    pending；updated_at 刷新。没有实际变化时什么也不改。不允许改 id、parent_id、forgotten_at、向量相关字段
    （忘记和恢复用专门的函数）；已忘记的条目不允许改 status，要先 restore_child（库里另有 CHECK 兜底）。
    """
    unknown = sorted(set(changes) - _UPDATABLE_FIELDS)
    if unknown:
        raise ValueError(f"不能用 update_child 修改：{', '.join(unknown)}")
    changed = {name for name, value in changes.items() if getattr(child, name) != value}
    if not changed:
        return child
    if "status" in changed and child.forgotten_at is not None:
        raise ValueError("这条记忆已被忘记，不能直接改状态；先用 restore_child 恢复")
    for name in changed:
        setattr(child, name, changes[name])
    if changed & set(_CONTENT_FIELDS):
        child.content_hash = child_content_hash(child.title, child.body, child.scope_kind, child.scope_key)
    if changed & _EMBED_FIELDS:
        child.embed_state = "pending"
    child.updated_at = utcnow()
    session.flush()
    return child


def _require_child(session: Session, child_id: str) -> MemoryChild:
    child = get_child(session, child_id)
    if child is None:
        raise LookupError(f"没有找到这条记忆：{child_id}")
    return child


def disable_child(session: Session, child_id: str) -> MemoryChild:
    """停用：status=disabled、embed_state=pending；forgotten_at 不动，列表里仍显示为“已停用”。

    不写维护事件：memory_events.action 的取值里没有“停用”（第 7.3 节），留给 P6.4 定。
    """
    child = _require_child(session, child_id)
    return update_child(session, child, status="disabled")


def forget_child(session: Session, child_id: str, *, detail: str | None = None) -> MemoryChild:
    """忘记（软删）：status=disabled、写 forgotten_at、embed_state=pending，并写一条 forget 事件。

    已经忘记过的再调用不重复写事件、不改 forgotten_at。
    """
    child = _require_child(session, child_id)
    if child.forgotten_at is not None:
        return child
    now = utcnow()
    child.status = "disabled"
    child.forgotten_at = now
    child.embed_state = "pending"
    child.updated_at = now
    add_event(session, "forget", detail or f"忘记“{child.title}”：列表不再显示，下一次召回不再使用。", [child.id])
    return child


RESTORE_LABELS = {"active": "生效", "superseded": "已替代", "expired": "已过期"}


def restore_status(child: MemoryChild, now: datetime) -> str:
    """恢复后应有的状态：不保留停用前的状态，按现有字段推断，避免恢复破坏版本链或让过期内容重新生效。

    - superseded_by 非空（停用前已被新条目替代）→ superseded，不和替代它的条目同时生效；
    - expires_at 已过 → expired；
    - 其他 → active（第 11.6 节）。停用前是 candidate 的也恢复为 active：恢复是用户的明确操作。
    """
    if child.superseded_by is not None:
        return "superseded"
    if child.expires_at is not None and child.expires_at <= now:
        return "expired"
    return "active"


def restore_child(session: Session, child_id: str, *, detail: str | None = None) -> MemoryChild:
    """恢复停用或忘记的条目：状态按 restore_status 定，清空 forgotten_at、embed_state=pending，写 restore 事件。

    只接受 disabled 的条目；其他状态抛 ValueError。恢复成 active 时若与已有生效条目内容相同，flush 会抛
    IntegrityError（部分唯一索引），调用方按第 11.4 节处理。
    """
    child = _require_child(session, child_id)
    if child.status != "disabled":
        raise ValueError(f"只有停用或忘记的记忆能恢复，当前状态：{child.status}")
    now = utcnow()
    child.status = restore_status(child, now)
    child.forgotten_at = None
    child.embed_state = "pending"
    child.updated_at = now
    session.flush()
    add_event(session, "restore", detail or f"恢复“{child.title}”为{RESTORE_LABELS[child.status]}。", [child.id])
    return child


# ---------- 维护记录 ----------

def add_event(session: Session, action: str, detail: str, block_ids: Sequence[str]) -> MemoryEvent:
    event = MemoryEvent(action=action, detail=detail, block_ids=list(block_ids), created_at=utcnow())
    session.add(event)
    session.flush()
    return event


def list_events(session: Session, *, limit: int = DEFAULT_EVENT_LIMIT, offset: int = 0) -> list[MemoryEvent]:
    """维护记录，新的在前；limit 限在 1..MAX_EVENT_LIMIT。"""
    limit = max(1, min(limit, MAX_EVENT_LIMIT))
    stmt = select(MemoryEvent).order_by(MemoryEvent.created_at.desc(), MemoryEvent.id.desc())
    return list(session.scalars(stmt.limit(limit).offset(max(0, offset))))
