"""Phase E：decision 之前的语义风险识别。

规则词表能抓住“呼吸困难”这类明确表述，但抓不住
“胸口像被石头压着，冷汗一直冒”。这个模块只在规则判为安全或含糊、
且用户像是在描述当前症状时，才让模型做一次分诊判断。

设计约束：
- 默认关闭（SEMANTIC_RISK_ENABLED=0），只影响显式开启的模型模式；
- 只输出分诊级别，不做诊断、不给处置；
- 模型失败视为“没有额外结论”，不阻断主流程；
- 证据必须逐字出现在用户原话里，否则丢弃。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.llm.client import LLMError, LLMResult, chat_structured
from backend.app.observability import span
from backend.app.prompts import load_prompt

PROMPT_NAME = "semantic_risk"
PROMPT_VERSION = "v1"
SYSTEM_PROMPT = load_prompt(PROMPT_NAME, PROMPT_VERSION)

# 只有像是在描述“当前/最近的身体不适”才值得多花一次模型调用。
SYMPTOM_TERMS = (
    "痛", "疼", "晕", "喘", "咳", "吐", "烧", "发热", "出血", "麻", "无力",
    "抽", "昏迷", "叫不醒", "胸闷", "心悸", "肚子", "腹", "胸", "头",
    "呼吸", "意识", "黑便", "便血", "肿", "抖", "站不稳", "看不清",
    "说话", "手脚", "脸", "嘴", "腹泻", "乏力", "不舒服",
)

# 科普/就医准备类问句：没有“我/孩子/最近”这类个人化线索时，不触发。
EDUCATION_PATTERNS = (
    re.compile(r"(哪些|什么|哪种)[^。！？\n]{0,8}(情况|症状|表现|信号)"),
    re.compile(r"(怎么办|如何处理|如何判断|有什么区别|是什么意思)"),
    re.compile(r"(准备|带)[^。！？\n]{0,8}(什么|哪些)"),
)

PERSONAL_MARKERS = (
    "我", "孩子", "宝宝", "娃", "家人", "老人", "他", "她", "自己",
    "最近", "今天", "刚才", "突然", "这两天", "昨晚", "现在", "一直",
)


class SemanticRiskVerdict(BaseModel):
    """模型只回答分诊级别，不做诊断。"""

    model_config = ConfigDict(extra="forbid")

    risk: Literal["emergency", "urgent", "routine", "not_medical"] = Field(
        description="emergency=立即急救/急诊；urgent=尽快就医；routine=普通门诊；not_medical=非症状描述"
    )
    confidence: float = Field(ge=0, le=1, description="自评置信度")
    evidence: list[str] = Field(
        default_factory=list, description="用户原话中的逐字证据，最多 3 条"
    )


@dataclass(frozen=True)
class SemanticRiskOutcome:
    risk: str = "routine"  # emergency / urgent / uncertain / routine
    confidence: float = 0.0
    evidence: list[str] | None = None
    checked: bool = False
    skip_reason: str = ""
    error: str = ""
    usage: dict | None = None

    @property
    def is_emergency(self) -> bool:
        return self.risk == "emergency"

    @property
    def is_uncertain_or_urgent(self) -> bool:
        return self.risk in {"urgent", "uncertain"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def enabled() -> bool:
    return (
        os.getenv("SEMANTIC_RISK_ENABLED", "0") == "1"
        and os.getenv("USE_MODEL", "0") == "1"
    )


def looks_like_symptom_message(message: str) -> bool:
    """便宜的触发条件，避免每句普通聊天都多打一次模型。"""
    text = (message or "").strip()
    if len(text) < 4:
        return False
    if not any(term in text for term in SYMPTOM_TERMS):
        return False
    if any(pattern.search(text) for pattern in EDUCATION_PATTERNS):
        # “头痛时哪些情况要急诊”是科普；有“我/孩子/最近”等线索才按个人症状处理。
        if not any(marker in text for marker in PERSONAL_MARKERS):
            return False
    return True


def _usage_dict(usage: LLMResult | None) -> dict:
    if usage is None:
        return {}
    return {
        "model": usage.model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "latency_ms": usage.latency_ms,
        "cost_estimate": usage.cost_estimate,
    }


def _clean_evidence(message: str, evidence: list[str]) -> list[str]:
    """丢弃模型编造的证据，只保留用户原话里逐字出现的片段。"""
    cleaned = []
    for item in evidence:
        item = str(item or "").strip()
        if item and item in message and item not in cleaned:
            cleaned.append(item)
        if len(cleaned) >= 3:
            break
    return cleaned


def assess(message: str) -> SemanticRiskOutcome:
    """调用一次语义分诊；任何失败都返回无额外结论，不阻断主流程。"""
    if not enabled():
        return SemanticRiskOutcome(skip_reason="disabled")
    if not looks_like_symptom_message(message):
        return SemanticRiskOutcome(skip_reason="not_symptom_message")

    try:
        with span(
            "safety.semantic_risk",
            kind="safety",
            attributes={"semantic_risk.enabled": True},
        ) as record:
            verdict, usage = chat_structured(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"message": message}, ensure_ascii=False
                        ),
                    },
                ],
                SemanticRiskVerdict,
                max_tokens=200,
                temperature=0.0,
                timeout=8.0,
                prompt_name=PROMPT_NAME,
                prompt_version=PROMPT_VERSION,
            )
            record.attributes["semantic_risk.verdict"] = verdict.risk
            record.attributes["semantic_risk.confidence"] = verdict.confidence
    except LLMError as exc:
        return SemanticRiskOutcome(
            checked=True, error=f"{type(exc).__name__}: {exc}"
        )
    except Exception as exc:
        return SemanticRiskOutcome(
            checked=True, error=f"{type(exc).__name__}: {exc}"
        )

    confidence = float(verdict.confidence)
    min_confidence = _env_float("SEMANTIC_RISK_MIN_CONFIDENCE", 0.5)
    evidence = _clean_evidence(message, verdict.evidence)

    if verdict.risk == "emergency":
        # 紧急但置信度不足时仍保守降级为“尽快就医”，不按安全放行。
        risk = "emergency" if confidence >= min_confidence else "uncertain"
    elif verdict.risk == "urgent":
        risk = "urgent" if confidence >= min_confidence else "routine"
    else:
        risk = "routine"

    return SemanticRiskOutcome(
        risk=risk,
        confidence=confidence,
        evidence=evidence,
        checked=True,
        usage=_usage_dict(usage),
    )
