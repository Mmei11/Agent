"""Phase 3 第 2 课：judge 的离线单元测试。"""

import json

from eval.calibrate_judge import _read_text
from eval.judge import (
    DimensionScore,
    JudgeLLMResult,
    JudgeResult,
    PROMPT_VERSION,
    build_payload,
    judge_case,
    verdict,
)


def _score(value: int) -> DimensionScore:
    return DimensionScore(score=value, reason="r", evidence="e")


def _llm_result(grounding: int = 5, boundary: int = 5) -> JudgeLLMResult:
    return JudgeLLMResult(
        evidence_grounding=_score(grounding),
        boundary_compliance=_score(boundary),
    )


def _result(
    followup: int = 5,
    grounding: int = 5,
    boundary: int = 5,
) -> JudgeResult:
    return JudgeResult(
        followup_relevance=_score(followup),
        evidence_grounding=_score(grounding),
        boundary_compliance=_score(boundary),
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


def test_verdict_marks_result_dimensions_not_applicable_without_final_result():
    summary = verdict(_result(5, 1, 1), {"final_result": {}})

    assert summary["passed"] is True
    assert summary["applicable"]["evidence_grounding"] is False
    assert summary["applicable"]["boundary_compliance"] is False


def test_verdict_fails_below_threshold():
    summary = verdict(
        _result(followup=3),
        {"final_result": {"status": "completed"}},
    )

    assert summary["passed"] is False
    assert summary["failed_dimensions"] == ["followup_relevance"]


def test_judge_case_uses_zero_temperature_and_versioned_prompt():
    captured: dict = {}

    def fake_llm(messages, response_model, **kwargs):
        captured["messages"] = messages
        captured["response_model"] = response_model
        captured.update(kwargs)
        return _llm_result(), object()

    result, _ = judge_case(
        {"conversation": [], "final_result": {}},
        judge_llm=fake_llm,
    )

    assert result.followup_relevance.score == 5
    assert captured["response_model"] is JudgeLLMResult
    assert captured["temperature"] == 0.0
    assert captured["prompt_name"] == "judge.case"
    assert captured["prompt_version"] == PROMPT_VERSION
    assert json.loads(captured["messages"][1]["content"])["final_result"] == {}


def test_judge_case_uses_independent_model_and_prices(monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "qwen-max")
    monkeypatch.setenv("JUDGE_PRICE_INPUT_PER_1K", "0.0024")
    monkeypatch.setenv("JUDGE_PRICE_OUTPUT_PER_1K", "0.0096")
    captured: dict = {}

    def fake_llm(messages, response_model, **kwargs):
        captured.update(kwargs)
        return _llm_result(), object()

    judge_case({"conversation": [], "final_result": {}}, judge_llm=fake_llm)

    assert captured["model"] == "qwen-max"
    assert captured["price_input_per_1k"] == 0.0024
    assert captured["price_output_per_1k"] == 0.0096


def test_judge_case_computes_followup_without_the_model():
    values = {
        "conversation": [
            {"role": "user", "text": "肚子疼"},
            {"role": "agent", "text": "请问主要的不适是什么？"},
        ],
        "final_result": {},
    }

    result, _ = judge_case(
        values,
        judge_llm=lambda *args, **kwargs: (_llm_result(), object()),
    )

    assert result.followup_relevance.score == 3


def test_dimension_score_defaults_missing_evidence():
    score = DimensionScore(score=4, reason="r")

    assert score.evidence == "无"


def test_read_text_accepts_gb18030(tmp_path):
    path = tmp_path / "labels.csv"
    path.write_bytes("sample_id,label,note\nS001,pass,中文备注\n".encode("gb18030"))

    assert "中文备注" in _read_text(path)