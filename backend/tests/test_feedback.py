"""Phase 6 L4：反馈存储、评分与追问选项。案例均为虚构。"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app
from backend.app.feedback import scores, store

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_feedback(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "feedback.sqlite"))
    monkeypatch.setenv("SCORE_LOG_PATH", str(tmp_path / "scores.jsonl"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    routes.reset_app()
    scores.reset_client()
    yield
    routes.reset_app()
    scores.reset_client()


@pytest.fixture(autouse=True)
def fake_knowledge_tool(monkeypatch):
    from backend.app.agent import graph as graph_module
    from backend.app.tools.contract import ToolResult

    monkeypatch.setattr(
        graph_module,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True,
            name=name,
            data={"department": "神经内科", "reason": None, "hits": []},
            latency_ms=1,
        ),
    )


def _turn(message: str = "头部不适") -> tuple[str, dict]:
    session_id = client.post(
        "/sessions", json={"initial_message": ""}
    ).json()["session_id"]
    response = client.post(
        f"/sessions/{session_id}/messages", json={"message": message}
    )
    assert response.status_code == 200
    return session_id, response.json()


def _score_rows(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_feedback_saves_and_emits_score(tmp_path):
    session_id, state = _turn()
    assert state["turn_id"] == 1

    response = client.post(
        f"/sessions/{session_id}/feedback",
        json={"rating": "helpful", "reason": "信息整理得清楚"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "session_id": session_id,
        "turn_id": 1,
        "rating": "helpful",
        "saved": True,
    }

    rows = store.list_for_session(session_id)
    assert len(rows) == 1
    assert rows[0]["rating"] == "helpful"
    assert rows[0]["trace_id"] == state["trace_id"]

    scores_rows = _score_rows(tmp_path / "scores.jsonl")
    assert len(scores_rows) == 1
    assert scores_rows[0]["name"] == "user_feedback"
    assert scores_rows[0]["value"] == 1
    assert scores_rows[0]["trace_id"] == state["trace_id"]
    assert scores_rows[0]["session_id"] == session_id


def test_feedback_upserts_same_turn(tmp_path):
    session_id, _ = _turn()

    for rating in ("helpful", "unhelpful"):
        response = client.post(
            f"/sessions/{session_id}/feedback", json={"rating": rating}
        )
        assert response.status_code == 200

    rows = store.list_for_session(session_id)
    assert len(rows) == 1
    assert rows[0]["rating"] == "unhelpful"
    assert store.stats()["total"] == 1
    assert store.stats()["unhelpful"] == 1


def test_feedback_empty_session_409():
    session_id = client.post(
        "/sessions", json={"initial_message": ""}
    ).json()["session_id"]

    response = client.post(
        f"/sessions/{session_id}/feedback", json={"rating": "helpful"}
    )
    assert response.status_code == 409


def test_feedback_unknown_session_404():
    response = client.post(
        "/sessions/not-exist/feedback", json={"rating": "helpful"}
    )
    assert response.status_code == 404


def test_feedback_invalid_rating_422():
    session_id, _ = _turn()
    response = client.post(
        f"/sessions/{session_id}/feedback", json={"rating": "maybe"}
    )
    assert response.status_code == 422


def test_feedback_reason_is_redacted_in_store_and_score(tmp_path):
    session_id, _ = _turn()
    client.post(
        f"/sessions/{session_id}/feedback",
        json={"rating": "unhelpful", "reason": "电话 13800138000 打不通"},
    )

    rows = store.list_for_session(session_id)
    assert "13800138000" not in rows[0]["reason"]

    content = (tmp_path / "scores.jsonl").read_text(encoding="utf-8")
    assert "13800138000" not in content


def test_suggested_replies_follow_the_question():
    session_id, first = _turn("头部不适")
    # 第一轮追问持续时间，选项应该是时间范围。
    assert first["suggested_replies"] == ["一天内", "两三天", "一周以上"]

    second = client.post(
        f"/sessions/{session_id}/messages", json={"message": "前两天开始的"}
    ).json()
    # 第二轮应换成下一字段的选项，且不是上一轮的残留。
    assert second["suggested_replies"]
    assert second["suggested_replies"] != first["suggested_replies"]
