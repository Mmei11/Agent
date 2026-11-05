"""Phase 6 L3.3：体积边界、会话在途锁与流计数。案例均为虚构。"""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app.api import inflight, routes
from backend.app.api.routes import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "limits.sqlite"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("MAX_MESSAGE_CHARS", "2000")
    monkeypatch.setenv("MAX_BODY_BYTES", "32768")
    monkeypatch.setenv("MAX_STREAMS_PER_CLIENT", "5")
    routes.reset_app()
    inflight.reset()
    yield
    inflight.reset()
    routes.reset_app()


def _create_session() -> str:
    return client.post("/sessions", json={"initial_message": ""}).json()["session_id"]


class SlowTurn:
    """让同步轮次卡住，方便制造并发重叠。"""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.both_entered = threading.Event()
        self.release = threading.Event()
        self._active = 0
        self._lock = threading.Lock()

    def __call__(self, session_id: str, message: str) -> dict:
        with self._lock:
            self._active += 1
            if self._active >= 2:
                self.both_entered.set()
        self.entered.set()
        self.release.wait(timeout=5)
        with self._lock:
            self._active -= 1
        return {"session_id": session_id, "collected_facts": {"主要症状": [message]}}


# --- 体积与消息长度 ---


def test_message_too_long_422(monkeypatch):
    monkeypatch.setenv("MAX_MESSAGE_CHARS", "5")

    assert client.post("/sessions", json={"initial_message": "123456"}).status_code == 422

    session_id = _create_session()
    assert (
        client.post(
            f"/sessions/{session_id}/messages", json={"message": "123456"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/sessions/{session_id}/stream", json={"message": "123456"}
        ).status_code
        == 422
    )


def test_body_too_large_413(monkeypatch):
    monkeypatch.setenv("MAX_BODY_BYTES", "100")

    response = client.post("/sessions", json={"initial_message": "x" * 200})
    assert response.status_code == 413
    assert response.json()["detail"] == "请求体过大"


# --- 会话在途锁 ---


def test_same_session_concurrent_409(monkeypatch):
    turn = SlowTurn()
    monkeypatch.setattr(routes, "_run_turn_sync", turn)
    session_id = _create_session()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.post,
            f"/sessions/{session_id}/messages",
            json={"message": "头部不适"},
        )
        assert turn.entered.wait(timeout=2)

        second = client.post(
            f"/sessions/{session_id}/messages", json={"message": "两天了"}
        )
        assert second.status_code == 409

        turn.release.set()
        assert first.result(timeout=5).status_code == 200


def test_different_sessions_run_in_parallel(monkeypatch):
    turn = SlowTurn()
    monkeypatch.setattr(routes, "_run_turn_sync", turn)
    first_id = _create_session()
    second_id = _create_session()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.post, f"/sessions/{first_id}/messages", json={"message": "头部不适"}
        )
        second = pool.submit(
            client.post, f"/sessions/{second_id}/messages", json={"message": "腹痛"}
        )
        # 两个不同会话应该同时进入，而不是互相排队。
        assert turn.both_entered.wait(timeout=2)

        turn.release.set()
        assert first.result(timeout=5).status_code == 200
        assert second.result(timeout=5).status_code == 200


def test_delete_while_inflight_409(monkeypatch):
    turn = SlowTurn()
    monkeypatch.setattr(routes, "_run_turn_sync", turn)
    session_id = _create_session()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(
            client.post,
            f"/sessions/{session_id}/messages",
            json={"message": "头部不适"},
        )
        assert turn.entered.wait(timeout=2)

        assert client.delete(f"/sessions/{session_id}").status_code == 409

        turn.release.set()
        assert first.result(timeout=5).status_code == 200


def test_stream_releases_session_lock_after_finish():
    session_id = _create_session()

    response = client.post(
        f"/sessions/{session_id}/stream", json={"message": "头部不适"}
    )
    assert response.status_code == 200

    # 流结束后锁必须释放，否则下一轮永远 409。
    assert (
        client.post(
            f"/sessions/{session_id}/messages", json={"message": "两天了"}
        ).status_code
        == 200
    )


# --- SSE 连接计数 ---


def test_stream_counter_limits_and_releases():
    inflight.reset()

    assert inflight.try_open_stream("caller", limit=2) is True
    assert inflight.try_open_stream("caller", limit=2) is True
    assert inflight.try_open_stream("caller", limit=2) is False

    inflight.close_stream("caller")
    assert inflight.stream_count("caller") == 1
    assert inflight.try_open_stream("caller", limit=2) is True

    assert inflight.try_open_stream("other", limit=2) is True
    inflight.reset()


def test_stream_limit_returns_429(monkeypatch):
    monkeypatch.setenv("MAX_STREAMS_PER_CLIENT", "1")

    async def slow_stream(config, session_id, message, turn_id, queue, caller_id):
        try:
            await asyncio.sleep(0.4)
            queue.put_nowait(("__done__", {}))
        finally:
            inflight.leave(session_id)
            inflight.close_stream(caller_id)

    monkeypatch.setattr(routes, "_stream_turn", slow_stream)
    first_id = _create_session()
    second_id = _create_session()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(
            client.post, f"/sessions/{first_id}/stream", json={"message": "头部不适"}
        )

        deadline = time.time() + 2
        while time.time() < deadline and inflight.stream_count("ip:testclient") < 1:
            time.sleep(0.01)
        assert inflight.stream_count("ip:testclient") == 1

        second = client.post(
            f"/sessions/{second_id}/stream", json={"message": "腹痛"}
        )
        assert second.status_code == 429

        assert first.result(timeout=5).status_code == 200

    assert inflight.stream_count("ip:testclient") == 0
