"""Phase B：会话记忆卡。

只回答一个问题：**哪些用户细节没进结构化字段，但跨轮之后还必须记得？**

例如：
- “我更喜欢下午去” → 时间偏好
- “我下周要出差” → 行程约束
- “我坐地铁过来比较方便” → 交通方式

这些都不会进 collected_facts，但用户后面会问“我刚才说的那个……”。
记忆卡只存这些细节，不复制事实和结论：
- 事实仍以 collected_facts 为准
- 结论仍以 final_result 为准

v1 用确定性规则筛选，先保证可解释、可测试；Phase D 可以换成模型抽取。
"""

from __future__ import annotations

from typing import Any

# 最多保留多少条细节；超出后丢最旧的。
DETAIL_MAX_ITEMS = 10
# 单条细节最多保留多少字，避免把整段长文塞进来。
DETAIL_MAX_CHARS = 120

# 句末是这些字符时，视为“提问”而不是“陈述”。
QUESTION_ENDINGS = ("?", "？", "吗", "吗？", "呢", "呢？")

# 提问句里如果出现这些个人细节线索，仍然值得记下来。
DETAIL_HINTS = (
    "更喜欢",
    "最好",
    "希望",
    "想先",
    "想去",
    "想在",
    "下周",
    "下个月",
    "明天",
    "后天",
    "周末",
    "上午",
    "下午",
    "晚上",
    "地铁",
    "公交",
    "开车",
    "打车",
    "走路",
    "同事",
    "朋友",
    "家人",
    "孩子",
    "宝宝",
    "出差",
    "出国",
    "上班",
    "体检",
    "隐形眼镜",
    "方便",
    "推荐",
)


def _is_pure_question(message: str) -> bool:
    return message.rstrip().endswith(QUESTION_ENDINGS)


def should_store_detail(message: str, new_facts: dict) -> bool:
    """这条消息是否值得作为“非字段细节”记住。

    规则很克制：有结构化事实、不是第一人称、或只是记忆指代追问，都不存。
    """
    text = (message or "").strip()
    if not text:
        return False
    has_hint = any(hint in text for hint in DETAIL_HINTS)
    # 抽到了字段也可能同时带着个人细节（例如“我下周要出差”抽到“下周”），
    # 有明确线索时仍然保留；纯症状描述才跳过。
    if new_facts and not has_hint:
        return False
    if "我" not in text:
        return False
    # “我刚才说什么”本身是追问，不是新细节。
    from backend.app.agent.memory_ref import is_memory_reference

    if is_memory_reference(text):
        return False
    # 纯提问不存；除非句子里出现了明确的个人细节线索。
    if _is_pure_question(text) and not has_hint:
        return False
    return True


def update_memory(
    existing: dict | None,
    *,
    message: str,
    new_facts: dict,
    missing_fields: list,
    turn_id: int,
) -> dict:
    """在 intake 之后更新记忆卡。事实与结论不在这里复制。"""
    memory = dict(existing or {})
    details = [dict(item) for item in memory.get("details") or []]

    if should_store_detail(message, new_facts):
        text = message.strip()[:DETAIL_MAX_CHARS]
        known = {item.get("text") for item in details}
        if text and text not in known:
            details.append({"text": text, "turn": turn_id})

    details = details[-DETAIL_MAX_ITEMS:]
    memory["details"] = details
    memory["open_questions"] = list(missing_fields or [])
    if details:
        memory["recent_topic"] = details[-1].get("text", "")
    memory["turns"] = turn_id
    return memory


def memory_view(
    memory: dict | None,
    *,
    max_items: int = 8,
    max_chars: int = 600,
) -> dict:
    """给回答节点的紧凑视图：最近优先，按条数与字符预算裁剪。"""
    source = list((memory or {}).get("details") or [])
    kept: list[dict] = []
    total = 0

    for item in reversed(source):
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if len(kept) >= max_items or total + len(text) > max_chars:
            break
        kept.append({"text": text, "turn": item.get("turn")})
        total += len(text)

    kept.reverse()
    return {
        "details": kept,
        "open_questions": list((memory or {}).get("open_questions") or [])[:6],
        "recent_topic": (memory or {}).get("recent_topic", ""),
    }
