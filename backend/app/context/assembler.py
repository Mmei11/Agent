"""Phase B：上下文装配。

节点不再自己拼 dict；这里统一决定“给模型看哪些切片、每段最多多少”。
当前只接 answer，后续 intake / decision 复用同一套预算。
"""

from __future__ import annotations

from typing import Any

from backend.app.context.budget import (
    ANSWER_MEMORY_CHARS,
    ANSWER_MEMORY_ITEMS,
    ANSWER_RECENT_CHARS,
    ANSWER_RECENT_ITEMS,
    REFERENCE_MEMORY_CHARS,
    REFERENCE_MEMORY_ITEMS,
    REFERENCE_RECENT_CHARS,
    REFERENCE_RECENT_ITEMS,
)
from backend.app.memory.session_memory import memory_view


def trim_conversation(
    items: list[dict] | None,
    *,
    max_items: int = ANSWER_RECENT_ITEMS,
    max_chars: int = ANSWER_RECENT_CHARS,
) -> list[dict]:
    """从最近往前取，按条数和字符预算裁剪；保持原始顺序。"""
    kept: list[dict] = []
    total = 0

    for item in reversed(items or []):
        text = str(item.get("text", ""))
        if not text:
            continue
        if len(kept) >= max_items or total + len(text) > max_chars:
            break
        kept.append(item)
        total += len(text)

    kept.reverse()
    return kept


def assemble_answer_context(state: Any) -> dict:
    """回答节点需要的全部切片。"""
    return {
        "question": state.latest_user_message,
        "collected_facts": state.collected_facts,
        "final_result": state.final_result,
        "recent_conversation": trim_conversation(state.conversation),
        "session_memory": memory_view(
            state.session_memory,
            max_items=ANSWER_MEMORY_ITEMS,
            max_chars=ANSWER_MEMORY_CHARS,
        ),
    }


def assemble_reference_context(state: Any) -> dict:
    """intake / decision 的参考切片。

    只用来理解“刚才说的那个”指什么；抽取仍只认 latest_user_message。
    """
    return {
        "session_memory": memory_view(
            state.session_memory,
            max_items=REFERENCE_MEMORY_ITEMS,
            max_chars=REFERENCE_MEMORY_CHARS,
        ),
        "recent_conversation": trim_conversation(
            state.conversation,
            max_items=REFERENCE_RECENT_ITEMS,
            max_chars=REFERENCE_RECENT_CHARS,
        ),
    }
