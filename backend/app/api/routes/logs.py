"""调用日志查询：只读、只接受本机请求。日志本身不经过这里写入，这里的请求也不记进日志。"""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.services.logs import reader

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def local_only(request: Request) -> None:
    # 后端默认只监听 127.0.0.1；这里兜底，以后改成 0.0.0.0 时日志也不会被局域网读到。
    if request.client is None or request.client.host not in LOOPBACK:
        raise HTTPException(status_code=403, detail="调用日志只允许在本机查看。")


router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[Depends(local_only)])


@router.get("/traces")
async def list_traces(
    days: Annotated[int, Query(ge=1, le=reader.MAX_DAYS)] = 1,
    kind: Literal["chat", "odds_refresh", "task", "api"] | None = None,
    problems_only: bool = False,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    # 读文件放进线程，不阻塞正在进行的聊天。
    return await asyncio.to_thread(reader.list_traces, days, kind, problems_only, q, cursor, limit)


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict:
    result = await asyncio.to_thread(reader.get_trace, trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail="没有找到这个调用编号；日志只保留 14 天。")
    return result
