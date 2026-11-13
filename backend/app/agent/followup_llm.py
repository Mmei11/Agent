"""Phase 7：根据已收集信息生成追问的快捷回答。

固定选项容易和用户已经说过的话重复：已经说了"头部不适"，追问部位时
还提示"头部、腹部、胸部"。这里让模型结合主要症状和已收集字段生成候选，
调用方负责过滤与回退。
"""

import json

from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import ReplySuggestions
from backend.app.observability import span
from backend.app.prompts import load_prompt

# 观测用的 prompt 标识：改 prompt 时同步升版本，才能按版本对比指标。
PROMPT_NAME = "followup.replies"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = load_prompt(PROMPT_NAME, PROMPT_VERSION)


def build_context(state: ConsultationState, field: str, question: str) -> dict:
    """模型生成候选只需要这几个字段，避免把整份对话都塞进 prompt。"""
    return {
        "field": field,
        "question": question,
        "collected_facts": state.collected_facts,
        "latest_user_message": state.latest_user_message,
    }


def generate_with_meta(
    state: ConsultationState, field: str, question: str
) -> tuple[ReplySuggestions, LLMResult]:
    """请求模型生成候选回答，返回（候选, 用量）。"""
    context = build_context(state, field, question)
    with span(
        "followup.replies",
        kind="internal",
        attributes={"followup.field": field},
    ) as record:
        suggestions, usage = chat_structured(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            ReplySuggestions,
            max_tokens=160,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )
        record.attributes["followup.reply_count"] = len(suggestions.replies)
    return suggestions, usage
