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

# 观测用的 prompt 标识：改 prompt 时同步升版本，才能按版本对比指标。
PROMPT_NAME = "followup.replies"
PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """你是"模拟问诊"项目的快捷回答生成器。用户正在按问题补充虚构病例信息。

你会收到一个 JSON 上下文：
- field：即将追问的字段
- question：这一轮的问题
- collected_facts：已经收集到的字段和值
- latest_user_message：用户刚说的话

请生成 3 条用户可以一键点选的回答：
1. 每条都是对 question 的直接回答，2 到 10 个汉字，口语化，不加标点和序号。
2. 必须结合 collected_facts。不要重复用户已经说过的信息。例如已经说了"头部不适"，
   追问部位时应给"前额""后脑""两侧太阳穴"这类更具体的位置，不要给"头部"。
3. 三条回答要覆盖不同方向，不要近义重复。
4. 不做诊断，不建议科室，不给用药。

只输出 JSON，格式：{"replies": ["...", "...", "..."]}"""


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
