"""Phase F：工具重试与熔断。"""

import pytest
from pydantic import BaseModel

from backend.app.tools.contract import ToolSpec
from backend.app.tools.runtime import call_with_reliability, reset_tool_breakers


class _Params(BaseModel):
    query: str = ""


class _IntParams(BaseModel):
    count: int


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setenv("TOOL_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("TOOL_RETRY_BACKOFF_MS", "0")
    monkeypatch.setenv("TOOL_BREAKER_THRESHOLD", "3")
    monkeypatch.setenv("TOOL_BREAKER_COOLDOWN_S", "30")
    reset_tool_breakers()
    yield
    reset_tool_breakers()


def _spec(handler, *, idempotent=True, params_model=_Params):
    return ToolSpec(
        name="demo",
        description="demo",
        params_model=params_model,
        handler=handler,
        idempotent=idempotent,
    )


def test_retries_transient_failure_then_succeeds():
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("第一次超时")
        return {"ok": True}

    result = call_with_reliability(_spec(handler), {"query": "头痛"})

    assert result.ok
    assert calls["n"] == 2


def test_invalid_params_are_not_retried():
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        return {"ok": True}

    result = call_with_reliability(
        _spec(handler, params_model=_IntParams), {"count": "not-an-int"}
    )

    assert not result.ok
    assert result.error_kind == "invalid_params"
    assert calls["n"] == 0


def test_non_idempotent_tool_is_not_retried():
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        raise TimeoutError("超时")

    result = call_with_reliability(
        _spec(handler, idempotent=False), {"query": "头痛"}
    )

    assert not result.ok
    assert result.error_kind == "timeout"
    assert calls["n"] == 1


def test_breaker_opens_after_threshold(monkeypatch):
    monkeypatch.setenv("TOOL_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("TOOL_BREAKER_THRESHOLD", "2")
    calls = {"n": 0}

    def handler(params):
        calls["n"] += 1
        raise RuntimeError("服务不可用")

    spec = _spec(handler)
    assert call_with_reliability(spec, {"query": "a"}).error_kind == "unavailable"
    assert call_with_reliability(spec, {"query": "b"}).error_kind == "unavailable"

    blocked = call_with_reliability(spec, {"query": "c"})

    assert "熔断" in (blocked.error or "")
    assert calls["n"] == 2


def test_backoff_between_attempts(monkeypatch):
    monkeypatch.setenv("TOOL_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("TOOL_RETRY_BACKOFF_MS", "100")
    sleeps = []

    def handler(params):
        raise TimeoutError("超时")

    call_with_reliability(
        _spec(handler), {"query": "头痛"}, sleep=sleeps.append
    )

    assert sleeps == [0.1]
