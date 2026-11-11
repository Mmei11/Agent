"""图内预路由：任何未出结果的轮次，先判断普通/医疗。

普通问题直接回复（没有收集到信息时附带邀请描述），医疗问题回到 decision
继续字段收集。这样问候、倾诉、流程问题不会在中间轮次被当成症状追问。
"""

import os

from backend.app.agent.followup import REPLY_OPTIONS
from backend.app.agent.general import GENERAL_FALLBACK, general_answer_is_safe
from backend.app.agent.router import route as route_reply
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError, LLMResult
from backend.app.observability import current_trace

OPENING_INVITE = (
    "今天身体状态怎么样？如果有不舒服，可以告诉我具体哪里不舒服、持续多久，"
    "我帮你整理就医方向。"
)


def _usage_dict(usage: LLMResult | None) -> dict:
    if usage is None:
        return {}
    return {
        "model": usage.model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": usage.latency_ms,
        "cost_estimate": usage.cost_estimate,
    }


def route_general(state: ConsultationState) -> dict:
    """普通 → 直接回答并结束本轮；医疗 → 回到 decision。"""
    if os.getenv("USE_MODEL") != "1":
        return {"next_action": "MEDICAL"}

    try:
        routed, usage = route_reply(state.latest_user_message)
    except LLMError:
        return {"next_action": "MEDICAL"}

    if routed.intent != "general":
        return {"next_action": "MEDICAL"}

    if general_answer_is_safe(routed.answer):
        text = routed.answer.strip()
        source = "router"
    else:
        text = GENERAL_FALLBACK
        source = "fallback"

    # 只在会话第一轮做一次邀请；后续 general 回复不再重复贴邀请和快捷选项。
    opening = state.turn_id == 1 and not state.collected_facts
    if opening:
        text = f"{text}\n\n{OPENING_INVITE}"

    scope = current_trace()
    return {
        "next_action": "GENERAL",
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": (
            list(REPLY_OPTIONS.get("主要症状", [])) if opening else []
        ),
        "trace": [
            {
                "node": "general_route",
                "source": "general",
                "general_answer_source": source,
                "router_intent": "general",
                "opening": opening,
                "answer_chars": len(text),
                "model_usage": _usage_dict(usage),
                "trace_id": scope.trace_id if scope else "",
            }
        ],
    }
