"""第三课：规则决策、置信度闸门与动作校验。不联网、不花钱。"""

import pytest

from backend.app.agent.decision import (
    choose_auto,
    gate_by_confidence,
    rule_action,
    validate_action,
)
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError, LLMResult
from backend.app.llm.schemas import DecisionChoice


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch):
    """默认跑规则路径，避免本机 USE_MODEL=1 影响单测。"""
    monkeypatch.delenv("USE_MODEL", raising=False)


def _usage() -> LLMResult:
    return LLMResult(
        content="",
        model="qwen-plus",
        input_tokens=120,
        output_tokens=30,
        latency_ms=800,
        cost_estimate=0.0002,
    )


def test_rule_asks_when_missing_and_under_limit():
    """有缺失字段、没到上限 → 规则选 ASK_FOLLOWUP。"""
    state = ConsultationState(
        missing_fields=["持续时间", "程度"],
        asked_questions=[],
        followup_count=0,
    )
    assert rule_action(state, max_followups=3) == "ASK_FOLLOWUP"


def test_rule_calls_tool_when_no_more_questions():
    """没有可问的了 → 规则选 CALL_TOOL。"""
    state = ConsultationState(
        missing_fields=["已知背景"],
        asked_questions=["请问有没有既往病史或正在用药？"],
        followup_count=1,
    )
    assert rule_action(state, max_followups=3) == "CALL_TOOL"


def test_rule_finalizes_when_tool_done():
    """工具已调用 → 规则选 FINALIZE。"""
    state = ConsultationState(
        missing_fields=["已知背景"],
        asked_questions=["请问有没有既往病史或正在用药？"],
        followup_count=1,
        tool_results={"department_route": {"department": "神经内科"}},
    )
    assert rule_action(state, max_followups=3) == "FINALIZE"


def test_validate_overrides_model_ask_when_none_available():
    """模型想追问但没有可问的了 → 程序兜底为 CALL_TOOL。"""
    state = ConsultationState(
        missing_fields=["已知背景"],
        asked_questions=["请问有没有既往病史或正在用药？"],
        followup_count=1,
    )
    final = validate_action("ASK_FOLLOWUP", state, max_followups=3)
    assert final == "CALL_TOOL"


def test_validate_prevents_duplicate_tool_call():
    """模型想再调工具但工具已调过 → 程序兜底为 FINALIZE。"""
    state = ConsultationState(
        missing_fields=["已知背景"],
        asked_questions=["请问有没有既往病史或正在用药？"],
        followup_count=1,
        tool_results={"department_route": {"department": "神经内科"}},
    )
    final = validate_action("CALL_TOOL", state, max_followups=3)
    assert final == "FINALIZE"


def test_validate_allows_model_when_correct():
    """模型选的动作合法 → 程序放行。"""
    state = ConsultationState(
        missing_fields=["持续时间", "程度"],
        asked_questions=[],
        followup_count=0,
    )
    final = validate_action("ASK_FOLLOWUP", state, max_followups=3)
    assert final == "ASK_FOLLOWUP"


def test_validate_stops_followup_after_explicit_refusal():
    """用户明确拒绝补充时，不再提出普通追问，改为受限工具收束。"""
    state = ConsultationState(
        latest_user_message="拒绝补充更多信息",
        missing_fields=["持续时间", "部位"],
        followup_count=1,
    )
    final = validate_action("ASK_FOLLOWUP", state, max_followups=3)
    assert final == "CALL_TOOL"


def test_validate_forces_tool_before_finalize():
    """模型想结束但工具没调过 → 先补齐科室建议，不让完成结果缺一块。"""
    state = ConsultationState(
        latest_user_message="拒绝补充更多信息",
        missing_fields=["持续时间"],
        followup_count=1,
    )
    final = validate_action("FINALIZE", state, max_followups=3)
    assert final == "CALL_TOOL"


def test_validate_allows_finalize_after_tool():
    """工具调过之后，FINALIZE 放行。"""
    state = ConsultationState(
        missing_fields=["持续时间"],
        followup_count=1,
        tool_results={"department_route": {"department": "神经内科"}},
    )
    final = validate_action("FINALIZE", state, max_followups=3)
    assert final == "FINALIZE"


def test_gate_trusts_high_confidence():
    """置信度过关 → 保留模型动作。"""
    state = ConsultationState(missing_fields=["持续时间"], followup_count=0)
    action, downgraded = gate_by_confidence("ASK_FOLLOWUP", 0.9, state, 3)

    assert action == "ASK_FOLLOWUP"
    assert downgraded is False


def test_gate_threshold_is_inclusive():
    """刚好 0.6 视为可信，避免阈值本身成为模糊地带。"""
    state = ConsultationState(missing_fields=["持续时间"], followup_count=0)
    action, downgraded = gate_by_confidence("ASK_FOLLOWUP", 0.6, state, 3)

    assert action == "ASK_FOLLOWUP"
    assert downgraded is False


def test_gate_downgrades_low_confidence_to_rule():
    """置信度不足 → 收回决定权，改用规则动作。"""
    state = ConsultationState(
        missing_fields=["已知背景"],
        asked_questions=["请问有没有既往病史或正在用药？"],
        followup_count=1,
    )
    action, downgraded = gate_by_confidence("ASK_FOLLOWUP", 0.4, state, 3)

    assert action == "CALL_TOOL"
    assert downgraded is True


def test_gate_keeps_rule_choice_without_confidence():
    """规则决策没有置信度，不参与闸门。"""
    state = ConsultationState(
        missing_fields=["持续时间"],
        followup_count=0,
        tool_results={"department_route": {"department": "神经内科"}},
    )
    action, downgraded = gate_by_confidence("FINALIZE", None, state, 3)

    assert action == "FINALIZE"
    assert downgraded is False


def test_choose_auto_uses_rule_in_mock_mode():
    """未启用模型时不产生任何模型用量。"""
    state = ConsultationState(missing_fields=["持续时间"], followup_count=0)
    result = choose_auto(state, max_followups=3)

    assert result.action == "ASK_FOLLOWUP"
    assert result.confidence is None
    assert result.usage == {"mode": "mock"}


def test_choose_auto_returns_model_reason_and_usage(monkeypatch):
    """模型路径把动作、依据、置信度和用量一起带回来。"""
    monkeypatch.setenv("USE_MODEL", "1")
    import backend.app.agent.decision_llm as decision_llm

    monkeypatch.setattr(
        decision_llm,
        "choose_with_meta",
        lambda state, max_followups: (
            DecisionChoice(action="FINALIZE", reason="信息已足够", confidence=0.8),
            _usage(),
        ),
    )

    result = choose_auto(ConsultationState(), max_followups=3)

    assert result.action == "FINALIZE"
    assert result.reason == "信息已足够"
    assert result.confidence == 0.8
    assert result.usage["mode"] == "model"
    assert result.usage["input_tokens"] == 120
    assert result.usage["cost_estimate"] == 0.0002


def test_choose_auto_falls_back_on_llm_error(monkeypatch):
    """模型调用失败 → 规则兜底，trace 里标记 fallback。"""
    monkeypatch.setenv("USE_MODEL", "1")
    import backend.app.agent.decision_llm as decision_llm

    def boom(state, max_followups):
        raise LLMError("model down", retryable=True)

    monkeypatch.setattr(decision_llm, "choose_with_meta", boom)

    state = ConsultationState(missing_fields=["持续时间"], followup_count=0)
    result = choose_auto(state, max_followups=3)

    assert result.action == "ASK_FOLLOWUP"
    assert result.confidence is None
    assert result.usage["fallback"] is True
    assert result.usage["error"] == "model down"