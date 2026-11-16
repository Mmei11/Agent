"""Phase E：decision 之前的语义风险识别。"""

import pytest

from backend.app.agent import graph as graph_module
from backend.app.agent.semantic_risk import (
    SemanticRiskOutcome,
    SemanticRiskVerdict,
    assess,
    looks_like_symptom_message,
)
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError, LLMResult
from backend.app.safety.policy import decide


def _usage() -> LLMResult:
    return LLMResult(
        content="",
        model="qwen-test",
        input_tokens=10,
        output_tokens=5,
        latency_ms=12,
        cost_estimate=None,
    )


def _state(message: str = "胸口像被石头压着，冷汗一直冒", **updates) -> ConsultationState:
    updates.setdefault("input_level", "safe")
    return ConsultationState(latest_user_message=message, **updates)


def test_looks_like_symptom_message_skips_general_education():
    assert looks_like_symptom_message("胸口像被石头压着，冷汗一直冒")
    assert looks_like_symptom_message("孩子抽了一次，抽完叫不太醒")
    assert not looks_like_symptom_message("头痛时哪些表现提示需要急诊？")
    assert not looks_like_symptom_message("感冒和流感有什么区别？")


def test_assess_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SEMANTIC_RISK_ENABLED", raising=False)
    monkeypatch.setenv("USE_MODEL", "1")

    outcome = assess("胸口像被石头压着，冷汗一直冒")

    assert not outcome.checked
    assert outcome.skip_reason == "disabled"


def test_assess_emergency_keeps_only_verbatim_evidence(monkeypatch):
    monkeypatch.setenv("SEMANTIC_RISK_ENABLED", "1")
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(
        "backend.app.agent.semantic_risk.chat_structured",
        lambda *args, **kwargs: (
            SemanticRiskVerdict(
                risk="emergency",
                confidence=0.9,
                evidence=["胸口像被石头压着", "模型编造的片段"],
            ),
            _usage(),
        ),
    )

    outcome = assess("胸口像被石头压着，冷汗一直冒")

    assert outcome.checked
    assert outcome.risk == "emergency"
    assert outcome.evidence == ["胸口像被石头压着"]
    assert outcome.usage["model"] == "qwen-test"


def test_assess_low_confidence_emergency_downgrades_to_uncertain(monkeypatch):
    monkeypatch.setenv("SEMANTIC_RISK_ENABLED", "1")
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(
        "backend.app.agent.semantic_risk.chat_structured",
        lambda *args, **kwargs: (
            SemanticRiskVerdict(risk="emergency", confidence=0.2, evidence=[]),
            _usage(),
        ),
    )

    outcome = assess("胸口像被石头压着，冷汗一直冒")

    assert outcome.risk == "uncertain"


def test_assess_llm_error_does_not_block(monkeypatch):
    monkeypatch.setenv("SEMANTIC_RISK_ENABLED", "1")
    monkeypatch.setenv("USE_MODEL", "1")

    def _fail(*args, **kwargs):
        raise LLMError("timeout", retryable=True)

    monkeypatch.setattr("backend.app.agent.semantic_risk.chat_structured", _fail)

    outcome = assess("胸口像被石头压着，冷汗一直冒")

    assert outcome.checked
    assert outcome.risk == "routine"
    assert outcome.error


def test_risk_check_upgrades_safe_input_to_r002(monkeypatch):
    monkeypatch.setattr(
        graph_module,
        "assess_semantic_risk",
        lambda message: SemanticRiskOutcome(
            risk="emergency",
            confidence=0.9,
            evidence=["冷汗一直冒"],
            checked=True,
        ),
    )

    result = graph_module.risk_check(_state())

    assert result["risk_flags"] == ["R002"]
    assert result["semantic_risk_evidence"] == ["冷汗一直冒"]
    assert result["trace"][0]["semantic_risk_checked"] is True


def test_risk_check_urgent_sets_v002(monkeypatch):
    monkeypatch.setattr(
        graph_module,
        "assess_semantic_risk",
        lambda message: SemanticRiskOutcome(
            risk="urgent", confidence=0.7, evidence=[], checked=True
        ),
    )

    result = graph_module.risk_check(_state(vague_turns=0))

    assert result["risk_flags"] == ["V002"]
    assert result["vague_turns"] == 1


def test_risk_check_skips_semantic_when_rule_already_risk(monkeypatch):
    called = {"value": False}

    def _should_not_run(message):
        called["value"] = True
        return SemanticRiskOutcome(risk="emergency", checked=True)

    monkeypatch.setattr(graph_module, "assess_semantic_risk", _should_not_run)

    result = graph_module.risk_check(
        _state(input_level="risk", input_labels=["R001"])
    )

    assert result["risk_flags"] == ["R001"]
    assert called["value"] is False


def test_output_uses_semantic_risk_message():
    state = _state(
        risk_flags=["R002"],
        semantic_risk_evidence=["胸口像被石头压着"],
    )

    result = graph_module.output(state)

    assert result["final_result"]["status"] == "risk_route"
    assert "紧急处理" in result["final_result"]["risk_message"]
    assert "胸口像被石头压着" in result["final_result"]["risk_message"]


def test_policy_recognizes_semantic_rules():
    risk = decide(None, _state(risk_flags=["R002"]), "")
    vague = decide(None, _state(risk_flags=["V002"]), "")

    assert risk.level == "risk"
    assert risk.action == "FINALIZE"
    assert vague.level == "vague"
