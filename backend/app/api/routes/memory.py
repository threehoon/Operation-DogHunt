"""长期记忆的只读接口（开发文档 P3.3）。编辑、停用、忘记、恢复接口在 P6.4/P7 再加。

同步路由，由 FastAPI 放进线程池；读写都经 app.repositories.memory，这里不写 SQL。
数据库连不上、连接串无效或还没迁移时返回 503，detail 只说明原因类别，不带连接串和密码；脱敏后的原因写日志。
其他错误（数据校验、序列化等）不伪装成 503，照常是 500。
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import Session

from app.core import applog
from app.db.session import URL_ERRORS, _short_error, get_engine, session_scope
from app.repositories import memory as repo
from app.schemas.memory import (
    BlockStatus,
    MaintenanceEntryOut,
    MemoryChildOut,
    MemoryParentOut,
    MemoryTreeResponse,
)

router = APIRouter(prefix="/memory", tags=["memory"])
logger = logging.getLogger("app.memory")

UNAVAILABLE_DETAIL = "记忆库暂时不可用：数据库连不上或还没有迁移（make db-migrate）。详情见 /api/v1/health/storage。"


def _unavailable(exc: BaseException) -> HTTPException:
    reason = _short_error(exc)  # 与 /health/storage 同一套：类名 + 首行，已脱敏
    applog.event(logger, "memory.db", f"读取记忆失败：{reason}", level=logging.WARNING, reason=reason)
    return HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL)


@contextmanager
def _read_session() -> Iterator[Session]:
    """只读会话；只把“数据库不可用”类错误转成 503。"""
    try:
        get_engine()  # 连接串格式错误在这里抛 ArgumentError/ValueError，和后面的数据校验 ValueError 分开
    except URL_ERRORS as exc:
        raise _unavailable(exc) from None
    try:
        with session_scope() as session:
            yield session
    except repo.DATABASE_UNAVAILABLE_ERRORS as exc:
        raise _unavailable(exc) from None


@router.get("", response_model=MemoryTreeResponse, summary="列出记忆父块、子块与维护记录")
def list_memory(
    include_forgotten: Annotated[bool, Query(description="是否包含已忘记（forgotten_at 非空）的条目")] = False,
    status: Annotated[list[BlockStatus] | None, Query(description="只看这些状态，可重复：?status=active&status=candidate")] = None,
    parent_id: Annotated[str | None, Query(max_length=36, description="只看这个父块的子块")] = None,
    limit: Annotated[int, Query(ge=1, le=repo.MAX_CHILD_LIMIT, description="子块分页大小")] = repo.DEFAULT_CHILD_LIMIT,
    offset: Annotated[int, Query(ge=0, description="子块分页偏移")] = 0,
    log_limit: Annotated[int, Query(ge=1, le=repo.MAX_EVENT_LIMIT, description="维护记录条数")] = repo.DEFAULT_EVENT_LIMIT,
) -> MemoryTreeResponse:
    """返回 `{parents, children, log}`，形状与前端 demoMemory() 一致。

    - parents：固定目录的全部父块，按目录顺序；不分页、不受过滤影响。
    - children：默认不含已忘记的条目（停用的仍在）；可用 include_forgotten、status、parent_id 过滤；
      按 updatedAt 新的在前排序，limit（默认 200，最大 500）/ offset 分页。
    - log：维护记录，新的在前，最多 log_limit 条（默认 50，最大 200）。
    空库返回三个空数组；数据库不可用返回 503。
    """
    with _read_session() as session:
        parents = repo.list_parents(session)
        children = repo.list_children(session, parent_id=parent_id, statuses=status,
                                      include_forgotten=include_forgotten, limit=limit, offset=offset)
        events = repo.list_events(session, limit=log_limit)
        return MemoryTreeResponse(
            parents=[MemoryParentOut.from_row(row) for row in parents],
            children=[MemoryChildOut.from_row(row) for row in children],
            log=[MaintenanceEntryOut.from_row(row) for row in events],
        )


@router.get("/children/{child_id}", response_model=MemoryChildOut, summary="查看一条记忆子块")
def get_memory_child(child_id: str) -> MemoryChildOut:
    """按 id 返回一条子块；已忘记的也能查到（forgottenAt 非空）。不存在返回 404。"""
    with _read_session() as session:
        row = repo.get_child(session, child_id)
        child = MemoryChildOut.from_row(row) if row is not None else None
    if child is None:
        raise HTTPException(status_code=404, detail="没有找到这条记忆；它可能已被永久删除，或 id 写错了。")
    return child
