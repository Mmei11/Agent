"""Phase 6 L3.3：同一会话在途锁与 SSE 连接计数。

两个进程内状态：
- 每个 session 同时只允许一轮问诊，防止两个请求读写同一份 checkpoint；
- 每个调用方同时打开的 SSE 流数量有上限，断线后后台任务仍计入，直到跑完。

多 worker / 多副本时每个进程各有一份，文档里写明不是全局约束。
"""

import hashlib
import os
import threading
from contextlib import contextmanager

from fastapi import HTTPException

from backend.app.config import load_env_file
from backend.app.observability.tracer import span
from backend.app.safety.audit import AuditEvent
from backend.app.safety.audit import write as write_audit

DEFAULT_MAX_STREAMS_PER_CLIENT = 5

_lock = threading.Lock()
_sessions: set[str] = set()
_streams: dict[str, int] = {}


def max_streams_per_client() -> int:
    load_env_file()
    raw = os.getenv("MAX_STREAMS_PER_CLIENT")
    try:
        return int(raw) if raw else DEFAULT_MAX_STREAMS_PER_CLIENT
    except ValueError:
        return DEFAULT_MAX_STREAMS_PER_CLIENT


def try_enter(session_id: str) -> bool:
    """占用会话；已经在途时返回 False。"""
    with _lock:
        if session_id in _sessions:
            return False
        _sessions.add(session_id)
        return True


def leave(session_id: str) -> None:
    with _lock:
        _sessions.discard(session_id)


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _audit_deny(rule: str, reason: str, identifier: str) -> None:
    write_audit(
        AuditEvent(
            layer="inflight",
            rule=rule,
            action="DENY",
            input_digest=_short_hash(identifier),
            detail=reason,
        )
    )


@contextmanager
def session_guard(session_id: str):
    """非流式接口用：with 块结束时自动释放。"""
    with span("api.session_lock", kind="gate") as record:
        record.attributes["session.id"] = _short_hash(session_id)
        if not try_enter(session_id):
            record.attributes["session.allowed"] = False
            _audit_deny("IN001", "session_busy", session_id)
            raise HTTPException(
                status_code=409,
                detail="上一轮还在处理，请稍后再试",
                headers={"Retry-After": "1"},
            )
        record.attributes["session.allowed"] = True
        try:
            yield
        finally:
            leave(session_id)


def enter_for_stream(session_id: str) -> bool:
    """流式接口占会话；span 与审计在这里统一处理。"""
    with span("api.session_lock", kind="gate") as record:
        record.attributes["session.id"] = _short_hash(session_id)
        record.attributes["session.stream"] = True
        allowed = try_enter(session_id)
        record.attributes["session.allowed"] = allowed
        if not allowed:
            _audit_deny("IN001", "session_busy", session_id)
        return allowed


def open_stream(caller_id: str, limit: int | None = None) -> bool:
    """占一个 SSE 连接名额；超限时写审计。"""
    with span("api.stream_slot", kind="gate") as record:
        record.attributes["caller.id"] = _short_hash(caller_id)
        allowed = try_open_stream(caller_id, limit)
        record.attributes["stream.allowed"] = allowed
        if not allowed:
            _audit_deny("IN002", "stream_limit", caller_id)
        return allowed


def try_open_stream(caller_id: str, limit: int | None = None) -> bool:
    """占用一个 SSE 连接名额；超限返回 False。"""
    limit = max_streams_per_client() if limit is None else limit
    with _lock:
        count = _streams.get(caller_id, 0)
        if count >= limit:
            return False
        _streams[caller_id] = count + 1
        return True


def close_stream(caller_id: str) -> None:
    with _lock:
        count = _streams.get(caller_id, 0)
        if count <= 1:
            _streams.pop(caller_id, None)
        else:
            _streams[caller_id] = count - 1


def stream_count(caller_id: str) -> int:
    with _lock:
        return _streams.get(caller_id, 0)


def in_flight_count() -> int:
    with _lock:
        return len(_sessions)


def reset() -> None:
    """测试用：清空所有在途状态。"""
    with _lock:
        _sessions.clear()
        _streams.clear()
