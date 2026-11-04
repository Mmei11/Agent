"""Phase 6 L2：并发下不同会话互不污染。案例均为虚构。"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app

client = TestClient(app)

# (用户原话, 该会话里必须出现、且不能出现在别人会话里的关键词)
# 关键词模式只识别有限词表，这里选能稳定抽取的症状。
CASES = [
    ("头部不适", "头部"),
    ("腹痛两天", "腹痛"),
    ("咳嗽三天", "咳嗽"),
    ("反复发热", "发热"),
    ("头晕", "头晕"),
]
TOKENS = [token for _, token in CASES]


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSULTATION_DB_PATH", str(tmp_path / "concurrency.sqlite"))
    routes.reset_app()
    yield
    routes.reset_app()


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
            data={"department": "全科", "reason": None, "hits": []},
            latency_ms=1,
        ),
    )


def _run_one(message: str) -> dict:
    session_id = client.post("/sessions", json={"initial_message": ""}).json()["session_id"]
    response = client.post(f"/sessions/{session_id}/messages", json={"message": message})
    assert response.status_code == 200
    return response.json()


def test_parallel_sessions_are_isolated():
    """多个会话并发写入：每个会话只看到自己的症状。"""
    with ThreadPoolExecutor(max_workers=len(CASES)) as pool:
        results = list(pool.map(_run_one, [message for message, _ in CASES]))

    for (_, token), data in zip(CASES, results):
        facts = str(data["collected_facts"])
        assert token in facts, f"{token} 没有进入自己的会话"
        leaked = [other for other in TOKENS if other != token and other in facts]
        assert not leaked, f"{token} 的会话混入了 {leaked}"


def test_parallel_turns_have_distinct_trace_ids():
    """并发轮次各自生成 trace，不共享上下文。"""

    def run(_: int) -> str:
        return _run_one("头部不适")["trace_id"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        trace_ids = list(pool.map(run, range(6)))

    assert len(set(trace_ids)) == len(trace_ids)
    assert all(trace_ids)
