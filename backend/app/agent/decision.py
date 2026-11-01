"""第三课：模型决策、置信度闸门与动作校验。案例均为虚构。"""

import os
from dataclasses import dataclass, field

from backend.app.agent.followup import select_next_field
from backend.app.agent.state import ConsultationState

# 低于这个分数，模型的动作不执行，改由规则决策。
MIN_CONFIDENCE = 0.6

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


def user_declined_followup(message: str) -> bool:
    """Detect explicit refusal phrases so the graph can stop ordinary follow-up."""
    return any(phrase in message for phrase in FOLLOWUP_DECLINE_PHRASES)


@dataclass(frozen=True)
class Decision:
    """一次决策的归一化结果：模型和规则共用同一种返回形状。"""

    action: str
    reason: str = ""
    confidence: float | None = None
    usage: dict = field(default_factory=dict)


def rule_action(state: ConsultationState, max_followups: int) -> str:
    """确定性规则：没问够就问，没调工具就调，否则结束。"""
    under_limit = state.followup_count < max_followups
    can_ask = select_next_field(state.missing_fields, state.asked_questions) is not None
    tool_called = "department_route" in state.tool_results

    if under_limit and can_ask:
        return "ASK_FOLLOWUP"
    if not tool_called:
        return "CALL_TOOL"
    return "FINALIZE"


def choose_auto(state: ConsultationState, max_followups: int) -> Decision:
    """USE_MODEL=1 时问模型，未启用或模型失败时回退规则。"""
    if os.getenv("USE_MODEL") != "1":
        return Decision(action=rule_action(state, max_followups), usage={"mode": "mock"})

    from backend.app.agent.decision_llm import choose_with_meta
    from backend.app.llm.client import LLMError

    try:
        choice, usage = choose_with_meta(state, max_followups)
    except LLMError as exc:
        # 模型挂了也要把问诊走完；原因留在 trace，供事后定位。
        return Decision(
            action=rule_action(state, max_followups),
            usage={"mode": "model", "fallback": True, "error": str(exc)},
        )

    return Decision(
        action=choice.action,
        reason=choice.reason,
        confidence=choice.confidence,
        usage={
            "mode": "model",
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "latency_ms": usage.latency_ms,
            "cost_estimate": usage.cost_estimate,
        },
    )


def gate_by_confidence(
    choice: str, confidence: float | None, state: ConsultationState, max_followups: int
) -> tuple[str, bool]:
    """置信度不足时收回决定权，返回（实际采用的动作, 是否降级）。"""
    if confidence is None or confidence >= MIN_CONFIDENCE:
        return choice, False
    return rule_action(state, max_followups), True


def validate_action(model_choice: str, state: ConsultationState, max_followups: int) -> str:
    """校验动作是否允许执行。不允许时返回兜底动作。

    返回最终执行的动作（可能与传入动作不同）。
    """
    under_limit = state.followup_count < max_followups  # 追问还没到上限
    can_ask = select_next_field(state.missing_fields, state.asked_questions) is not None  # 还有值得问的字段
    tool_called = "department_route" in state.tool_results  # 工具已经调过了

    if model_choice == "ASK_FOLLOWUP":
        # 用户明确拒答时，尊重拒答并收束；工具尚未运行则继续做受限整理。
        if user_declined_followup(state.latest_user_message):
            return "FINALIZE" if tool_called else "CALL_TOOL"
        if under_limit and can_ask:  # 没到上限且还有可问的 → 放行
            return "ASK_FOLLOWUP"
        # 模型想追问但没有可问的了 → 兜底
        if tool_called:
            return "FINALIZE"  # 工具调过了 → 结束
        return "CALL_TOOL"  # 工具没调 → 去调工具

    if model_choice == "CALL_TOOL":
        if not tool_called:
            return "CALL_TOOL"  # 工具没调过 → 放行
        # 工具已调用 → 不重复调
        return "FINALIZE"  # 兜底为结束

    # 完成结果必须包含工具整理过的内容，没调工具就不许结束。
    if not tool_called:
        return "CALL_TOOL"
    return "FINALIZE"