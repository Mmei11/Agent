"""Phase 1 第三课：决策上下文打包与结构化决策。案例均为虚构。"""

import json

from backend.app.agent.state import ConsultationState
from backend.app.context.assembler import assemble_reference_context
from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import DecisionChoice
from backend.app.observability import span
from backend.app.tools.catalog import DEFAULT_CATALOG, prompt_tool_results

# 观测用的 prompt 标识：改 prompt 时同步升版本，才能按版本对比指标。
PROMPT_NAME = "decision.choose"
PROMPT_VERSION = "v5"

DECISION_SYSTEM_PROMPT = """你是"模拟问诊"项目的动作选择器。你只决定下一步做什么，不做诊断，不建议科室，不给用药。

你会收到一个 JSON 上下文，字段含义：
- latest_user_message：用户这一轮刚说的话
- collected_facts：已收集的字段值
- fact_evidence：每个值的原文出处
- missing_fields：仍缺失的必需字段
- asked_questions：已经问过的全部问题
- last_question：上一轮问出口的那一句
- tool_results：已经调用过的工具及其结果摘要（不是原文，足够判断下一步）
- followup_count / max_followups：已追问次数与上限
- session_memory：跨轮保留的用户细节与摘要（只用于理解指代，不当作新事实）
- recent_conversation：最近几轮对话（只用于理解指代）
- tools：允许自动调用的只读工具清单，含 name、description、risk_class、scope、input_schema
- tool_call_count / max_tool_calls：本轮已调用工具次数与上限

可选动作只有三个：
1. ASK_FOLLOWUP：继续追问一个缺失字段
2. CALL_TOOL：调用受限工具整理科室与就诊建议
3. FINALIZE：结束信息收集，输出当前结果

选择规则：
- 用户这一轮明确拒答、表示记不清或无法提供时，不再追问。
- missing_fields 里还有没问过的字段，且 followup_count < max_followups → ASK_FOLLOWUP。
- 没有值得问的字段，且工具还没调用 → CALL_TOOL。
- 用户拒答但工具还没调用 → CALL_TOOL，先整理一次科室建议再结束。
- 工具已经调用 → FINALIZE。FINALIZE 的前提是工具已调用。
- 不要选择已经调用过的工具。
- session_memory 和 recent_conversation 只用于理解用户指代；不要把它们当成新的症状事实。
- 用户已经在 collected_facts、session_memory.summary 或 session_memory.details 里明确说过的信息，不要重复追问同一个字段。
- CALL_TOOL 时，tool_name 必须来自 tools 清单，tool_args 必须符合该工具的 input_schema。
- tool_results 里已经出现过的工具不要重复调用；单轮最多调用 max_tool_calls 次，达到上限后必须 FINALIZE。
- 红旗已经由安全层在 decision 之前处理；不要用 red_flag_lookup 代替风险分流。

输出要求：
- action：三个动作之一，必须与上下文一致。
- reason：一句话，引用具体字段名或用户原话片段说明依据。
- confidence：0 到 1。只有依据能在上下文里直接找到时才高于 0.6；信息不足、规则冲突或只能靠猜时如实给低分。
- tool_name：action=CALL_TOOL 时必填，必须来自 tools；其他动作填 null。
- tool_args：action=CALL_TOOL 时给一个 JSON 对象，符合对应工具的 input_schema；其他动作给 {}。

只输出 JSON，不要 Markdown、解释或额外字段。"""


def build_context(
    state: ConsultationState, max_followups: int, max_tool_calls: int = 3
) -> dict:
    """把模型做决定需要看到的东西打包成一个 dict。

    不含 risk_flags：命中风险的请求不会走到决策节点，传进去只会误导模型。
    session_memory / recent_conversation 只帮助理解指代，不当作新事实。
    tools 只包含允许自动调用的只读工具。
    """
    return {
        "latest_user_message": state.latest_user_message,
        "collected_facts": state.collected_facts,
        "fact_evidence": state.fact_evidence,
        "missing_fields": state.missing_fields,
        "asked_questions": state.asked_questions,
        "last_question": state.asked_questions[-1] if state.asked_questions else None,
        "tool_results": prompt_tool_results(state.tool_results),
        "tool_call_count": state.tool_call_count,
        "max_tool_calls": max_tool_calls,
        "followup_count": state.followup_count,
        "max_followups": max_followups,
        "tools": DEFAULT_CATALOG.prompt_entries(),
        **assemble_reference_context(state),
    }


def choose_with_meta(
    state: ConsultationState, max_followups: int, max_tool_calls: int = 3
) -> tuple[DecisionChoice, LLMResult]:
    """请求模型做结构化决策，返回（动作+依据+置信度, 用量）。

    上下文在这里才被序列化，build_context 保持可单测的纯函数。
    """
    context = build_context(state, max_followups, max_tool_calls)
    # 只记录“模型看到了哪些字段名/数量”，不落用户原话和字段值。
    with span(
        "decision.context",
        kind="internal",
        attributes={
            "decision.missing_fields": context["missing_fields"],
            "decision.asked_count": len(context["asked_questions"]),
            "decision.tool_results": sorted(context["tool_results"]),
            "decision.followup_count": context["followup_count"],
            "decision.has_evidence": bool(context["fact_evidence"]),
        },
    ) as record:
        choice, usage = chat_structured(
            [
                {"role": "system", "content": DECISION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ],
            DecisionChoice,
            max_tokens=200,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )
        record.attributes["decision.action"] = choice.action
        record.attributes["decision.confidence"] = choice.confidence
    return choice, usage