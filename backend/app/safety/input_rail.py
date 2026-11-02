"""Phase 5 L1：输入层护栏。

三层防线里的第一层，只处理用户原话。它做四件事：
1. 归一化，避免零宽字符、全角字符绕过后续匹配。
2. 识别越狱/提示注入，这类输入想改的是系统规则，不是描述症状。
3. 标记并脱敏 PII；普通 PII 不拒绝服务。
4. 复用 rule_engine 的红旗/含糊规则，保持现有行为不漂移。
"""

import re
import unicodedata
from dataclasses import dataclass

from backend.app.observability.redact import redact_text
from backend.app.safety.rule_engine import screen

# 零宽字符不占显示宽度，但会把“呼吸困难”切成两个词，必须在匹配前删掉。
ZERO_WIDTH_CHARS = dict.fromkeys((0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF), None)

# 越狱/提示注入模式。每条带规则号，审计和测试都靠它定位。
JAILBREAK_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "I001",
        re.compile(
            r"(?:忽略|无视|忘记|忘掉|抛开).{0,8}(?:之前|上面|前面|所有|你的).{0,8}"
            r"(?:指令|要求|规则|设定|提示|限制)"
        ),
    ),
    ("I002", re.compile(r"(?:开发者模式|越狱模式|god\s*mode|dan\s*模式)", re.IGNORECASE)),
    (
        "I003",
        re.compile(
            r"(?:输出|打印|告诉|复述|展示).{0,8}(?:系统提示|提示词|system\s*prompt|instructions)",
            re.IGNORECASE,
        ),
    ),
    (
        "I004",
        re.compile(
            r"(?:你现在是|从现在开始你(?:是|扮演)|扮演(?:一个)?)[^，。；\n]{0,6}"
            r"(?:医生|医师|大夫|管理员|系统|专家|人工智能|ai)",
            re.IGNORECASE,
        ),
    ),
    (
        "I005",
        re.compile(
            r"(?:不要|别|无需).{0,4}(?:遵守|服从|按照).{0,6}(?:规则|指令|要求|限制)"
            r"|(?:别管|不管).{0,6}(?:规则|指令|要求|限制)"
        ),
    ),
    (
        "I006",
        re.compile(
            r"(?:ignore|disregard|forget).{0,16}(?:previous|above|prior|all).{0,16}"
            r"(?:instructions|rules|prompts)",
            re.IGNORECASE,
        ),
    ),
    (
        "I007",
        re.compile(
            r"(?:act\s+as|you\s+are\s+now|pretend\s+to\s+be).{0,24}"
            r"(?:doctor|admin|system|expert|unrestricted\s+ai)",
            re.IGNORECASE,
        ),
    ),
    (
        "I008",
        re.compile(
            r"(?:系统提示|提示词|system\s*prompt|instructions).{0,8}"
            r"(?:输出|打印|告诉|复述|展示)",
            re.IGNORECASE,
        ),
    ),
]

PII_LABEL = "PII"


@dataclass(frozen=True)
class InputVerdict:
    """输入层的结论。level 是总级别，labels 保留命中的每一条规则。"""

    level: str
    labels: tuple[str, ...]
    reasons: tuple[str, ...]
    redacted_text: str

    @property
    def is_risk(self) -> bool:
        return self.level == "risk"

    @property
    def is_jailbreak(self) -> bool:
        return any(label.startswith("I") for label in self.labels)

    @property
    def has_pii(self) -> bool:
        return PII_LABEL in self.labels


def normalize(text: str) -> str:
    """NFKC + 去零宽 + 小写；只用于匹配，不用于回写。"""
    text = unicodedata.normalize("NFKC", text)
    return text.translate(ZERO_WIDTH_CHARS).lower()


def _jailbreak_hits(text: str) -> list[tuple[str, str]]:
    hits = []
    for rule_id, pattern in JAILBREAK_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append((rule_id, f"命中越狱/注入模式：{match.group(0)}"))
    return hits


def inspect(message: str) -> InputVerdict:
    """检查一条用户消息，返回总级别、规则标签和脱敏后的文本。"""
    text = normalize(message)
    labels: list[str] = []
    reasons: list[str] = []

    redacted = redact_text(message)
    if redacted != message:
        labels.append(PII_LABEL)
        reasons.append("输入包含 PII，已脱敏")

    jailbreak_hits = _jailbreak_hits(text)
    for rule_id, reason in jailbreak_hits:
        labels.append(rule_id)
        reasons.append(reason)

    risk = screen(text)
    if risk["level"] == "risk":
        labels.append(risk["rule"])
        reasons.append(risk["reason"])
        level = "risk"
    elif risk["level"] == "vague":
        labels.append(risk["rule"])
        reasons.append(risk["reason"])
        level = "vague"
    elif jailbreak_hits:
        level = "jailbreak"
    else:
        level = "safe"

    return InputVerdict(
        level=level,
        labels=tuple(labels),
        reasons=tuple(reasons),
        redacted_text=redacted,
    )
