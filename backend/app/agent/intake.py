"""Day 6：结构化信息提取。从用户输入中提取必要字段，区分已知与缺失。案例均为虚构。"""

import os

from backend.app.agent.state import ConsultationState
from backend.app.observability import current_trace, span
from backend.app.safety.input_rail import inspect

REQUIRED_FIELDS = ["主要症状", "持续时间", "部位", "程度", "伴随表现", "已知背景"]

# 可选字段：用户提到就提取，不主动追问。
# 单独列出来，是为了让 missing_fields 只由必需字段驱动，不占用追问额度。
OPTIONAL_FIELDS = ["加重缓解因素", "时间模式", "测量值"]

ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

# 关键词映射：每个字段对应一组触发词
FIELD_KEYWORDS = {
    "主要症状": ["头部不适", "头痛", "头晕", "恶心", "发热", "呼吸困难", "咳嗽", "腹痛"],
    "持续时间": ["两天", "三天", "一周", "一天", "几天", "半个月", "一个月"],
    "部位": ["前额", "头部", "胸部", "腹部", "背部", "左侧", "右侧"],
    "程度": ["轻", "明显", "严重", "剧烈", "轻微"],
    "伴随表现": ["暂无", "恶心", "发热", "胃部不适", "咳嗽", "乏力"],
    "已知背景": ["高血压", "糖尿病", "过敏", "无病史", "既往"],
    "加重缓解因素": ["更明显", "更疼", "加重", "缓解", "就胀", "就喘", "就反酸", "弯腰", "站起来", "爬"],
    "时间模式": ["一阵一阵", "晚上", "夜里", "早上", "每天", "反复", "总醒", "次"],
    "测量值": ["三十八度", "三十九度", "三十七度", "四十度", "℃", "毫米汞柱", "次/分"],
}


def extract_facts(message: str) -> dict:
    """从用户消息中提取已知字段。未匹配到的字段不写入。

    用关键词匹配：消息里包含某字段的关键词，就记入该字段。
    每个字段的值是列表，保留匹配到的原词，方便多轮追加。
    """
    facts = {}
    for field, keywords in FIELD_KEYWORDS.items():
        matched = [kw for kw in keywords if kw in message]
        if matched:
            facts[field] = matched
    return facts


def compute_missing(collected: dict) -> list:
    """计算还缺失的字段：只有必需字段参与，可选字段永远不会变成追问目标。"""
    return [f for f in REQUIRED_FIELDS if f not in collected]


def extract_facts_auto(message: str, meta: dict | None = None) -> tuple[dict, dict]:
    """USE_MODEL=1 时走模型抽取，返回（字段值, 证据）；失败或未启用时回退关键词。

    meta 收集本次用了哪个抽取器和用量，由调用方写进 trace。
    """
    if os.getenv("USE_MODEL") == "1":
        try:
            from backend.app.agent.intake_llm import extract_facts_with_meta

            facts, evidence, usage = extract_facts_with_meta(message)
            if meta is not None:
                meta.update(
                    {
                        "extractor": "llm",
                        "usage": {
                            "model": usage.model,
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "latency_ms": usage.latency_ms,
                            "cost_estimate": usage.cost_estimate,
                        },
                    }
                )
            return facts, evidence
        except Exception as exc:
            # 模型层出任何问题都不能打断问诊，回退关键词并把原因留在 trace。
            if meta is not None:
                meta.update(
                    {
                        "extractor": "keyword",
                        "fallback": True,
                        "error": type(exc).__name__,
                    }
                )
    elif meta is not None:
        meta["extractor"] = "keyword"
    return extract_facts(message), {}


def intake(state: ConsultationState) -> dict:
    """入口节点：先过输入层，再提取字段，合并多轮信息，更新缺失列表。

    输入层在这里而不是单独一个节点：这样 PII 在进入抽取模型和 checkpoint
    之前就已经脱敏，同时不改变现有图的拓扑与测试断言。
    """
    with span("safety.input", kind="safety") as record:
        verdict = inspect(state.latest_user_message)
        record.attributes.update(
            {
                "input.level": verdict.level,
                "input.labels": list(verdict.labels),
                "input.pii": verdict.has_pii,
            }
        )
    message = verdict.redacted_text
    meta = {}
    new_facts, new_evidence = extract_facts_auto(message, meta)

    collected = {k: list(v) for k, v in state.collected_facts.items()}
    for field, values in new_facts.items():
        if field in collected:
            for v in values:
                if v not in collected[field]:
                    collected[field].append(v)
        else:
            collected[field] = list(values)

    evidence = {k: list(v) for k, v in state.fact_evidence.items()}
    for field, fragments in new_evidence.items():
        existing = evidence.setdefault(field, [])
        for fragment in fragments:
            if fragment not in existing:
                existing.append(fragment)

    missing = compute_missing(collected)
    scope = current_trace()

    return {
        "turn_id": state.turn_id + 1,
        "trace_id": scope.trace_id if scope else "",
        # 用脱敏后的文本回写，后续节点和 checkpoint 都不再持有原始 PII。
        "latest_user_message": message,
        "input_level": verdict.level,
        "input_labels": list(verdict.labels),
        "conversation": [{"role": "user", "text": message}],
        "collected_facts": collected,
        "fact_evidence": evidence,
        "missing_fields": missing,
        "final_result": {},
        "rendered_text": "",
        # 只留摘要：字段值已经在 collected_facts 里，这里只记数量与抽取方式。
        "trace": [
            {
                "node": "intake",
                "fact_count": len(new_facts),
                "missing_count": len(missing),
                "input_level": verdict.level,
                "input_labels": list(verdict.labels),
                "trace_id": scope.trace_id if scope else "",
                **meta,
            }
        ],
    }
