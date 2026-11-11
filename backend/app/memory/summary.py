"""Phase B：长会话滚动摘要。

只摘要“最近窗口之外”的旧对话，避免把最近几轮又压成摘要。
模型失败时不推进游标，下一轮重试；没有旧摘要时用抽取式兜底，
避免摘要完全空白。
"""

from __future__ import annotations

from typing import Any, Callable

from backend.app.context.budget import ANSWER_RECENT_ITEMS

# 最近窗口之外累计多少条才触发一次摘要。
SUMMARY_TRIGGER_ITEMS = 12
# 摘要正文上限，和 SessionSummary.max_length 保持一致。
SUMMARY_MAX_CHARS = 300

Summarizer = Callable[[str, list[dict]], str]


def should_rollup(
    memory: dict | None,
    conversation_len: int,
    *,
    recent_items: int = ANSWER_RECENT_ITEMS,
) -> bool:
    """最近窗口之外是否已经积累了足够多的未摘要内容。"""
    end = max(0, conversation_len - recent_items)
    upto = int((memory or {}).get("summary_upto_index") or 0)
    return end - upto >= SUMMARY_TRIGGER_ITEMS


def fallback_summary(turns: list[dict]) -> str:
    """模型不可用时的抽取式兜底：保留角色和原话，截断到上限。"""
    parts = []
    for item in turns:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        role = "用户" if item.get("role") == "user" else "助手"
        parts.append(f"{role}：{text}")
    return "；".join(parts)[:SUMMARY_MAX_CHARS]


def rollup_memory(
    memory: dict | None,
    conversation: list[dict] | None,
    *,
    summarizer: Summarizer | None = None,
) -> dict:
    """按需把旧对话合并进摘要；不触发时原样返回。"""
    result = dict(memory or {})
    conversation = list(conversation or [])

    if not should_rollup(result, len(conversation)):
        return result

    end = max(0, len(conversation) - ANSWER_RECENT_ITEMS)
    upto = int(result.get("summary_upto_index") or 0)
    turns = conversation[upto:end]
    if not turns:
        return result

    previous = str(result.get("summary") or "")
    try:
        if summarizer is None:
            from backend.app.memory.summary_llm import summarize

            summary = summarize(previous, turns)
        else:
            summary = summarizer(previous, turns)
    except Exception:
        # 模型失败：有旧摘要就保留、游标不动，下一轮重试。
        # 没有旧摘要时用抽取式兜底，至少不让长会话完全失去上下文。
        if not previous:
            fallback = fallback_summary(turns)
            if fallback:
                result["summary"] = fallback
                result["summary_upto_index"] = end
                result["summary_source"] = "fallback"
        return result

    summary = str(summary or "").strip()[:SUMMARY_MAX_CHARS]
    if not summary:
        return result

    result["summary"] = summary
    result["summary_upto_index"] = end
    result["summary_turns"] = len(turns)
    result["summary_source"] = "model"
    return result
