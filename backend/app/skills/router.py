"""Phase D：技能路由。

三种模式，由 SKILL_ROUTER_MODE 控制：
- rule：只跑关键词规则（默认同 hybrid 的确定性部分）
- model：只问模型，失败回退 default
- hybrid：规则能明确命中就用规则，否则再问模型

模型只看到技能摘要（名字/标题/一句话描述），不加载 SKILL.md。
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from backend.app.llm.client import LLMError, chat_structured
from backend.app.skills.registry import DEFAULT_NAME, list_skill_summaries

PROMPT_NAME = "skill.route"
PROMPT_VERSION = "v1"


class SkillChoice(BaseModel):
    skill: str = Field(description="选中的技能名；没有合适的就选 default")
    reason: str = Field(default="", description="一句话说明依据")
    confidence: float = Field(default=0.0, ge=0, le=1)


def route_rule(message: str) -> str:
    """关键词命中数优先，同分时 priority 小的胜出。"""
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
        score = (hits, -summary.priority)
        if best_score is None or score > best_score:
            best_score = score
            best_name = summary.name

    return best_name


def route_model(message: str) -> str:
    """让模型从技能摘要里选；失败、越界或未启用模型时回退 default。"""
    if os.getenv("USE_MODEL") != "1":
        return DEFAULT_NAME

    summaries = [
        summary for summary in list_skill_summaries() if summary.name != DEFAULT_NAME
    ]
    if not summaries:
        return DEFAULT_NAME

    catalog = "\n".join(
        f"- {summary.name}（{summary.title}）：{summary.description}"
        for summary in summaries
    )
    system = (
        "你是问诊技能路由器。根据用户消息，从下列技能中选择最合适的一个。\n"
        f"{catalog}\n"
        f"- {DEFAULT_NAME}（通用问诊）：没有命中特定病种时使用。\n\n"
        "规则：\n"
        "1. 用户描述具体症状时，优先选最匹配的专科技能；\n"
        "2. 儿童或婴幼儿伴发热，优先 pediatric_fever；\n"
        "3. 拿不准、只是普通健康问题或信息不足时选 default；\n"
        "4. 只输出 JSON，不要 Markdown 或额外字段。"
    )

    try:
        choice, _ = chat_structured(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
            SkillChoice,
            max_tokens=200,
            prompt_name=PROMPT_NAME,
            prompt_version=PROMPT_VERSION,
        )
    except LLMError:
        return DEFAULT_NAME

    names = {summary.name for summary in summaries} | {DEFAULT_NAME}
    return choice.skill if choice.skill in names else DEFAULT_NAME


def route(message: str) -> str:
    """按 SKILL_ROUTER_MODE 选择技能；技能层关闭时永远 default。"""
    if os.getenv("SKILLS_ENABLED", "1") == "0":
        return DEFAULT_NAME

    text = (message or "").strip()
    if not text:
        return DEFAULT_NAME

    mode = os.getenv("SKILL_ROUTER_MODE", "hybrid").strip().lower()
    if mode == "rule":
        return route_rule(text)
    if mode == "model":
        return route_model(text)
    if mode == "hybrid":
        rule_choice = route_rule(text)
        return rule_choice if rule_choice != DEFAULT_NAME else route_model(text)
    # 配置写错时按最保守的规则路径处理。
    return route_rule(text)
