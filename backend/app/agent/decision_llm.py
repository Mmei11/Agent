"""Phase 1 第三课：决策上下文打包与结构化决策。案例均为虚构。"""

import json

from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import DecisionChoice

DECISION_SYSTEM_PROMPT = """你是"模拟问诊"项目的动作选择器。你只决定下一步做什么，不做诊断，不建议科室，不给用药。

你会收到一个 JSON 上下文，字段含义：
- latest_user_message：用户这一轮刚说的话
- collected_facts：已收集的字段值
- fact_evidence：每个值的原文出处
- missing_fields：仍缺失的必需字段
- asked_questions：已经问过的全部问题
- last_question：上一轮问出口的那一句
- tool_results：已经调用过的工具及其结果
- followup_count / max_followups：已追问次数与上限

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

输出要求：
- action：三个动作之一，必须与上下文一致。
- reason：一句话，引用具体字段名或用户原话片段说明依据。
- confidence：0 到 1。只有依据能在上下文里直接找到时才高于 0.6；信息不足、规则冲突或只能靠猜时如实给低分。

只输出 JSON，不要 Markdown、解释或额外字段。"""


def build_context(state: ConsultationState, max_followups: int) -> dict:
    """把模型做决定需要看到的东西打包成一个 dict。

    不含 risk_flags：命中风险的请求不会走到决策节点，传进去只会误导模型。
    """
    return {
        "latest_user_message": state.latest_user_message,
        "collected_facts": state.collected_facts,
        "fact_evidence": state.fact_evidence,
        "missing_fields": state.missing_fields,
        "asked_questions": state.asked_questions,
        "last_question": state.asked_questions[-1] if state.asked_questions else None,
        "tool_results": state.tool_results,
        "followup_count": state.followup_count,
        "max_followups": max_followups,
    }


def choose_with_meta(
    state: ConsultationState, max_followups: int
) -> tuple[DecisionChoice, LLMResult]:
    """请求模型做结构化决策，返回（动作+依据+置信度, 用量）。

    上下文在这里才被序列化，build_context 保持可单测的纯函数。
    """
    context = build_context(state, max_followups)
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
    )
    return choice, usage