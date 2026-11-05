"""Phase 6 L3.3：请求体与消息长度边界。

顺序在认证之前：超大的 body 不应该先被解析、再被拒绝。
Content-Length 能直接判断；chunked 请求没有长度，就在读取时累计计数。
"""

import json
import os

from fastapi import HTTPException

from backend.app.config import load_env_file

DEFAULT_MAX_BODY_BYTES = 32 * 1024
DEFAULT_MAX_MESSAGE_CHARS = 2000


class BodyTooLarge(Exception):
    """收到超过上限的请求体时，从 receive 里抛出，由中间件转成 413。"""


def max_body_bytes() -> int:
    load_env_file()
    raw = os.getenv("MAX_BODY_BYTES")
    try:
        return int(raw) if raw else DEFAULT_MAX_BODY_BYTES
    except ValueError:
        return DEFAULT_MAX_BODY_BYTES


def max_message_chars() -> int:
    load_env_file()
    raw = os.getenv("MAX_MESSAGE_CHARS")
    try:
        return int(raw) if raw else DEFAULT_MAX_MESSAGE_CHARS
    except ValueError:
        return DEFAULT_MAX_MESSAGE_CHARS


def check_message_length(message: str) -> None:
    """业务层第二道边界：合法 body 里也不允许超长消息。"""
    limit = max_message_chars()
    if len(message) > limit:
        raise HTTPException(
            status_code=422,
            detail=f"消息不能超过 {limit} 字",
        )


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """纯 ASGI 中间件：不缓冲响应，避免影响 SSE 流式输出。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        limit = max_body_bytes()
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")

        if raw_length is not None:
            try:
                declared = int(raw_length)
            except ValueError:
                declared = None
            if declared is not None and declared > limit:
                await _send_json(send, 413, "请求体过大")
                return

        received = 0
        response_started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            # 响应已经开始就无法再改状态码；否则返回标准 413。
            if not response_started:
                await _send_json(send, 413, "请求体过大")
            else:
                raise
