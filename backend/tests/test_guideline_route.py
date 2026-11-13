"""Phase C：就医准备问题的确定性工具路径。"""

import pytest

from backend.app.agent import opening
from backend.app.agent.answer import answer_followup
from backend.app.agent.general import is_guideline_question
from backend.app.agent.opening import answer_guideline, route_general
from backend.app.agent.state import ConsultationState
from backend.app.tools.contract import ToolResult


@pytest.mark.parametrize(
    "message",
    [
        "我明天要去医院，需要准备什么？",
        "就诊前要带什么材料？",
        "去医院挂号要走什么流程？",
        "第一次去大医院，需要带哪些资料？",
    ],
)
def test_recognizes_guideline_questions(message):
    assert is_guideline_question(message) is True


@pytest.mark.parametrize(
    "message",
    ["今天几号？", "我今天头痛", "布洛芬怎么吃", ""],
)
def test_does_not_match_ordinary_questions(message):
    assert is_guideline_question(message) is False


def _state(message="就诊前要带什么材料？", **kwargs) -> ConsultationState:
    return ConsultationState(latest_user_message=message, turn_id=2, **kwargs)


def _fake_tool(content=None):
    def _call(name, params):
        hits = [{"content": content}] if content else []
        return ToolResult(
            ok=True,
            name=name,
            data={"no_match": not hits, "hits": hits},
            latency_ms=1,
        )

    return _call


def test_answer_guideline_uses_tool_hits(monkeypatch):
    monkeypatch.setattr(
        opening, "call_tool", _fake_tool("带好身份证、医保卡和既往检查资料。")
    )

    result = answer_guideline(_state(), node="general_route")

    assert "身份证" in result["rendered_text"]
    assert result["next_action"] == "GENERAL"
    assert result["tool_results"]["guideline_retrieve"]["hits"]
    assert any(
        entry.get("node") == "tool"
        and entry.get("called") == "guideline_retrieve"
        for entry in result["trace"]
    )


def test_answer_guideline_falls_back_without_hits(monkeypatch):
    monkeypatch.setattr(opening, "call_tool", _fake_tool())

    result = answer_guideline(_state(), node="general_route")

    assert "暂时没有查到" in result["rendered_text"]


def test_route_general_guideline_works_without_model(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "0")
    monkeypatch.setattr(opening, "call_tool", _fake_tool("带好身份证和医保卡。"))

    result = route_general(_state())

    assert result["next_action"] == "GENERAL"
    assert "身份证" in result["conversation"][-1]["text"]


def test_answer_followup_guideline_uses_tool(monkeypatch):
    monkeypatch.setattr(opening, "call_tool", _fake_tool("带好身份证和医保卡。"))
    state = ConsultationState(
        latest_user_message="那挂号要带什么材料？",
        turn_id=3,
        turn_kind="followup",
        final_result={"status": "completed", "next_step": "建议就诊方向：神经内科"},
        collected_facts={"主要症状": ["头部不适"]},
    )

    result = answer_followup(state)

    assert "身份证" in result["rendered_text"]
    assert any(
        entry.get("node") == "tool"
        and entry.get("called") == "guideline_retrieve"
        for entry in result["trace"]
    )
