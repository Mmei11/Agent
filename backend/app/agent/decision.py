"""Phase C：决策、置信度闸门与工具校验。案例均为虚构。"""

import os
from dataclasses import dataclass, field, replace

from pydantic import ValidationError

from backend.app.agent.followup import select_next_field
from backend.app.agent.state import ConsultationState
from backend.app.skills.context import allowed_tools, skill_for
from backend.app.tools.catalog import DEFAULT_CATALOG, ToolCatalog
from backend.app.tools.contract import ToolSpec

# 低于这个分数，模型的动作不执行，改由规则决策。
MIN_CONFIDENCE = 0.6
# 单轮最多调用几次工具；防止模型在 decision → tool 之间来回循环。
MAX_TOOL_CALLS = 3

FOLLOWUP_DECLINE_PHRASES = (
    "拒绝补充",
    "拒绝回答",
    "不愿补充",
    "不愿回答",
    "不想补充",
    "不想回答",
    "不想继续说",
    "不再补充",
)


@dataclass(frozen=True)
class Decision:
    """一次决策的归一化结果：模型和规则共用同一种返回形状。"""

    action: str
    reason: str = ""
    confidence: float | None = None
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


def user_declined_followup(message: str) -> bool:
    """Detect explicit refusal phrases so the graph can stop ordinary follow-up."""
    return any(phrase in message for phrase in FOLLOWUP_DECLINE_PHRASES)


def query_from_facts(collected_facts: dict) -> str:
    """把已收集字段拼成一句检索问句；没有事实时返回空串。"""
    parts: list[str] = []
    for values in collected_facts.values():
        if isinstance(values, list):
            parts.extend(str(value) for value in values if value)
        elif values:
            parts.append(str(values))
    return " ".join(parts).strip()


def _default_tool(state: ConsultationState, catalog: ToolCatalog) -> ToolSpec | None:
    """按目录顺序找当前技能允许、且还没调用过的只读工具。"""
    allowed = allowed_tools(state)
    for spec in catalog.auto_callable():
        if spec.name in allowed and spec.name not in state.tool_results:
            return spec
    return None


def _tool_called(state: ConsultationState) -> bool:
    """当前技能允许的工具里，是否至少调用过一个。"""
    allowed = allowed_tools(state)
    return any(name in allowed for name in state.tool_results)


def _resolve_tool(
    name: str, state: ConsultationState, catalog: ToolCatalog
) -> ToolSpec | None:
    """把模型给的工具名解析成可执行的契约；不合法时按保守规则回退。"""
    if state.tool_call_count >= MAX_TOOL_CALLS:
        return None
    if not name:
        # 模型说 CALL_TOOL 但没给名字：没有历史工具时用默认工具，否则收束。
        return None if state.tool_results else _default_tool(state, catalog)

    spec = catalog.get(name)
    if (
        spec
        and spec.auto_callable
        and name in allowed_tools(state)
        and name not in state.tool_results
    ):
        return spec

    # 名字非法或已调用过：只在还没调用过任何工具时回退到默认工具。
    return _default_tool(state, catalog) if not state.tool_results else None


def _build_args(
    spec: ToolSpec, args: dict | None, state: ConsultationState
) -> dict:
    """补齐 query 并用参数 Schema 校验；失败时退回最小合法形状。"""
    candidate = dict(args or {})
    query = str(candidate.get("query") or "").strip()
    if not query:
        query = (
            query_from_facts(state.collected_facts)
            or state.latest_user_message.strip()
            or "就医咨询"
        )
        candidate["query"] = query

    try:
        spec.params_model(**candidate)
    except ValidationError:
        return {"query": query}
    return candidate


def _can_ask(state: ConsultationState) -> bool:
    """按当前技能的字段与模板判断是否还有值得问的字段。"""
    skill = skill_for(state)
    return (
        select_next_field(
            state.missing_fields,
            state.asked_questions,
            priority=skill.manifest.followup_priority,
            templates=skill.manifest.question_templates,
        )
        is not None
    )


def rule_action(state: ConsultationState, max_followups: int) -> str:
    """确定性规则：没问够就问，没调工具就调，否则结束。"""
    under_limit = state.followup_count < max_followups
    can_ask = _can_ask(state)
    tool_called = _tool_called(state)

    if under_limit and can_ask:
        return "ASK_FOLLOWUP"
    if not tool_called:
        return "CALL_TOOL"
    return "FINALIZE"


def rule_decision(state: ConsultationState, max_followups: int) -> Decision:
    """规则版决策：动作之外，再给出可执行的工具与参数。"""
    action = rule_action(state, max_followups)
    if action == "CALL_TOOL":
        spec = _default_tool(state, DEFAULT_CATALOG)
        if spec is None:
            return Decision(action="FINALIZE", reason="规则：没有可用工具")
        return Decision(
            action="CALL_TOOL",
            reason="规则：先补齐工具结果",
            tool_name=spec.name,
            tool_args=_build_args(spec, {}, state),
        )
    return Decision(action=action, reason="规则兜底")


