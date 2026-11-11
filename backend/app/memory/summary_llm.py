"""Phase B：滚动摘要的模型调用。

输入是“上一条摘要 + 最近窗口之外的若干轮对话”，输出一段 300 字以内摘要。
和 intake 的解析器分开：摘要允许压缩和改写，解析器要求逐字证据。
"""

import json

from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import SessionSummary

PROMPT_NAME = "memory.summary"
PROMPT_VERSION = "v1"

SUMMARY_SYSTEM_PROMPT = """你是"模拟问诊"项目的会话摘要器。你会收到 previous_summary 和 new_turns。
把 new_turns 合并进 previous_summary，输出 2 到 5 句、最多 300 字的摘要。

保留：
- 用户描述的症状、时间线、部位、程度、伴随表现
- 用户的偏好、行程、约束、生活细节
- 已经给出的结论和未决问题

不要：
- 新增诊断、处方、药物名称或剂量
- 编造用户没说过的事
- 把助手的话当成用户事实

没有新信息时保留 previous_summary。只输出 JSON：{"summary": "..."}"""


def summarize_with_meta(
    previous_summary: str, turns: list[dict]
) -> tuple[str, LLMResult]:
    """请求模型更新摘要，返回（摘要, 用量）。"""
    payload = {
        "previous_summary": previous_summary,
        "new_turns": [
            {"role": item.get("role", ""), "text": item.get("text", "")}
            for item in turns
        ],
    }
    result, usage = chat_structured(
        [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        SessionSummary,
        max_tokens=320,
        prompt_name=PROMPT_NAME,
        prompt_version=PROMPT_VERSION,
    )
    return result.summary, usage


def summarize(previous_summary: str, turns: list[dict]) -> str:
    return summarize_with_meta(previous_summary, turns)[0]
