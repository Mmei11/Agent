"""Phase 3 第 4 课补丁：追问相关性的确定性判定。

模型不擅长“比较两个 turn 的先后”。这里把追问映射到字段，
只扫描该提问之前的用户原话，判断有效 / 重复 / 用户拒绝后仍追问。
judge 只负责有据与边界两个需要语义判断的维度。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.app.agent.followup import QUESTION_TEMPLATES

FIELD_PRIORITY = {
    "主要症状": 1,
    "持续时间": 2,
    "部位": 3,
    "伴随表现": 4,
    "程度": 5,
    "已知背景": 6,
}

TEMPLATE_TO_FIELD = {question: field for field, question in QUESTION_TEMPLATES.items()}

# 长词放前面，避免“不舒服”被“不适”抢先匹配。
FIELD_PATTERNS = {
    "主要症状": re.compile(
        r"呼吸困难|不舒服|酸痛|疼痛|头痛|头晕|恶心|发热|咳嗽|腹痛|麻木|乏力|"
        r"心慌|胸闷|腹泻|拉肚子|便秘|出血|不适|疼|痛|酸|胀|痒|晕|喘"
    ),
    "持续时间": re.compile(
        r"\d+|半个月|一个月|一阵子|两天|三天|一周|一天|几天|今天|昨天|前天|"
        r"刚才|最近|持续|小时|分钟"
    ),
    "部位": re.compile(
        r"头部|额头|颈部|肩部|胸前|胸口|腹部|胃部|腿部|腰部|背部|喉咙|膝盖|"
        r"头|额|颈|肩|胸|腹|胃|腿|腰|背|手|脚|眼|耳|鼻|膝|肘"
    ),
    "伴随表现": re.compile(
        r"(?<!有)没有其他|暂无|无其他|没别的|没有别的|没有不适|没有不舒服|没其他"
    ),
    "程度": re.compile(
        r"轻度|中度|重度|轻微|明显|严重|剧烈|比较轻|比较重|程度轻|程度中等|"
        r"还好|不太|轻|中|重"
    ),
    "已知背景": re.compile(
        r"无病史|没有病史|没病史|无用药|没有用药|没在用药|无过敏|没有过敏|"
        r"不记得|不清楚"
    ),
}

GENERIC_REFUSAL = re.compile(r"拒绝|不想提供|不提供|不想说|无法提供|只想了解|不想继续")

FIELD_REFUSAL = {
    "持续时间": re.compile(
        r"(不知道|不清楚|说不清|记不清).{0,4}(多久|持续|时间)"
        r"|(多久|持续|时间).{0,4}(不知道|不清楚|说不清|记不清)"
    ),
    "部位": re.compile(
        r"(不知道|不清楚|说不清|记不清).{0,4}(部位|位置|哪里)"
        r"|(部位|位置|哪里).{0,4}(不知道|不清楚|说不清|记不清)"
    ),
    "程度": re.compile(
        r"(不知道|不清楚|说不清).{0,4}(程度|多严重)"
        r"|(程度|多严重).{0,4}(不知道|不清楚|说不清)"
    ),
    "伴随表现": re.compile(r"(不知道|不清楚).{0,6}(其他|伴随|表现)"),
    "已知背景": re.compile(r"(不知道|不清楚).{0,6}(病史|用药|背景)"),
}

NEGATION = re.compile(r"没有|无|不|否认|未")


@dataclass(frozen=True)
class FollowupCheck:
    turn: int
    field: str
    status: str  # valid / repeated / invalid
    evidence: str


def _short(text: str, limit: int = 30) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _mentioned(field: str, text: str) -> bool:
    pattern = FIELD_PATTERNS[field]
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 4) : match.start()]
        if NEGATION.search(prefix):
            continue
        return True
    return False


def _refused_fields(text: str) -> set[str]:
    if GENERIC_REFUSAL.search(text):
        return set(FIELD_PRIORITY)
    return {
        field
        for field, pattern in FIELD_REFUSAL.items()
        if pattern.search(text)
    }


def _prior_user_turns(conversation: list[dict], turn: int) -> list[str]:
    return [
        entry.get("text", "")
        for entry in conversation[: turn - 1]
        if entry.get("role") == "user"
    ]


def analyze_followups(conversation: list[dict]) -> list[FollowupCheck]:
    checks: list[FollowupCheck] = []
    for turn, entry in enumerate(conversation, start=1):
        if entry.get("role") != "agent":
            continue
        text = entry.get("text", "").strip()
        if not text.endswith("？"):
            continue
        field = next(
            (field for template, field in TEMPLATE_TO_FIELD.items() if template in text),
            None,
        )
        if field is None:
            continue

        prior_turns = _prior_user_turns(conversation, turn)
        provided = next(
            (text for text in prior_turns if _mentioned(field, text)),
            None,
        )
        if provided:
            checks.append(
                FollowupCheck(turn, field, "repeated", _short(provided))
            )
            continue

        refused = _refused_fields("；".join(prior_turns))
        if field in refused:
            checks.append(FollowupCheck(turn, field, "invalid", _short(prior_turns[-1])))
            continue

        checks.append(FollowupCheck(turn, field, "valid", "无"))
    return checks


def score_followups(checks: list[FollowupCheck]) -> dict:
    """把逐条判定折成 1-5 分；规则可审计，不依赖模型。"""
    if not checks:
        return {
            "score": 5,
            "reason": "系统追问 0 条：本案例未产生追问",
            "evidence": "无",
        }

    status_text = {
        "valid": "有效",
        "repeated": "重复",
        "invalid": "用户已拒绝/不知道后仍追问",
    }
    parts = [
        f"追问{check.turn}（{check.field}）：{status_text[check.status]}，"
        f"依据：{check.evidence}"
        for check in checks
    ]
    reason = f"系统追问 {len(checks)} 条：" + "；".join(parts)
    evidence = next(
        (
            check.evidence
            for check in checks
            if check.status != "valid" and check.evidence != "无"
        ),
        "无",
    )

    if len(checks) > 3:
        score = 1
    elif (
        sum(check.status == "invalid" for check in checks) >= 1
        or sum(check.status == "repeated" for check in checks) >= 2
    ):
        score = 2
    elif any(check.status == "repeated" for check in checks):
        score = 3
    else:
        indices = [
            FIELD_PRIORITY[check.field]
            for check in checks
            if check.field in FIELD_PRIORITY
        ]
        out_of_order = any(
            earlier > later for earlier, later in zip(indices, indices[1:])
        )
        score = 4 if out_of_order else 5

    return {"score": score, "reason": reason, "evidence": evidence}