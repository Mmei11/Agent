"""用户询问本次会话记录了哪些信息时的确定性回答。

这类问题是“查记录”，不是求治疗建议，不能套用医疗输出护栏：
用户自己的症状里可能包含风险词，模型一复述就会被整段替换。
"""

import re

from backend.app.agent.state import ConsultationState

FIELD_ORDER = (
    "主要症状",
    "持续时间",
    "部位",
    "程度",
    "伴随表现",
    "已知背景",
    "加重缓解因素",
    "时间模式",
    "测量值",
)

SESSION_INFO_PATTERNS = (
    re.compile(r"你(知道|知晓|记得|掌握|记录|收集).{0,3}我(的)?(哪些|什么)(信息|内容|情况)"),
    re.compile(r"你(都)?(记得|记录|收集)(了)?(什么|哪些)"),
    re.compile(r"我(现在|目前)?(的)?症状(有|是)?哪些"),
    re.compile(r"我(现在|目前)?(的)?(有)?哪些症状"),
    re.compile(r"帮我(列举|列一下|总结).{0,8}我的(症状|信息|情况|资料)"),
    re.compile(r"(收集|记录)了(哪些|什么)(信息|内容)"),
    re.compile(r"(还|尚)缺(哪些|什么)(信息|字段)"),
    re.compile(r"我的(信息|资料)"),
)


def is_session_info_query(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in SESSION_INFO_PATTERNS)


def build_answer(state: ConsultationState) -> str:
    facts = state.collected_facts or {}
    lines = ["我目前记录的问诊信息如下："]

    for field in FIELD_ORDER:
        values = facts.get(field)
        if not values:
            continue
        if isinstance(values, list):
            rendered = "、".join(str(value) for value in values)
        else:
            rendered = str(values)
        lines.append(f"- {field}：{rendered}")

    if len(lines) == 1:
        lines.append("目前还没有收集到症状信息。你可以描述一个虚构病例，我会继续追问。")

    missing = state.missing_fields or []
    lines.append(f"尚待确认：{'、'.join(missing) if missing else '无'}")
    lines.append("以上信息来自你本次会话的描述，保存在当前会话和账号历史里。")

    if (state.final_result or {}).get("status") == "risk_route" or state.risk_flags:
        lines.append("提醒：本次会话曾命中急症风险，如相关症状仍在，请尽快就医。")

    return "\n".join(lines)
