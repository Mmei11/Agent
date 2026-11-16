"""Phase E：有界输出审查者。"""

import pytest

from backend.app.agent.critic import CriticVerdict, FALLBACK_TEXT, critic
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError, LLMResult
from backend.app.tools.contract import ToolResult


def _usage() -> LLMResult:
    return LLMResult(
        content="",
        model="qwen-test",
        input_tokens=10,
        output_tokens=5,
        latency_ms=12,
        cost_estimate=None,
    )


def _state(text: str = "建议就诊方向：神经内科。", **updates) -> ConsultationState:
    updates.setdefault("latest_user_message", "头部不适两天")
    updates.setdefault(
        "final_result",
        {"status": "completed", "next_step": "建议就诊方向：神经内科"},
    )
    updates.setdefault("rendered_text", text)
    return ConsultationState(**updates)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("CRITIC_ENABLED", "1")
    monkeypatch.setenv("USE_MODEL", "1")


def test_critic_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CRITIC_ENABLED", raising=False)

    assert critic(_state()) == {}


def test_critic_blocks_unsupported_pmid(monkeypatch, enabled):
    state = _state("研究显示该药有效。PMID: 40000001")

    result = critic(state)

    assert result["final_result"]["status"] == "error"
    assert result["rendered_text"] == FALLBACK_TEXT
    assert result["trace"][0]["reasons"] == ["fabricated_evidence"]


def test_critic_allows_pmid_present_in_tool_results(monkeypatch, enabled):
    monkeypatch.setattr(
        "backend.app.agent.critic.chat_structured",
        lambda *args, **kwargs: (
            CriticVerdict(verdict="pass", confidence=0.9),
            _usage(),
        ),
    )
    state = _state(
        "研究显示该药有效。PMID: 40000001",
        tool_results={"pubmed_search_articles": {"text": "PMID: 40000001"}},
    )

    result = critic(state)

    assert result["trace"][0]["verdict"] == "pass"
    assert "final_result" not in result


def test_critic_blocks_on_model_verdict(monkeypatch, enabled):
    monkeypatch.setattr(
        "backend.app.agent.critic.chat_structured",
        lambda *args, **kwargs: (
            CriticVerdict(
                verdict="block",
                reasons=["boundary"],
                hint="删除确诊句",
                confidence=0.9,
            ),
            _usage(),
        ),
    )

    result = critic(_state("你这就是偏头痛，吃布洛芬 400mg。"))

    assert result["final_result"]["status"] == "error"
    assert result["rendered_text"] == FALLBACK_TEXT
    assert result["trace"][0]["blocked"] is True
    assert result["trace"][0]["reasons"] == ["boundary"]


def test_critic_low_confidence_block_does_not_replace(monkeypatch, enabled):
    monkeypatch.setattr(
        "backend.app.agent.critic.chat_structured",
        lambda *args, **kwargs: (
            CriticVerdict(verdict="block", reasons=["boundary"], confidence=0.2),
            _usage(),
        ),
    )

    state = _state("建议就诊方向：神经内科。")
    result = critic(state)

    assert "final_result" not in result
    assert result["trace"][0]["low_confidence_block"] is True


def test_critic_fails_open_on_llm_error(monkeypatch, enabled):
    def _fail(*args, **kwargs):
        raise LLMError("timeout", retryable=True)

    monkeypatch.setattr("backend.app.agent.critic.chat_structured", _fail)

    result = critic(_state())

    assert "final_result" not in result
    assert result["trace"][0]["error"]


def test_critic_skips_non_completed_result(monkeypatch, enabled):
    state = _state(final_result={"status": "risk_route"})

    assert critic(state) == {}


def test_critic_skips_general_answer(monkeypatch, enabled):
    state = _state(
        trace=[{"node": "answer", "router_intent": "general"}],
    )

    assert critic(state) == {}


def test_graph_invokes_critic_after_output(monkeypatch):
    from backend.app.agent import graph as graph_module

    calls = []

    def fake_critic(state):
        calls.append(state.rendered_text)
        return {"trace": [{"node": "critic", "verdict": "pass"}]}

    monkeypatch.setattr(graph_module, "critic", fake_critic)
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

    app = graph_module.build_graph()
    result = app.invoke(
        {"latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史"}
    )

    assert calls
    assert [entry["node"] for entry in result["trace"]][-1] == "critic"