def choose_auto(state: ConsultationState, max_followups: int) -> Decision:
    """USE_MODEL=1 时问模型，未启用或模型失败时回退规则。"""
    if os.getenv("USE_MODEL") != "1":
        return replace(rule_decision(state, max_followups), usage={"mode": "mock"})

    from backend.app.agent.decision_llm import choose_with_meta
    from backend.app.llm.client import LLMError

    try:
        choice, usage = choose_with_meta(state, max_followups)
    except LLMError as exc:
        # 模型挂了也要把问诊走完；原因留在 trace，供事后定位。
        return replace(
            rule_decision(state, max_followups),
            usage={"mode": "model", "fallback": True, "error": str(exc)},
        )

    return Decision(
        action=choice.action,
        reason=choice.reason,
        confidence=choice.confidence,
        tool_name=choice.tool_name or "",
        tool_args=dict(choice.tool_args or {}),
        usage={
            "mode": "model",
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "latency_ms": usage.latency_ms,
            "cost_estimate": usage.cost_estimate,
        },
    )


def gate_decision(
    decision: Decision, state: ConsultationState, max_followups: int
) -> tuple[Decision, bool]:
    """置信度不足时收回决定权，返回（实际采用的决策, 是否降级）。"""
    if decision.confidence is None or decision.confidence >= MIN_CONFIDENCE:
        return decision, False
    return rule_decision(state, max_followups), True


def gate_by_confidence(
    choice: str, confidence: float | None, state: ConsultationState, max_followups: int
) -> tuple[str, bool]:
    """兼容旧接口：只返回动作字符串和是否降级。"""
    decision, downgraded = gate_decision(
        Decision(action=choice, confidence=confidence), state, max_followups
    )
    return decision.action, downgraded


def validate_action(model_choice: str, state: ConsultationState, max_followups: int) -> str:
    """旧接口：只校验动作，不处理工具名与参数。

    保留给既有测试和脚本；图内使用 validate_decision。
    """
    under_limit = state.followup_count < max_followups
    can_ask = _can_ask(state)
    tool_called = _tool_called(state)

    if model_choice == "ASK_FOLLOWUP":
        if user_declined_followup(state.latest_user_message):
            return "FINALIZE" if tool_called else "CALL_TOOL"
        if under_limit and can_ask:
            return "ASK_FOLLOWUP"
        if tool_called:
            return "FINALIZE"
        return "CALL_TOOL"

    if model_choice == "CALL_TOOL":
        if not tool_called:
            return "CALL_TOOL"
        return "FINALIZE"

    if not tool_called:
        return "CALL_TOOL"
    return "FINALIZE"


def validate_decision(
    decision: Decision,
    state: ConsultationState,
    max_followups: int,
    catalog: ToolCatalog = DEFAULT_CATALOG,
) -> Decision:
    """校验动作、工具名、参数和单轮预算；不合法时按保守规则回退。"""
    if decision.action not in {"ASK_FOLLOWUP", "CALL_TOOL", "FINALIZE"}:
        return rule_decision(state, max_followups)

    under_limit = state.followup_count < max_followups
    can_ask = _can_ask(state)

    if decision.action == "ASK_FOLLOWUP":
        if user_declined_followup(state.latest_user_message) or not (
            under_limit and can_ask
        ):
            fallback = "FINALIZE" if state.tool_results else "CALL_TOOL"
            return validate_decision(
                replace(decision, action=fallback, tool_name="", tool_args={}),
                state,
                max_followups,
                catalog,
            )
        return replace(decision, tool_name="", tool_args={})

    if decision.action == "CALL_TOOL":
        spec = _resolve_tool(decision.tool_name, state, catalog)
        if spec is None:
            return replace(
                decision,
                action="FINALIZE",
                reason=decision.reason or "没有可用工具",
                tool_name="",
                tool_args={},
            )
        return replace(
            decision,
            action="CALL_TOOL",
            tool_name=spec.name,
            tool_args=_build_args(spec, decision.tool_args, state),
        )

    # FINALIZE：完成结果必须包含至少一次工具整理，没调过就先补一次。
    if state.tool_results:
        return replace(decision, action="FINALIZE", tool_name="", tool_args={})
    spec = _default_tool(state, catalog)
    if spec is None:
        return replace(decision, action="FINALIZE", tool_name="", tool_args={})
    return replace(
        decision,
        action="CALL_TOOL",
        reason=decision.reason or "补齐工具结果",
        tool_name=spec.name,
        tool_args=_build_args(spec, {}, state),
    )
