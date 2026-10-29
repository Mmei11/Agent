"""Phase 3 第 2 课：judge 的离线单元测试。"""

import json

from eval.judge import (
    DimensionScore,
    JudgeResult,
    build_payload,
    judge_case,
    verdict,
)


def _score(value: int, applicable: bool = True) -> DimensionScore:
    return DimensionScore(score=value, applicable=applicable, reason="r", evidence="e")


def _result(
    followup: int = 5,
    grounding: int = 5,
    boundary: int = 5,
    grounding_applicable: bool = True,
    boundary_applicable: bool = True,
) -> JudgeResult:
    return JudgeResult(
        followup_relevance=_score(followup),
        evidence_grounding=_score(grounding, grounding_applicable),
        boundary_compliance=_score(boundary, boundary_applicable),
    )


def test_build_payload_labels_speakers_and_drops_expected_answers():
    values = {
        "conversation": [
            {"role": "user", "text": "头部不适"},
            {"role": "agent", "text": "多久了？"},
        ],
        "final_result": {"status": "completed"},
        "rendered_text": "建议就诊方向：神经内科",
        "tool_results": {"department_suggest": {"department": "神经内科"}},
        "expected_actions": ["ASK_FOLLOWUP"],
        "prohibited_behaviors": ["确诊"],
    }

    payload = build_payload(values)

    assert payload["conversation"][0] == {
        "turn": 1,
        "speaker": "用户",
        "text": "头部不适",
    }
    assert payload["conversation"][1]["speaker"] == "系统"
    assert "expected_actions" not in payload
    assert "prohibited_behaviors" not in payload


def test_verdict_ignores_not_applicable_dimensions():
    summary = verdict(
        _result(5, 1, 1, grounding_applicable=False, boundary_applicable=False)
    )

    assert summary["passed"] is True
    assert summary["failed_dimensions"] == []


def test_verdict_fails_below_threshold():
    summary = verdict(_result(followup=3))

    assert summary["passed"] is False
    assert summary["failed_dimensions"] == ["followup_relevance"]


def test_judge_case_uses_zero_temperature_and_versioned_prompt():
    captured: dict = {}

    def fake_llm(messages, response_model, **kwargs):
        captured["messages"] = messages
        captured["response_model"] = response_model
        captured.update(kwargs)
        return _result(), object()

    result, _ = judge_case(
        {"conversation": [], "final_result": {}},
        judge_llm=fake_llm,
    )

    assert result.followup_relevance.score == 5
    assert captured["temperature"] == 0.0
    assert captured["prompt_name"] == "judge.case"
    assert captured["prompt_version"] == "v1"
    assert json.loads(captured["messages"][1]["content"])["final_result"] == {}