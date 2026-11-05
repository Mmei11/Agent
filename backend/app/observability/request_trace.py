"""Phase 6 L3.4：请求级 trace。

认证、限流、会话锁发生在图执行之前，原来没有 trace 上下文，
这些闸门的 span 无处可落。这里给 /sessions* 请求开一条根 trace，
图执行时再用 parent.trace_id 把 turn trace 关联回来。
"""

import re

from backend.app.observability.tracer import start_trace

_SESSION_PATH = re.compile(r"^/sessions/([^/]+)")


class RequestTraceMiddleware:
    """纯 ASGI 中间件，不缓冲响应，SSE 不受影响。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        is_session_path = path == "/sessions" or path.startswith("/sessions/")
        if scope.get("type") != "http" or not is_session_path:
            return await self.app(scope, receive, send)

        match = _SESSION_PATH.match(path)
        session_id = match.group(1) if match else ""

        with start_trace(session_id, turn_id=0, root_name="http.request"):
            await self.app(scope, receive, send)
