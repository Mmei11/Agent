"""Phase 1 第三课第二站：决策 Schema 与结构化调用。不联网、不花钱。"""

import json

import pytest
from pydantic import ValidationError

from backend.app.agent import decision_llm
from backend.app.agent.decision_llm import choose_with_meta
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMResult
from backend.app.llm.schemas import DecisionChoice


def usage() -> LLMResult:
    return LLMResult(
        content="",
        model="qwen-plus",
        input_tokens=120,
        output_tokens=30,
        latency_ms=800,
        cost_estimate=0.0002,
    )


@pytest.mark.parametrize("action", ["ASK_FOLLOWUP", "CALL_TOOL", "FINALIZE"])
def test_schema_accepts_each_action(action):
    """三个合法动作都能通过校验。"""
    choice = DecisionChoice(action=action, reason="依据", confidence=0.8)
    assert choice.action == action


def test_schema_rejects_unknown_action():
    """模型编出来的动作在 schema 层就被拦住，不会流进业务代码。"""
    with pytest.raises(ValidationError):
        DecisionChoice(action="FLY", reason="依据", confidence=0.8)


def test_schema_rejects_confidence_out_of_range():
    """置信度必须是 0 到 1，越界直接判非法。"""
    with pytest.raises(ValidationError):
        DecisionChoice(action="FINALIZE", reason="依据", confidence=1.5)


def test_schema_rejects_extra_fields():
    """模型多输出的字段会让 schema 失败，避免悄悄夹带东西。"""
    with pytest.raises(ValidationError):
        DecisionChoice(
            action="FINALIZE", reason="依据", confidence=0.8, note="多余字段"
        )


def test_choose_sends_full_context(monkeypatch):
    """决策请求必须带上完整上下文，并返回模型的动作、依据和用量。"""
    captured = {}

    def fake_chat_structured(messages, response_model, **kwargs):
        captured["messages"] = messages
        captured["response_model"] = response_model
        return (
            DecisionChoice(
                action="ASK_FOLLOWUP", reason="仍缺持续时间且未问过", confidence=0.9
            ),
            usage(),
        )

    monkeypatch.setattr(decision_llm, "chat_structured", fake_chat_structured)

    state = ConsultationState(
        latest_user_message="前额一阵一阵胀痛",
        collected_facts={"主要症状": ["胀痛"]},
        fact_evidence={"主要症状": ["一阵一阵胀痛"]},
        missing_fields=["持续时间", "部位"],
        asked_questions=["请问主要的不适是什么？"],
        followup_count=1,
    )

    choice, usage_result = choose_with_meta(state, max_followups=3)

    payload = json.loads(captured["messages"][1]["content"])
    assert payload["latest_user_message"] == "前额一阵一阵胀痛"
    assert payload["fact_evidence"]["主要症状"] == ["一阵一阵胀痛"]
    assert payload["last_question"] == "请问主要的不适是什么？"
    assert payload["max_followups"] == 3
    assert captured["response_model"] is DecisionChoice
    assert choice.action == "ASK_FOLLOWUP"
    assert choice.reason == "仍缺持续时间且未问过"
    assert choice.confidence == 0.9
    assert usage_result.input_tokens == 120