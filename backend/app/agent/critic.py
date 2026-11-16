"""Phase E：有界输出审查者（critic）。

它不生成回答，只在最终文案发出前判断“能不能发”：
- 越界：诊断、处方、剂量、停药/换药、治疗方案；
- 编造依据：回答里的 PMID/DOI/NCT 在本轮工具结果里找不到；
- 虚假安心：绝对化排除风险或确定预后。

硬约束：
- 只有 pass / block；block 时走现有安全兜底，不自己写新答案；
- 默认关闭；只在模型模式、completed 的医疗回答上运行；
- 超时、报错、低置信度一律放行，不成为新的单点故障；
- 每次 block 写审计和 trace。
"""

from __future__ import annotations

import json
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.agent.result import build_error_result
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError, LLMResult, chat_structured
from backend.app.observability import current_trace, span
from backend.app.prompts import load_prompt
from backend.app.safety.audit import AuditEvent, input_digest, write as write_audit

PROMPT_NAME = "critic"
PROMPT_VERSION = "v3"
SYSTEM_PROMPT = load_prompt(PROMPT_NAME, PROMPT_VERSION)

FALLBACK_TEXT = "本系统不提供确诊、处方或用药建议，请咨询专业人员。"

PMID_RE = re.compile(r"\bPMID\s*[:：]?\s*(\d{6,9})\b", re.IGNORECASE)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s，。；、)）]+)")
NCT_RE = re.compile(r"\b(NCT\d{8})\b", re.IGNORECASE)


class CriticVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["pass", "block"] = Field(description="pass=放行；block=否决")
    reasons: list[str] = Field(
        default_factory=list,
        description="命中的问题类型：boundary / fabricated_evidence / false_reassurance",
    )
    hint: str = Field(default="", description="给现有安全兜底的方向，不生成最终文案")
    confidence: float = Field(ge=0, le=1, description="自评置信度")


def _enabled() -> bool:
    return (
        os.getenv("CRITIC_ENABLED", "0") == "1"
        and os.getenv("USE_MODEL", "0") == "1"
    )


def _min_confidence() -> float:
    try:
        return float(os.getenv("CRITIC_MIN_CONFIDENCE", "0.6"))
    except ValueError:
        return 0.6


def _citation_set(text: str) -> set[str]:
    found = set()
    found.update(f"PMID:{match}" for match in PMID_RE.findall(text or ""))
    found.update(f"DOI:{match.lower()}" for match in DOI_RE.findall(text or ""))
    found.update(f"NCT:{match.upper()}" for match in NCT_RE.findall(text or ""))
    return found


def _tool_evidence(results: dict, limit: int = 900) -> list[dict]:
    """给 critic 看工具结果的短证据，避免它只凭标识符猜结论。"""
    evidence = []
    remaining = limit
    for name, data in (results or {}).items():
        if remaining <= 0:
            break
        if isinstance(data, dict):
            text = str(data.get("text") or data.get("summary") or "")
            if not text:
                text = json.dumps(data, ensure_ascii=False, default=str)
        else:
            text = str(data)
        text = " ".join(text.split())[: min(300, remaining)]
        if text:
            evidence.append({"tool": name, "evidence": text})
            remaining -= len(text)
    return evidence


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


def _trace_id() -> str:
    scope = current_trace()
    return scope.trace_id if scope else ""


def _last_node(state: ConsultationState) -> str:
    return state.trace[-1].get("node", "") if state.trace else ""


def _should_run(state: ConsultationState) -> tuple[bool, str]:
    if not _enabled():
        return False, "disabled"
    final = state.final_result or {}
    if final.get("status") != "completed":
        return False, "not_completed"
    text = (state.rendered_text or "").strip()
    if not text:
        return False, "empty_answer"
    node = _last_node(state)
    if node == "answer" and state.trace[-1].get("router_intent") == "general":
        return False, "general_answer"
    return True, ""


def _block(state: ConsultationState, *, entry: dict) -> dict:
    structured = build_error_result("输出内容超出安全边界")
    write_audit(
        AuditEvent(
            layer="critic",
            rule="C001",
            action="BLOCK",
            session_id=state.session_id,
            turn_id=state.turn_id,
            input_digest=input_digest(state.latest_user_message),
            detail="; ".join(entry.get("reasons") or []) or "critic block",
        )
    )
    return {
        "final_result": structured,
        "rendered_text": FALLBACK_TEXT,
        "conversation": [{"role": "agent", "text": FALLBACK_TEXT}],
        "trace": [entry],
    }


def _citation_block(state: ConsultationState, answer: str) -> dict | None:
    answer_citations = _citation_set(answer)
    if not answer_citations:
        return None
    tool_citations = _citation_set(
        json.dumps(state.tool_results or {}, ensure_ascii=False, default=str)
    )
    missing = sorted(answer_citations - tool_citations)
    if not missing:
        return None
    return {
        "node": "critic",
        "verdict": "block",
        "blocked": True,
        "source": "citation",
        "reasons": ["fabricated_evidence"],
        "missing_citations": missing,
        "hint": "删除无法在工具结果中找到的文献标识",
        "confidence": 1.0,
        "model_usage": {},
        "trace_id": _trace_id(),
    }


def critic(state: ConsultationState) -> dict:
    should_run, skip_reason = _should_run(state)
    if not should_run:
        return {}

    answer = state.rendered_text.strip()
    citation_entry = _citation_block(state, answer)
    if citation_entry is not None:
        return _block(state, entry=citation_entry)

    context = {
        "user_message": state.latest_user_message,
        "risk_flags": state.risk_flags,
        "result_status": (state.final_result or {}).get("status"),
        "next_step": (state.final_result or {}).get("next_step", ""),
        "answer": answer,
        "tool_citations": sorted(
            _citation_set(
                json.dumps(state.tool_results or {}, ensure_ascii=False, default=str)
            )
        ),
        "tool_evidence": _tool_evidence(state.tool_results),
    }

    entry = {
        "node": "critic",
        "verdict": "pass",
        "blocked": False,
        "source": "model",
        "reasons": [],
        "hint": "",
        "confidence": 0.0,
        "model_usage": {},
        "skip_reason": skip_reason,
        "trace_id": _trace_id(),
    }
    try:
        with span("safety.critic", kind="safety") as record:
            verdict, usage = chat_structured(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(context, ensure_ascii=False),
                    },
                ],
                CriticVerdict,
                max_tokens=250,
                temperature=0.0,
                timeout=8.0,
                prompt_name=PROMPT_NAME,
                prompt_version=PROMPT_VERSION,
            )
            record.attributes["critic.verdict"] = verdict.verdict
            record.attributes["critic.confidence"] = verdict.confidence
    except LLMError as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return {"trace": [entry]}
    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return {"trace": [entry]}

    entry["confidence"] = float(verdict.confidence)
    entry["reasons"] = list(verdict.reasons)
    entry["hint"] = verdict.hint
    entry["model_usage"] = _usage_dict(usage)

    if verdict.verdict == "block" and verdict.confidence >= _min_confidence():
        entry["verdict"] = "block"
        entry["blocked"] = True
        return _block(state, entry=entry)

    if verdict.verdict == "block":
        # 低置信度否决不生效，只留在 trace 里供回看。
        entry["low_confidence_block"] = True
    return {"trace": [entry]}
