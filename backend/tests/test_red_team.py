"""Phase 3 第 5 课：红队判定逻辑的离线测试。"""

from eval.red_team import evaluate


def _summary(status: int = 200) -> dict:
    return {
        "response_status": status,
        "result_status": "completed",
        "trace_nodes": ["intake", "risk_check", "decision", "tool", "output"],
        "tool_calls": [],
        "failures": [],
    }


def _state(final_result: dict | None = None) -> dict:
    return {
        "final_result": final_result or {},
        "rendered_text": "建议就诊方向：神经内科",
        "trace": [],
    }


CASE = {
    "forbidden_terms": [],
    "forbidden_regex": [],
    "expected_result": "safe",
}


def test_409_with_final_result_is_safe_closure():
    assert evaluate(CASE, _summary(409), _state({"status": "completed"})) == []


def test_409_without_final_result_fails():
    failures = evaluate(CASE, _summary(409), _state())

    assert any("409" in failure for failure in failures)


def test_500_fails():
    failures = evaluate(CASE, _summary(500), _state({"status": "completed"}))

    assert any("500" in failure for failure in failures)