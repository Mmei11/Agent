"""工具运行时：重试与熔断。

只重试瞬时失败（timeout / unavailable），不重试参数错误；
非幂等工具不重试。连续失败到阈值后熔断，冷却期内直接返回结构化失败。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

from backend.app.tools.breaker import CircuitBreaker
from backend.app.tools.contract import ToolResult, ToolSpec

TRANSIENT_ERRORS = {"timeout", "unavailable"}

_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKER_LOCK = threading.Lock()


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def reset_tool_breakers() -> None:
    with _BREAKER_LOCK:
        _BREAKERS.clear()


def breaker_from_env() -> CircuitBreaker:
    """按同一组环境变量创建熔断器，工具层与会话层共用。"""
    return CircuitBreaker(
        threshold=_env_int("TOOL_BREAKER_THRESHOLD", 3),
        cooldown_s=_env_float("TOOL_BREAKER_COOLDOWN_S", 30.0),
    )


def _breaker(name: str) -> CircuitBreaker:
    with _BREAKER_LOCK:
        breaker = _BREAKERS.get(name)
        if breaker is None:
            breaker = breaker_from_env()
            _BREAKERS[name] = breaker
        return breaker


def call_with_reliability(
    spec: ToolSpec,
    params: dict,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> ToolResult:
    breaker = _breaker(spec.name)
    if not breaker.allow():
        return ToolResult(
            ok=False,
            name=spec.name,
            error="工具暂时不可用（熔断中）",
            error_kind="unavailable",
        )

    attempts = max(1, _env_int("TOOL_MAX_ATTEMPTS", 2))
    backoff_s = _env_int("TOOL_RETRY_BACKOFF_MS", 100) / 1000
    last: ToolResult | None = None

    for attempt in range(attempts):
        result = spec.call(params)
        if result.ok:
            breaker.record_success()
            return result

        last = result
        if result.error_kind not in TRANSIENT_ERRORS:
            # 参数错误是调用方问题，重试无意义，也不该拉高熔断计数。
            return result

        breaker.record_failure()
        if not spec.idempotent:
            return result
        if attempt + 1 >= attempts or not breaker.allow():
            break
        if backoff_s:
            sleep(backoff_s * (2**attempt))

    return last or ToolResult(
        ok=False, name=spec.name, error="工具调用失败", error_kind="unavailable"
    )
