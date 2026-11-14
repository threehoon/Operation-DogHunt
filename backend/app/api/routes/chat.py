import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.core.applog import event
from app.core.config import Settings, get_settings
from app.integrations.llm import ModelError
from app.schemas.chat import ChatRequest, ChatResponse, ModelStatus
from app.services.chat.service import chat, prepare_stream

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def _rejected(error: ModelError) -> HTTPException:
    """以 HTTP 状态码返回的失败（忙、未配置、选场失效、提示词损坏，或非流式接口的模型错误）也记一条，页面上能看到原因。"""
    event(logger, "chat.rejected", f"聊天请求失败：{error}", level=logging.WARNING, error=error, status=error.status_code)
    return HTTPException(status_code=error.status_code, detail=str(error))


@router.get("/model", response_model=ModelStatus)
async def model_status(settings: Annotated[Settings, Depends(get_settings)]) -> ModelStatus:
    # 仅检查配置是否齐全，不请求中转站、不消耗额度，也不返回认证信息。
    return ModelStatus(configured=settings.model_configured, model=settings.model_name or None, timeout_seconds=settings.model_timeout_seconds)


@router.post("/chat", response_model=ChatResponse)
async def send_chat(request: ChatRequest, settings: Annotated[Settings, Depends(get_settings)]) -> ChatResponse:
    try:
        return await chat(request, settings)
    except ModelError as error:
        raise _rejected(error) from None


@router.post("/chat/stream")
async def stream_chat(request: ChatRequest, settings: Annotated[Settings, Depends(get_settings)]) -> StreamingResponse:
    """逐段推送回答（SSE）。开始推送前的错误仍用 HTTP 状态码返回，之后的错误以 error 事件返回。"""
    try:
        events = await prepare_stream(request, settings)
    except ModelError as error:
        raise _rejected(error) from None
    return StreamingResponse(events, media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
