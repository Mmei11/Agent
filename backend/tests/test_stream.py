"""Phase 6 L1：流式接口的事件顺序、错误路径与状态一致性。案例均为虚构。"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app
from backend.app.observability.instrument import SAFE_KEYS

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    """流式测试不写入本地演示库。"""
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "consultations.sqlite"))
    routes.reset_app()
    yield
    routes.reset_app()


@pytest.fixture(autouse=True)
def fake_knowledge_tool(monkeypatch):
    """不依赖知识库与外部模型。"""
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


def read_events(response) -> list[dict]:
    """把 SSE 帧解析成 [{event, data}]；心跳注释行直接忽略。"""
    events: list[dict] = []
    current: dict = {}
    for line in response.iter_lines():
        if line.startswith("event:"):
            current["event"] = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current["data"] = json.loads(line[len("data:") :].strip())
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


def stream(client_: TestClient, session_id: str, message: str):
    return client_.post(f"/sessions/{session_id}/stream", json={"message": message})


def test_stream_event_order_and_result_consistency():
    """事件顺序固定：meta → node(start/end)* → result，且结果与 GET 一致。"""
    sid = client.post("/sessions", json={"initial_message": ""}).json()["session_id"]

    resp = stream(client, sid, "头部不适")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = read_events(resp)
    assert events[0]["event"] == "meta"
    assert events[-1]["event"] == "result"

    meta = events[0]["data"]
    assert meta["session_id"] == sid
    assert meta["turn_id"] == 1
    assert meta["trace_id"]

    # 每个业务节点都成对出现 start / end；debug 模式里的 checkpoint 事件不对外。
    starts = [e["data"]["node"] for e in events if e["event"] == "node" and e["data"]["phase"] == "start"]
    ends = [e["data"]["node"] for e in events if e["event"] == "node" and e["data"]["phase"] == "end"]
    assert starts == ends
    assert {"intake", "risk_check", "decision", "followup"} <= set(starts)
    assert set(starts) <= routes.STREAM_NODES

    # 节点摘要只含白名单字段，不把完整 state 或用户原话推给前端。
    for event in events:
        if event["event"] == "node" and event["data"]["phase"] == "end":
            assert set(event["data"]["summary"]) <= set(SAFE_KEYS)

    result = events[-1]["data"]
    assert result["trace_id"] == meta["trace_id"]
    assert result["collected_facts"]

    # 流式与查询接口读到的是同一份 checkpoint。
    state = client.get(f"/sessions/{sid}").json()
    assert result["collected_facts"] == state["collected_facts"]
    assert result["asked_questions"] == state["asked_questions"]
    assert result["followup_count"] == state["followup_count"]


def test_stream_risk_path():
    """风险输入同样走流式：结果状态是 risk_route。"""
    sid = client.post("/sessions", json={"initial_message": ""}).json()["session_id"]

    events = read_events(stream(client, sid, "模拟人物突然呼吸困难"))
    result = events[-1]["data"]
    assert result["final_result"]["status"] == "risk_route"
    assert result["risk_flags"]


def test_stream_unknown_session_404():
    assert stream(client, "missing-session", "头部不适").status_code == 404


def test_stream_empty_message_400():
    sid = client.post("/sessions", json={"initial_message": ""}).json()["session_id"]
    assert stream(client, sid, "   ").status_code == 400


def test_stream_finished_session_409():
    sid = client.post(
        "/sessions",
        json={"initial_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史"},
    ).json()["session_id"]

    assert stream(client, sid, "再补充一句").status_code == 409


def test_send_message_still_works():
    """旧同步接口保持原契约，不被流式改造影响。"""
    sid = client.post("/sessions", json={"initial_message": ""}).json()["session_id"]
    resp = client.post(f"/sessions/{sid}/messages", json={"message": "头部不适"})
    assert resp.status_code == 200
    assert resp.json()["collected_facts"]
