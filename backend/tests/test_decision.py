"""Day 10 决策器测试：模型选的动作被校验，不合法时兜底。案例均为虚构。"""

from backend.app.agent.decision import mock_choose, parse_action, validate_action
from backend.app.agent.state import ConsultationState


def test_mock_asks_when_missing_and_under_limit():
    """有缺失字段、没到上限 → 预设选 ASK_FOLLOWUP。"""
    state = ConsultationState(
        missing_fields=["持续时间", "程度"],
        asked_questions=[],
        followup_count=0,
    )
    raw = mock_choose(state)
    action = parse_action(raw)
    assert action == "ASK_FOLLOWUP"


def test_mock_calls_tool_when_no_more_questions():
    """没有可问的了 → 预设选 CALL_TOOL。"""
    state = ConsultationState(
        missing_fields=["已知背景"],
        asked_questions=["请问有没有既往病史或正在用药？"],
        followup_count=1,
    )
    raw = mock_choose(state)
    action = parse_action(raw)
    assert action == "CALL_TOOL"


def test_mock_finalizes_when_tool_done():
    """工具已调用 → 预设选 FINALIZE。"""
    state = ConsultationState(
        missing_fields=["已知背景"],
        asked_questions=["请问有没有既往病史或正在用药？"],
        followup_count=1,
        tool_results={"department_suggest": {"department": "神经内科"}},
    )
    raw = mock_choose(state)
    action = parse_action(raw)
    assert action == "FINALIZE"


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
        tool_results={"department_suggest": {"department": "神经内科"}},
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


def test_parse_action_rejects_unknown():
    """模型返回未知动作 → 抛异常。"""
    from backend.app.agent.decision import DecisionError

    try:
        parse_action('{"action":"FLY"}')
        assert False, "应该抛异常"
    except DecisionError:
        pass


def test_parse_action_rejects_bad_json():
    """模型返回非 JSON → 抛异常。"""
    from backend.app.agent.decision import DecisionError

    try:
        parse_action("not json")
        assert False, "应该抛异常"
    except DecisionError:
        pass
