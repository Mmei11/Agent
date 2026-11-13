"""Phase 1 第二课：模型结构化抽取。案例均为虚构。"""

from backend.app.llm.client import LLMResult, chat_structured
from backend.app.llm.schemas import FIELD_MAP, IntakeFacts
from backend.app.safety.rule_engine import check_negation
from backend.app.prompts import load_prompt

# 症状类字段才需要"证据本身以否定词开头"的额外检查。
# 背景字段的"无病史""没有过敏史"是有效信息，不能当否定丢掉。
SYMPTOM_FIELDS = {"chief_complaint", "associated"}

# 只认多字否定前缀，避免把"没什么力气"这种症状误判成否定。
# 单字"不/没/无"交给 check_negation 看前面的窗口来判。
NEGATION_PREFIXES = ("没有", "无明显", "未出现", "否认", "不是")

# 观测用的 prompt 标识：改 prompt 时同步升版本，才能按版本对比指标。
PROMPT_NAME = "intake.extract"
PROMPT_VERSION = "v3"

# “加重缓解因素”只接受带这些线索的原文片段；单纯提到吃药/治疗不算。
FACTOR_CUES = (
    "加重",
    "缓解",
    "更疼",
    "更痛",
    "更明显",
    "减轻",
    "好转",
    "好多了",
    "就胀",
    "就喘",
    "就反酸",
    "弯腰",
    "站起来",
    "爬",
    "走路",
    "活动",
    "运动",
    "休息",
    "饭后",
    "空腹",
    "夜里",
    "早上",
    "白天",
    "天气",
    "低头",
    "抬头",
    "转头",
    "久坐",
    "久站",
    "咳嗽",
    "打喷嚏",
    "吃药后",
    "服药后",
)

SYSTEM_PROMPT = load_prompt(PROMPT_NAME, PROMPT_VERSION)


def _is_negated(text: str, fact, field: str) -> bool:
    """值或证据只要有一个被否定，这个字段就不算数。"""
    for fragment in (fact.value.strip(), fact.evidence.strip()):
        if not fragment:
            continue
        if field in SYMPTOM_FIELDS and fragment.startswith(NEGATION_PREFIXES):
            return True
        if check_negation(text, fragment):
            return True
    return False


def keep_supported(facts: IntakeFacts, text: str) -> IntakeFacts:
    """三道闸门：证据逐字存在、value 也在原文、不被否定；空列表表示作废。"""
    cleaned = {}
    for field in FIELD_MAP:
        kept = []
        for fact in getattr(facts, field):
            evidence = fact.evidence.strip()
            value = fact.value.strip()
            if not evidence or evidence not in text:
                continue  # 证据不在原文里 → 编造，丢弃
            if not value or value not in text:
                continue  # value 不在原文里 → 把归纳结论当事实，丢弃
            if field == "aggravating" and not any(cue in value for cue in FACTOR_CUES):
                continue  # 只是“吃了某种药”，不是加重/缓解因素
            if _is_negated(text, fact, field):
                continue  # 证据被否定 → 不计入
            kept.append(fact)
        cleaned[field] = kept
    return IntakeFacts(**cleaned)


def to_evidence_dict(facts: IntakeFacts) -> dict:
    """证据按中文字段名归档，空证据不写入。"""
    result = {}
    for field, chinese_name in FIELD_MAP.items():
        evidences = []
        for fact in getattr(facts, field):
            evidence = fact.evidence.strip()
            if evidence and evidence not in evidences:
                evidences.append(evidence)
        if evidences:
            result[chinese_name] = evidences
    return result


def to_field_dict(facts: IntakeFacts) -> dict:
    """转成评测脚本和 state 使用的中文键名，空字段不写入。"""
    result = {}
    for field, chinese_name in FIELD_MAP.items():
        values = []
        for fact in getattr(facts, field):
            value = fact.value.strip()
            if value and value not in values:
                values.append(value)
        if values:
            result[chinese_name] = values
    return result


def extract_facts_with_meta(text: str) -> tuple[dict, dict, LLMResult]:
    """抽取并返回值、证据和用量。值和证据必须来自同一个 cleaned。"""
    facts, usage = chat_structured(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        IntakeFacts,
        max_tokens=800,
        prompt_name=PROMPT_NAME,
        prompt_version=PROMPT_VERSION,
    )
    cleaned = keep_supported(facts, text)
    return to_field_dict(cleaned), to_evidence_dict(cleaned), usage


def extract_facts(text: str) -> dict:
    """评测脚本的入口：与关键词版同名同形状，方便 A/B 对比。"""
    return extract_facts_with_meta(text)[0]
