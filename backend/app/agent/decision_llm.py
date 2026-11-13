"""Phase 1 第三课：决策上下文打包与结构化决策。案例均为虚构。"""

import json

from backend.app.agent.state import ConsultationState
from backend.app.context.assembler import assemble_reference_context
from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import DecisionChoice
from backend.app.observability import span
from backend.app.skills.context import allowed_tools
from backend.app.tools.catalog import DEFAULT_CATALOG, prompt_tool_results
from backend.app.prompts import load_prompt

# 观测用的 prompt 标识：改 prompt 时同步升版本，才能按版本对比指标。
PROMPT_NAME = "decision.choose"
PROMPT_VERSION = "v6"

DECISION_SYSTEM_PROMPT = load_prompt(PROMPT_NAME, PROMPT_VERSION)


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
        # 外部工具由确定性路径使用；decision 只看到当前技能允许的内部工具。
        "tools": [
            entry
            for entry in DEFAULT_CATALOG.prompt_entries()
            if entry["scope"] == "internal"
            and entry["name"] in allowed_tools(state)
        ],
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
