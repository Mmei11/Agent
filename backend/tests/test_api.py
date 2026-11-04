"""Day 14 API 接口测试：创建、发消息、查看、删除。案例均为虚构。"""

from fastapi.testclient import TestClient
import pytest

from backend.app.api import routes
from backend.app.api.routes import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """API 测试不写入本地演示用的 consultations.sqlite。"""
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "consultations.sqlite"))
    routes.reset_app()
    yield
    routes.reset_app()


@pytest.fixture(autouse=True)
def fake_knowledge_tool(monkeypatch):
    """接口测试不依赖知识库与外部模型；需要别的行为时在用例里再覆盖。"""
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


def test_create_session_empty():
    """创建空会话：返回 session_id 和空状态。"""
    resp = client.post("/sessions", json={"initial_message": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert data["collected_facts"] == {}
    assert data["followup_count"] == 0
    sid = data["session_id"]
    assert client.get(f"/sessions/{sid}").status_code == 200
    assert client.post(f"/sessions/{sid}/messages", json={"message": "头部不适"}).status_code == 200


def test_create_session_with_message():
    """创建会话并带初始消息：Agent 处理后返回状态。"""
    resp = client.post("/sessions", json={"initial_message": "头部不适"})
    assert resp.status_code == 200
    data = resp.json()
    assert "头部不适" in str(data["collected_facts"])
    assert len(data["asked_questions"]) > 0


def test_send_message_continues_session():
    """发送消息继续会话：第二次消息能看到第一次的信息。"""
    # 创建会话
    create = client.post("/sessions", json={"initial_message": "头部不适"})
    sid = create.json()["session_id"]

    # 发送第二条消息
    resp = client.post(f"/sessions/{sid}/messages", json={"message": "前两天开始的"})
    assert resp.status_code == 200
    data = resp.json()
    # 两轮信息都在
    assert "头部不适" in str(data["collected_facts"])
    assert "两天" in str(data["collected_facts"])


def test_send_empty_message_rejected():
    """空消息：返回 400 错误。"""
    create = client.post("/sessions", json={"initial_message": "头部不适"})
    sid = create.json()["session_id"]

    resp = client.post(f"/sessions/{sid}/messages", json={"message": "   "})
    assert resp.status_code == 400


def test_get_session_returns_state():
    """查看会话状态：不处理消息，返回当前状态。"""
    create = client.post("/sessions", json={"initial_message": "头部不适两天"})
    sid = create.json()["session_id"]

    resp = client.get(f"/sessions/{sid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == sid
    assert "头部不适" in str(data["collected_facts"])


def test_get_unknown_session_404():
    """查看不存在的会话：返回 404。"""
    resp = client.get("/sessions/nonexistent-id")
    assert resp.status_code == 404


def test_delete_session():
    """删除会话：返回删除确认。"""
    create = client.post("/sessions", json={"initial_message": ""})
    sid = create.json()["session_id"]

    resp = client.delete(f"/sessions/{sid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    assert client.get(f"/sessions/{sid}").status_code == 404


def test_risk_path_through_api():
    """风险输入通过 API：走固定风险分支。"""
    resp = client.post("/sessions", json={"initial_message": "模拟人物突然呼吸困难"})
    assert resp.status_code == 200
    data = resp.json()
    assert "R001" in data["risk_flags"]


def test_declining_followup_finishes_without_another_question():
    created = client.post("/sessions", json={"initial_message": "头部不适"}).json()
    sid = created["session_id"]

    response = client.post(
        f"/sessions/{sid}/messages", json={"message": "拒绝补充更多信息"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["final_result"]["status"] == "completed"
    assert data["followup_count"] == 1
    assert len(data["asked_questions"]) == 1


def test_tool_timeout_returns_structured_error(monkeypatch):
    from backend.app.agent import graph as graph_module
    from backend.app.tools.contract import ToolResult

    monkeypatch.setattr(
        graph_module,
        "call_tool",
        lambda name, params: ToolResult(
            ok=False, name=name, error="执行超过 5.0s", error_kind="timeout"
        ),
    )
    response = client.post(
        "/sessions",
        json={
            "initial_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史"
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["final_result"]["status"] == "error"
    assert "执行超过" in data["final_result"]["next_step"]
    assert "执行超过" in data["rendered_text"]


def test_risk_session_is_terminal_and_new_case_is_independent():
    risk = client.post("/sessions", json={"initial_message": "模拟人物突然呼吸困难"}).json()
    risk_id = risk["session_id"]
    assert risk["final_result"]["status"] == "risk_route"
    assert client.post(f"/sessions/{risk_id}/messages", json={"message": "头部不适"}).status_code == 409

    normal = client.post("/sessions", json={"initial_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史"}).json()
    assert normal["session_id"] != risk_id
    assert normal["risk_flags"] == []
    assert normal["final_result"]["status"] == "completed"
    assert len(normal["conversation"]) == 2


def test_conversation_restores_from_sqlite():
    created = client.post("/sessions", json={"initial_message": "头部不适"}).json()
    sid = created["session_id"]
    assert created["final_result"] == {}
    assert [item["role"] for item in created["conversation"]] == ["user", "agent"]

    updated = client.post(f"/sessions/{sid}/messages", json={"message": "前两天开始的"}).json()
    restored = client.get(f"/sessions/{sid}").json()
    assert restored["conversation"] == updated["conversation"]
    assert [item["role"] for item in restored["conversation"]] == ["user", "agent", "user", "agent"]
