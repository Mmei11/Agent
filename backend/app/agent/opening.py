"""图内预路由：任何未出结果的轮次，先判断普通/医疗。

普通问题直接回复（没有收集到信息时附带邀请描述），医疗问题回到 decision
继续字段收集。这样问候、倾诉、流程问题不会在中间轮次被当成症状追问。
"""

import os

from backend.app.agent.followup import REPLY_OPTIONS
from backend.app.agent.general import (
    GENERAL_FALLBACK,
    general_answer_is_safe,
    is_guideline_question,
)
from backend.app.agent.router import route as route_reply
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError, LLMResult
from backend.app.observability import current_trace
from backend.app.tools.registry import call_tool as _live_call_tool
from backend.app.tools.registry import stub_call_tool

# 没有向量库时用确定性替身跑通流程；与 graph.py 的开关保持一致。
call_tool = (
    stub_call_tool
    if os.getenv("KNOWLEDGE_BACKEND", "pgvector") == "stub"
    else _live_call_tool
)

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


def _guideline_query(state: ConsultationState) -> str:
    facts = []
    for values in (state.collected_facts or {}).values():
        if isinstance(values, list):
            facts.extend(str(value) for value in values if value)
        elif values:
            facts.append(str(values))
    return " ".join([state.latest_user_message.strip(), *facts]).strip()


def answer_guideline(state: ConsultationState, *, node: str = "general_route") -> dict:
    """就医准备问题：确定性调用 guideline_retrieve，再把结果整理成回答。

    这类问题应走 general，但答案不能靠模型常识编；工具结果作为唯一依据，
    查不到就明说，不拿最相近的一条凑数。
    """
    query = _guideline_query(state)
    outcome = call_tool("guideline_retrieve", {"query": query})
    hits = ((outcome.data or {}).get("hits") or []) if outcome.ok else []

    if hits:
        lines = ["就医前可以准备这些："]
        for hit in hits[:3]:
            content = str(hit.get("content") or hit.get("title") or "").strip()
            if content:
                lines.append(f"- {content}")
        lines.append("具体要求以就诊医院为准。")
        text = "\n".join(lines)[:300]
    else:
        text = "暂时没有查到具体的就诊准备条目，建议直接咨询就诊医院。"

    if not general_answer_is_safe(text):
        text = GENERAL_FALLBACK

    scope = current_trace()
    trace_id = scope.trace_id if scope else ""
    tool_data = (
        outcome.data
        if outcome.ok
        else {"error": outcome.error, "error_kind": outcome.error_kind}
    )
    return {
        "next_action": "GENERAL",
        "rendered_text": text,
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": [],
        "tool_results": {"guideline_retrieve": tool_data},
        "tool_call_count": state.tool_call_count + 1,
        "trace": [
            {
                "node": node,
                "source": "general",
                "guideline": True,
                "tool": "guideline_retrieve",
                "tool_ok": outcome.ok,
                "hits": len(hits),
                "trace_id": trace_id,
            },
            {
                "node": "tool",
                "called": "guideline_retrieve",
                "args_keys": ["query"],
                "ok": outcome.ok,
                "latency_ms": outcome.latency_ms,
                "trace_id": trace_id,
            },
        ],
    }


def route_general(state: ConsultationState) -> dict:
    """普通 → 直接回答并结束本轮；医疗 → 回到 decision。"""
    # 就医准备类问题先走确定性工具，不依赖模型是否把它判成普通问题。
    if is_guideline_question(state.latest_user_message):
        return answer_guideline(state, node="general_route")

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
