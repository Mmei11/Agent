"""Phase D：技能路由（规则版）。

当前只做关键词命中；模型路由留到后续课程。路由只使用注册表摘要，
选中后再由调用方 load_skill() 读取 SKILL.md。
"""

from __future__ import annotations

import os

from backend.app.skills.registry import DEFAULT_NAME, list_skill_summaries


def route(message: str) -> str:
    """返回技能名；没有命中或技能层关闭时返回 default。"""
    if os.getenv("SKILLS_ENABLED", "1") == "0":
        return DEFAULT_NAME

    text = (message or "").strip().lower()
    if not text:
        return DEFAULT_NAME

    best_name = DEFAULT_NAME
    best_score: tuple[int, int] | None = None

    for summary in list_skill_summaries():
        if summary.name == DEFAULT_NAME:
            continue
        hits = sum(
            1
            for keyword in summary.when_to_use
            if keyword and keyword.lower() in text
        )
        if hits <= 0:
            continue
        # 命中越多越优先；同分时 priority 数字小的胜出。
        score = (hits, -summary.priority)
        if best_score is None or score > best_score:
            best_score = score
            best_name = summary.name

    return best_name
