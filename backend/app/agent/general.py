"""普通问题的确定性工具：日期回答与安全校验。

普通/医疗的路由由 router.py 的模型完成，这里不再维护关键词表。
"""

import re
from datetime import datetime, timedelta, timezone

from backend.app.drugs.lookup import load_labels
from backend.app.safety.rule_engine import screen

GENERAL_FALLBACK = (
    "我是 Consult/Lab 的模拟问诊助手，可以帮你整理虚构病例的就医方向，"
    "也能查询 OTC 药品说明书。医疗问题请咨询专业医生。"
)

# 展示用北京时间；容器默认 UTC，日期问题不能跟着错。
DISPLAY_TZ = timezone(timedelta(hours=8))
WEEKDAY_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

# 普通回复也不允许变成处方或推荐。
UNSAFE_GENERAL_PATTERNS = (
    re.compile(r"建议(服用|使用|吃)"),
    re.compile(r"推荐(服用|使用|吃)"),
    re.compile(r"(你|您)可以(吃|服用|使用)"),
    re.compile(r"(每次|每日|一日|一次)"),
    re.compile(r"\d+\s*(mg|毫克|g|克|ml|毫升|片|粒|袋|次)", re.IGNORECASE),
    re.compile(r"确诊|诊断为"),
    re.compile(r"抑郁症|焦虑症|精神疾病|心理疾病"),
)

# 就医准备类问题：这类问题该走 general，但需要调用 guideline_retrieve，
# 不能只靠模型常识回答。
GUIDELINE_PATTERNS = (
    re.compile(r"(就诊|就医|去医院|挂号|门诊)[^。！？\n]{0,12}(准备|带|材料|资料|流程|手续)"),
    re.compile(r"(准备|带)[^。！？\n]{0,8}(什么|哪些)[^。！？\n]{0,6}(材料|资料|东西)"),
    re.compile(r"第一次[^。！？\n]{0,6}(医院|就诊|看病)"),
)


def is_guideline_question(message: str) -> bool:
    """判断是否在问就医准备/挂号流程。"""
    text = (message or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in GUIDELINE_PATTERNS)


def display_now() -> datetime:
    return datetime.now(DISPLAY_TZ)


def is_date_question(message: str) -> bool:
    return any(
        phrase in message
        for phrase in (
            "几月几号",
            "今天几号",
            "今天是什么日子",
            "什么日子",
            "星期几",
            "日期",
            "现在几点",
            "今天是什么",
        )
    )


def date_answer(message: str) -> str:
    now = display_now()
    weekday = WEEKDAY_CN[now.weekday()]
    if "几点" in message or "时间" in message:
        return f"现在是 {now:%Y-%m-%d %H:%M}，{weekday}（北京时间）。"
    return f"今天是 {now:%Y-%m-%d}，{weekday}。"


def general_answer_is_safe(text: str) -> bool:
    text = text.strip()
    if not 5 <= len(text) <= 300:
        return False
    if any(pattern.search(text) for pattern in UNSAFE_GENERAL_PATTERNS):
        return False
    if screen(text)["level"] != "safe":
        return False
    lowered = text.lower()
    for label in load_labels():
        if any(alias.lower() in lowered for alias in label.aliases):
            return False
    return True
