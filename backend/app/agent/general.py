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


# 红旗科普类问题：问的是"什么情况要急诊/有哪些危险信号"，
# 不是描述自己的症状。这类问题可以调用 red_flag_lookup 列出警示，
# 但不能反过来判定用户"没有风险"。
RED_FLAG_PATTERNS = (
    re.compile(
        r"(什么|哪些|哪种)[^。！？\n]{0,8}(情况|症状|表现)[^。！？\n]{0,12}"
        r"(立即|马上|赶紧|急诊|就医|去医院|危险)"
    ),
    re.compile(r"(危险信号|红旗|预警信号|紧急情况)"),
    re.compile(r"(需要|要)[^。！？\n]{0,4}(立即|马上)[^。！？\n]{0,4}(就医|去医院|急诊)"),
    re.compile(r"(算不算|是不是)[^。！？\n]{0,8}(危险|红旗|急诊|紧急)"),
)

RED_FLAG_FALLBACK = (
    "暂时无法确认具体的危险信号；如出现明显不适或症状加重，请及时就医。"
)

# 红旗科普允许出现"胸痛、意识不清"这类词，所以不能复用 general_answer_is_safe
# 的 screen 闸门；这里只挡住处方类表达和安抚性结论。
RED_FLAG_REASSURANCE = ("不用担心", "没有危险", "无需就医", "不用就医", "可以观察")


def is_red_flag_question(message: str) -> bool:
    """判断是否在问"什么情况算危险/要急诊"。"""
    text = (message or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in RED_FLAG_PATTERNS)


def red_flag_answer_is_safe(text: str) -> bool:
    text = text.strip()
    if not 5 <= len(text) <= 300:
        return False
    if any(pattern.search(text) for pattern in UNSAFE_GENERAL_PATTERNS):
        return False
    if any(phrase in text for phrase in RED_FLAG_REASSURANCE):
        return False
    return "立即就医" in text or "及时就医" in text


# 外部工具类问题：药品说明书与文献检索。
DRUG_LABEL_PATTERNS = (
    re.compile(r"(说明书|药品说明|用药说明|药品标签|药物标签)"),
    re.compile(r"\b(fda|openfda)\b", re.IGNORECASE),
    re.compile(r"(药品禁忌|用药禁忌|不良反应|药品警示|用药警示)"),
)

RESEARCH_PATTERNS = (
    re.compile(r"\bpubmed\b", re.IGNORECASE),
    re.compile(
        r"(有没有|有哪些|有什么|有何|查一下|搜一下|找一下|检索)"
        r"[^。！？\n]{0,12}(研究|文献|论文|证据|指南|临床试验)"
    ),
    re.compile(
        r"(研究|文献|论文|指南|临床试验)[^。！？\n]{0,6}"
        r"(显示|表明|支持|怎么说|有哪些|有什么)"
    ),
)

# 外部文案的安全闸门：比普通回答多挡一层英文剂量表达。
EXTERNAL_DOSAGE_PATTERNS = (
    re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:mg|ml|g|mcg|tablet|tablets|capsule|capsules)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\d+\s*(?:片|粒|毫克|毫升|克)"),
    re.compile(r"\b\d+\s*(?:dose|doses|pill|pills)\b", re.IGNORECASE),
    re.compile(r"\b(take|taking)\b[^.]{0,40}\b(every|hours|days)\b", re.IGNORECASE),
)


def is_drug_label_question(message: str) -> bool:
    """是否在问某份药品说明书。具体有没有药名由调用方再判断。"""
    text = (message or "").strip()
    return bool(text) and any(pattern.search(text) for pattern in DRUG_LABEL_PATTERNS)


def is_research_question(message: str) -> bool:
    """是否在明确要文献/研究证据，而不是问症状。"""
    text = (message or "").strip()
    return bool(text) and any(pattern.search(text) for pattern in RESEARCH_PATTERNS)


def external_answer_is_safe(text: str) -> bool:
    """外部结果的组织文案：必须带来源、不得出现剂量或处方建议。"""
    text = text.strip()
    if not 5 <= len(text) <= 900:
        return False
    if any(pattern.search(text) for pattern in UNSAFE_GENERAL_PATTERNS):
        return False
    if any(pattern.search(text) for pattern in EXTERNAL_DOSAGE_PATTERNS):
        return False
    return "openFDA" in text or "PubMed" in text


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
