"""Phase C：决策层的工具选择、参数校验与预算。"""

from types import SimpleNamespace

from pydantic import BaseModel

from backend.app.agent.decision import (
    Decision,
    query_from_facts,
    rule_decision,
    validate_decision,
)
from backend.app.agent.graph import build_graph
from backend.app.agent.intake import intake
from backend.app.agent.state import ConsultationState
from backend.app.llm.schemas import DecisionChoice
from backend.app.tools.catalog import ToolCatalog
from backend.app.tools.contract import ToolResult, ToolSpec


class _Params(BaseModel):
    query: str = ""


def _spec(name: str, risk_class: str = "read_only") -> ToolSpec:
    return ToolSpec(
        name=name,
        description=name,
        params_model=_Params,
        handler=lambda params: {"ok": True},
        risk_class=risk_class,
    )


def test_query_from_facts_joins_values():
    assert query_from_facts({"主要症状": ["头痛"], "持续时间": ["两天"]}) == "头痛 两天"


def test_rule_decision_carries_default_tool_and_query():
    state = ConsultationState(missing_fields=[], followup_count=0)
    decision = rule_decision(state, max_followups=3)

    assert decision.action == "CALL_TOOL"
    assert decision.tool_name == "department_route"
    assert decision.tool_args["query"]


def test_validate_allows_model_selected_guideline_tool():
    state = ConsultationState(
        collected_facts={"主要症状": ["头痛"]}, followup_count=0
    )
    decision = Decision(
        action="CALL_TOOL",
        reason="需要就诊准备",
        confidence=0.9,
        tool_name="guideline_retrieve",
        tool_args={},
    )

    final = validate_decision(decision, state, max_followups=3)

    assert final.action == "CALL_TOOL"
    assert final.tool_name == "guideline_retrieve"
    assert final.tool_args["query"] == "头痛"


def test_validate_falls_back_to_default_when_tool_unknown():
    state = ConsultationState(collected_facts={"主要症状": ["头痛"]}, followup_count=0)
    decision = Decision(
        action="CALL_TOOL",
        reason="",
        confidence=0.9,
        tool_name="does_not_exist",
    )

    final = validate_decision(decision, state, max_followups=3)

    assert final.action == "CALL_TOOL"
    assert final.tool_name == "department_route"


def test_validate_finalizes_when_tool_already_used():
    state = ConsultationState(
        followup_count=0,
        tool_results={"department_route": {"department": "神经内科"}},
        tool_call_count=1,
    )
    decision = Decision(
        action="CALL_TOOL",
        reason="",
        confidence=0.9,
        tool_name="department_route",
    )

    final = validate_decision(decision, state, max_followups=3)

    assert final.action == "FINALIZE"


def test_validate_forces_tool_before_finalize():
    state = ConsultationState(followup_count=0)

    final = validate_decision(
        Decision(action="FINALIZE", confidence=0.9), state, max_followups=3
    )

    assert final.action == "CALL_TOOL"
    assert final.tool_name == "department_route"


def test_validate_respects_tool_budget():
    state = ConsultationState(followup_count=0, tool_call_count=3)

    final = validate_decision(
        Decision(action="CALL_TOOL", confidence=0.9), state, max_followups=3
    )

    assert final.action == "FINALIZE"


def test_validate_never_auto_calls_write_tool():
    catalog = ToolCatalog([_spec("write_tool", risk_class="write")])
    state = ConsultationState(followup_count=0)

    final = validate_decision(
        Decision(
            action="CALL_TOOL",
            confidence=0.9,
            tool_name="write_tool",
        ),
        state,
        max_followups=3,
        catalog=catalog,
    )

    assert final.action == "FINALIZE"


def test_intake_resets_tool_scheduling():
    state = ConsultationState(
        latest_user_message="头痛",
        tool_call_count=2,
        next_tool="guideline_retrieve",
        next_tool_args={"query": "旧参数"},
    )

    result = intake(state)

    assert result["tool_call_count"] == 0
    assert result["next_tool"] == ""
    assert result["next_tool_args"] == {}


def test_graph_executes_model_selected_tool(monkeypatch):
    """模型选 guideline_retrieve 时，图里真的执行它，而不是写死 department_route。"""
    monkeypatch.setenv("USE_MODEL", "1")
    import backend.app.agent.decision_llm as decision_llm
    import backend.app.agent.graph as graph_module
    import backend.app.agent.intake as intake_module

    facts = {
        "主要症状": ["头部不适"],
        "持续时间": ["两天"],
        "部位": ["前额"],
        "程度": ["轻"],
        "伴随表现": ["恶心"],
        "已知背景": ["无病史"],
    }
    monkeypatch.setattr(
        intake_module,
        "extract_facts_auto",
        lambda message, meta=None: (facts, {}),
    )

    usage = SimpleNamespace(
        model="fake",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        cost_estimate=0.0,
    )
    monkeypatch.setattr(
        decision_llm,
        "choose_with_meta",
        lambda state, max_followups: (
            DecisionChoice(
                action="CALL_TOOL",
                reason="需要就诊准备",
                confidence=0.9,
                tool_name="guideline_retrieve",
                tool_args={"query": "就诊准备"},
            ),
            usage,
        ),
    )

    calls = []

    def fake_tool(name, params):
        calls.append((name, params))
        return ToolResult(
            ok=True, name=name, data={"no_match": True, "hits": []}, latency_ms=1
        )

    monkeypatch.setattr(graph_module, "call_tool", fake_tool)

    app = build_graph()
    result = app.invoke(
        {"latest_user_message": "头部不适两天，前额，程度轻，伴有恶心，无病史"}
    )

    assert calls == [("guideline_retrieve", {"query": "就诊准备"})]
    assert "guideline_retrieve" in result["tool_results"]
    assert result["tool_call_count"] == 1
