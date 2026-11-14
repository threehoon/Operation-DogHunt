"""给每个 HTTP 请求一个调用编号，放进响应头 X-Trace-Id，结束时记一条 http.request（含流式回答传完为止的耗时）。"""

import logging
from time import monotonic

from app.core.applog import event, new_trace_id, trace

logger = logging.getLogger("app.http")
# 日志页自己的请求不记，避免开着自动刷新时把日志刷满。
SKIP_PREFIXES = ("/api/v1/logs",)


class TraceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return
        trace_id = new_trace_id()
        started = monotonic()
        status: int | None = None
        finished = False

        async def send_with_id(message):
            nonlocal status, finished
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {**message, "headers": [*message.get("headers", []), (b"x-trace-id", trace_id.encode())]}
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = True
            await send(message)

        with trace(trace_id):
            error: BaseException | None = None
            try:
                await self.app(scope, receive, send_with_id)
            except Exception as exc:
                error = exc
                raise
            finally:
                ms = round((monotonic() - started) * 1000)
                # 网页中途关闭或流被取消时，响应体没有发完。
                outcome = "error" if error else "done" if finished else "disconnected"
                code = status or 500
                level = logging.ERROR if error or code >= 500 else logging.WARNING if code >= 400 or outcome == "disconnected" else logging.INFO
                note = "，网页中途断开" if outcome == "disconnected" else ""
                event(logger, "http.request", f"{scope['method']} {scope['path']} → {status or '未响应'}（{ms} ms{note}）",
                      level=level, error=error, method=scope["method"], path=scope["path"], status=status, ms=ms, outcome=outcome)
