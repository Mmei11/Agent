"""Phase A：M7 工具选择判定逻辑。"""

from eval.tool_selection import evaluate_case, outcome_of


def test_matching_tool_and_outcome():
    case = {
        "expected_tool": "department_route",
        "expected_outcome": "completed",
        "forbidden_tools": [],
    }
    verdict = evaluate_case(case, ["department_route"], "completed")

    assert verdict["selection_correct"] is True
    assert verdict["outcome_correct"] is True
    assert verdict["dangerous_misuse"] == []
    assert verdict["unreachable"] is False


def test_no_tool_expected_but_tool_called_is_misuse():
    case = {
        "expected_tool": None,
        "expected_outcome": "risk_route",
        "forbidden_tools": ["department_route"],
    }
    verdict = evaluate_case(case, ["department_route"], "risk_route")

    assert verdict["selection_correct"] is False
    assert verdict["dangerous_misuse"] == ["department_route"]


def test_guideline_tool_is_marked_unreachable():
    case = {
        "expected_tool": "guideline_retrieve",
        "expected_outcome": "any",
        "forbidden_tools": [],
    }
    verdict = evaluate_case(case, ["department_route"], "followup")

    assert verdict["selection_correct"] is False
    assert verdict["unreachable"] is True
    assert verdict["outcome_correct"] is True  # expected outcome is "any"


def test_blocked_by_intake_is_separate_from_wrong_tool():
    case = {
        "expected_tool": "department_route",
        "expected_outcome": "completed",
        "forbidden_tools": [],
    }
    verdict = evaluate_case(case, [], "followup", ["部位"])

    assert verdict["selection_correct"] is False
    assert verdict["blocked_by_intake"] is True


def test_outcome_of_general_route():
    values = {
        "final_result": {},
        "trace": [{"node": "general_route", "source": "general"}],
        "turn_kind": "consultation",
    }
    assert outcome_of(values) == "general"


def test_outcome_of_followup():
    values = {"final_result": {}, "trace": [], "turn_kind": "followup", "followup_count": 1}
    assert outcome_of(values) == "followup"
