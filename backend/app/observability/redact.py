"""Phase 2 第三课：采集端 PII 脱敏。

原则：脱敏发生在 span 进入任何 sink 之前，而不是展示端。
即使以后有人把用户原话塞进 attributes，落盘的也只会是脱敏后的版本。
"""

import re
from dataclasses import replace

# 中国大陆手机号：1 开头，第二位 3–9，共 11 位。
# 前后加数字边界，避免把 18 位身份证里的片段误判成手机号。
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# 18 位身份证：地区码 + 出生日期 + 顺序码 + 校验位。
ID_CARD_RE = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 只处理“标签 + 姓名”这种明确写法，避免把普通两字词误当人名。
NAME_LABEL_RE = re.compile(r"(?P<label>姓名|患者|病人)\s*[:：]\s*(?P<name>[一-龥]{2,4})")
SELF_NAME_RE = re.compile(r"(?P<label>我叫|名字叫)\s*(?P<name>[一-龥]{2,4})")

# 这些键名的值默认整段抹掉；比逐字匹配更安全，也避免漏掉变体。
SENSITIVE_KEYS = {
    "phone",
    "mobile",
    "tel",
    "telephone",
    "id_card",
    "idcard",
    "id_number",
    "email",
    "patient_name",
    "user_name",
    "real_name",
    "full_name",
    "latest_user_message",
    "user_input",
    "message",
    "conversation",
}

REDACTED = "[REDACTED]"


def redact_text(text: str) -> str:
    """替换自由文本里的手机号、身份证、邮箱和显式姓名。"""
    if not isinstance(text, str):
        return text
    text = ID_CARD_RE.sub("[身份证]", text)
    text = PHONE_RE.sub("[手机号]", text)
    text = EMAIL_RE.sub("[邮箱]", text)
    text = NAME_LABEL_RE.sub(lambda match: f"{match.group('label')}：[姓名]", text)
    text = SELF_NAME_RE.sub(lambda match: f"{match.group('label')}[姓名]", text)
    return text


def redact_value(value, key: str | None = None):
    """递归处理字典和列表；敏感键直接整段抹掉。"""
    if key is not None and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {item_key: redact_value(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


def redact_record(record):
    """返回脱敏后的 span 副本，不修改业务正在使用的原对象。"""
    return replace(
        record,
        attributes=redact_value(record.attributes),
        error=redact_text(record.error) if record.error else None,
    )