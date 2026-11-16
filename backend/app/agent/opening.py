"""图内预路由：任何未出结果的轮次，先判断普通/医疗。

普通问题直接回复（没有收集到信息时附带邀请描述），医疗问题回到 decision
继续字段收集。这样问候、倾诉、流程问题不会在中间轮次被当成症状追问。
"""

import os
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from backend.app.agent.followup import REPLY_OPTIONS
from backend.app.agent.general import (
    CONDITION_ALIASES,
    DRUG_NAME_ALIASES,
    GENERAL_FALLBACK,
    RED_FLAG_FALLBACK,
    external_answer_is_safe,
    general_answer_is_safe,
    is_adverse_event_question,
    is_approval_question,
    is_clinical_trial_question,
    is_drug_label_question,
    is_guideline_question,
    is_health_topic_question,
    is_recall_question,
    is_red_flag_question,
    is_research_question,
    is_shortage_question,
    looks_like_followup_answer,
    red_flag_answer_is_safe,
    translated_external_is_safe,
)
from backend.app.agent.router import route as route_reply
from backend.app.agent.state import ConsultationState
from backend.app.agent.translate import localize_external
from backend.app.drugs.lookup import load_labels
from backend.app.llm.client import LLMError, LLMResult, chat
from backend.app.observability import current_trace
from backend.app.tools.catalog import DEFAULT_CATALOG
from backend.app.tools.external_summaries import (
    markdown_text,
    medlineplus_payload,
    parse_openfda_label,
    pubmed_counts,
    safe_snippet,
)
from backend.app.tools.registry import call_tool

OPENING_INVITE = (
    "今天身体状态怎么样？如果有不舒服，可以告诉我具体哪里不舒服、持续多久，"
    "我帮你整理就医方向。"
)


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


def _tool_query(state: ConsultationState) -> str:
    facts = []
    for values in (state.collected_facts or {}).values():
        if isinstance(values, list):
            facts.extend(str(value) for value in values if value)
        elif values:
            facts.append(str(values))
    return " ".join([state.latest_user_message.strip(), *facts]).strip()


def answer_guideline(state: ConsultationState, *, node: str = "general_route") -> dict:
    """就医准备问题：确定性调用 guideline_retrieve，再把结果整理成回答。

    这类问题应走 general，但答案不能靠模型常识编；工具结果作为唯一依据，
    查不到就明说，不拿最相近的一条凑数。
    """
    query = _tool_query(state)
    outcome = call_tool("guideline_retrieve", {"query": query})
    hits = ((outcome.data or {}).get("hits") or []) if outcome.ok else []

    if hits:
        lines = ["就医前可以准备这些："]
        for hit in hits[:3]:
            content = str(hit.get("content") or hit.get("title") or "").strip()
            if content:
                lines.append(f"- {content}")
        lines.append("具体要求以就诊医院为准。")
        text = "\n".join(lines)[:300]
    else:
        text = "暂时没有查到具体的就诊准备条目，建议直接咨询就诊医院。"

    if not general_answer_is_safe(text):
        text = GENERAL_FALLBACK

    scope = current_trace()
    trace_id = scope.trace_id if scope else ""
    tool_data = (
        outcome.data
        if outcome.ok
        else {"error": outcome.error, "error_kind": outcome.error_kind}
    )
    return {
        "next_action": "GENERAL",
        "rendered_text": text,
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": [],
        "tool_results": {"guideline_retrieve": tool_data},
        "tool_call_count": state.tool_call_count + 1,
        "trace": [
            {
                "node": node,
                "source": "general",
                "guideline": True,
                "tool": "guideline_retrieve",
                "tool_ok": outcome.ok,
                "hits": len(hits),
                "trace_id": trace_id,
            },
            {
                "node": "tool",
                "called": "guideline_retrieve",
                "args_keys": ["query"],
                "ok": outcome.ok,
                "latency_ms": outcome.latency_ms,
                "trace_id": trace_id,
            },
        ],
    }


def answer_red_flags(state: ConsultationState, *, node: str = "general_route") -> dict:
    """红旗科普：调用 red_flag_lookup 列出警示，不做“你没有风险”的结论。

    这是 red_flag_lookup 在图上唯一的合法场景：用户主动问“什么情况要急诊”。
    风险分流仍由 rule_engine 更早完成，工具结果不写 risk_flags。
    """
    query = _tool_query(state)
    outcome = call_tool("red_flag_lookup", {"query": query})
    hits = ((outcome.data or {}).get("hits") or []) if outcome.ok else []

    if hits:
        lines = ["出现以下情况请立即就医或拨打急救电话："]
        for hit in hits[:3]:
            content = str(hit.get("content") or hit.get("title") or "").strip()
            if content:
                lines.append(f"- {content[:60]}")
        lines.append("以上是通用警示，不代表对你当前情况的判断；症状明显或加重时请及时就医。")
        text = "\n".join(lines)
    else:
        # 查不到不等于安全：这里只退回通用提示，不给任何安抚性结论。
        text = RED_FLAG_FALLBACK

    if not red_flag_answer_is_safe(text):
        text = RED_FLAG_FALLBACK

    scope = current_trace()
    trace_id = scope.trace_id if scope else ""
    tool_data = (
        outcome.data
        if outcome.ok
        else {"error": outcome.error, "error_kind": outcome.error_kind}
    )
    return {
        "next_action": "GENERAL",
        "rendered_text": text,
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": [],
        "tool_results": {"red_flag_lookup": tool_data},
        "tool_call_count": state.tool_call_count + 1,
        "trace": [
            {
                "node": node,
                "source": "general",
                # 路径标记：走的是红旗科普，不是风险命中；真实命中在 risk_flags。
                "red_flag_qa": True,
                "tool": "red_flag_lookup",
                "tool_ok": outcome.ok,
                "hits": len(hits),
                "trace_id": trace_id,
            },
            {
                "node": "tool",
                "called": "red_flag_lookup",
                "args_keys": ["query"],
                "ok": outcome.ok,
                "latency_ms": outcome.latency_ms,
                "trace_id": trace_id,
            },
        ],
    }


def _source_line(tool_name: str) -> str:
    if tool_name.startswith("openfda"):
        return "来源：openFDA。"
    if tool_name.startswith("pubmed"):
        return "来源：PubMed。"
    if tool_name.startswith("clinicaltrials"):
        return "来源：ClinicalTrials.gov。"
    if tool_name.startswith("medlineplus"):
        return "来源：MedlinePlus。"
    return ""


def _has_source_marker(text: str) -> bool:
    return any(
        marker in text
        for marker in ("openFDA", "PubMed", "ClinicalTrials", "MedlinePlus")
    )


def _external_reply(
    state: ConsultationState,
    *,
    node: str,
    tool_name: str,
    text: str,
    outcome,
    args_keys: list[str],
    trace_extra: dict,
    localize: bool = True,
) -> dict:
    """外部工具回答的统一外壳：中文摘要 + 文案 + 原始结果 + 两条 trace。"""
    original_text = text
    if localize:
        localized_text = localize_external(text)
        # 摘要也必须过安全闸门；失败就退回原文，不让摘要引入剂量或建议。
        if localized_text != original_text and not translated_external_is_safe(
            localized_text
        ):
            localized_text = original_text
        text = localized_text
    translated = text != original_text

    # 摘要可能压掉来源行；按工具补回，保证可核验。
    source_line = _source_line(tool_name)
    if source_line and not _has_source_marker(text):
        text = f"{text}\n{source_line}"

    scope = current_trace()
    trace_id = scope.trace_id if scope else ""
    tool_data = (
        outcome.data
        if outcome.ok
        else {"error": outcome.error, "error_kind": outcome.error_kind}
    )
    return {
        "next_action": "GENERAL",
        "rendered_text": text,
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": [],
        "tool_results": {tool_name: tool_data},
        "tool_call_count": state.tool_call_count + 1,
        "trace": [
            {
                "node": node,
                "source": "general",
                "tool": tool_name,
                "tool_ok": outcome.ok,
                "translated": translated,
                "trace_id": trace_id,
                **trace_extra,
            },
            {
                "node": "tool",
                "called": tool_name,
                "args_keys": args_keys,
                "ok": outcome.ok,
                "latency_ms": outcome.latency_ms,
                "trace_id": trace_id,
            },
        ],
    }


def _english_drug_name(message: str) -> str:
    """从本地药品别名里找英文通用名，openFDA 只认英文。"""
    lowered = message.lower()
    for label in load_labels():
        aliases = [str(alias) for alias in getattr(label, "aliases", ())]
        if not any(alias.lower() in lowered for alias in aliases):
            continue
        for alias in aliases:
            candidate = alias.lower().strip()
            if re.fullmatch(r"[a-z][a-z \-]*", candidate):
                return candidate
    for chinese, english in DRUG_NAME_ALIASES.items():
        if chinese in lowered:
            return english
    return ""


def answer_drug_label(state: ConsultationState, *, node: str = "general_route") -> dict | None:
    """openFDA 药品说明书：只展示用途、警示与禁忌，剔除剂量段落。"""
    if not is_drug_label_question(state.latest_user_message):
        return None
    tool_name = "openfda_get_drug_label"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    drug = _english_drug_name(state.latest_user_message)
    if not drug:
        return None

    outcome = call_tool(
        tool_name, {"search": f'openfda.generic_name:"{drug}"', "limit": 1}
    )
    parsed = parse_openfda_label((outcome.data or {}) if outcome.ok else {})

    if parsed["title"]:
        meta = parsed["meta"]
        sections = parsed["sections"]
        lines = [f"FDA 说明书：{parsed['title']}"]
        if meta.get("Manufacturer"):
            lines.append(f"厂商：{meta['Manufacturer']}")
        if meta.get("Product type"):
            lines.append(f"类型：{meta['Product type']}")
        if meta.get("Effective date"):
            lines.append(f"生效日期：{meta['Effective date']}")

        for key, label, limit in (
            ("Indications and usage", "用途", 90),
            ("Warnings", "警示", 170),
            ("Do not use", "不要使用", 90),
        ):
            snippet = safe_snippet(sections.get(key, ""), limit)
            if snippet:
                lines.append(f"{label}：{snippet}")

        set_id = (meta.get("Set ID") or "").split()
        suffix = f"（Set ID {set_id[0]}）" if set_id else ""
        lines.append(f"来源：openFDA{suffix}。仅展示说明书信息，不构成用药建议。")
        text = "\n".join(lines)
    else:
        text = f"暂时没有查到 {drug} 的 FDA 说明书；请以药品包装说明书或药师意见为准。"

    if not external_answer_is_safe(text):
        text = "外部药品说明书暂无法安全展示；请以药品包装说明书或药师意见为准。"

    return _external_reply(
        state,
        node=node,
        tool_name=tool_name,
        text=text,
        outcome=outcome,
        args_keys=["search", "limit"],
        trace_extra={"openfda_label": True},
    )


def _clip_plain(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _markdown_blocks(text: str) -> list[tuple[str, str]]:
    """按 `### 标题` 切块，返回 (标题, 整块正文)。"""
    parts = re.split(r"^###\s+", text, flags=re.MULTILINE)[1:]
    blocks = []
    for part in parts:
        lines = part.splitlines()
        if lines and lines[0].strip():
            blocks.append((lines[0].strip(), part))
    return blocks


def _markdown_field(block: str, name: str) -> str:
    match = re.search(rf"\*\*{name}:\*\*\s*(.+)", block)
    return match.group(1).strip() if match else ""


_DOSE_IN_TEXT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|ml|g|mcg|tablet|tablets|capsule|capsules|dose|doses)\b",
    re.IGNORECASE,
)


def _strip_dose(text: str) -> str:
    """召回描述里常有规格，对外文案不带剂量。"""
    cleaned = _DOSE_IN_TEXT.sub("", str(text))
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r",\s*,", ",", cleaned)
    return cleaned.strip(" ,;")


def _recall_entries(text: str) -> list[dict]:
    entries = []
    for block in re.split(r"^\s*---\s*$", text, flags=re.MULTILINE):
        head = re.search(r"\*\*Recall #(.+?)\*\*\s*—\s*Class (\w+)", block)
        if not head:
            continue

        def field(name: str) -> str:
            match = re.search(rf"^{name}:\s*(.+)$", block, flags=re.MULTILINE)
            return match.group(1).strip() if match else ""

        entries.append(
            {
                "number": head.group(1).strip(),
                "klass": head.group(2),
                "product": field("Product"),
                "reason": field("Reason"),
                "status": field("Status"),
                "date": field("Recall initiation date") or field("Report date"),
            }
        )
    return entries


def _format_date(value: str) -> str:
    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def answer_recall(state: ConsultationState, *, node: str = "general_route") -> dict | None:
    """openFDA 召回记录：只列召回号、原因、状态与日期。"""
    if not is_recall_question(state.latest_user_message):
        return None
    tool_name = "openfda_search_recalls"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    drug = _english_drug_name(state.latest_user_message)
    if not drug:
        return None

    outcome = call_tool(
        tool_name,
        {
            "category": "drug",
            "search": f'openfda.generic_name:"{drug}"',
            "limit": 3,
        },
    )
    payload = markdown_text((outcome.data or {}) if outcome.ok else "")
    entries = _recall_entries(payload)

    if entries:
        total = re.search(r"\*\*(\d+) total results\*\*", payload)
        lines = [
            f"FDA 召回记录（共 {total.group(1)} 条）："
            if total
            else "FDA 召回记录："
        ]
        for index, entry in enumerate(entries[:3], start=1):
            parts = [f"{entry['number']}（Class {entry['klass']}）"]
            product = _clip_plain(_strip_dose(entry["product"]), 60)
            reason = _clip_plain(_strip_dose(entry["reason"]), 70)
            if product:
                parts.append(product)
            if reason:
                parts.append(f"原因：{reason}")
            if entry["status"]:
                parts.append(f"状态：{entry['status']}")
            if entry["date"]:
                parts.append(f"发起：{_format_date(entry['date'])}")
            lines.append(f"{index}. " + "；".join(parts))
        lines.append(
            "来源：openFDA 执法报告。召回信息不代表该药当前仍在售或仍被召回，请以官方公告为准。"
        )
        text = "\n".join(lines)
    else:
        text = f"没有查到 {drug} 的 FDA 召回记录（来源：openFDA）。"

    if not external_answer_is_safe(text):
        text = "外部药品召回信息暂无法安全展示；请以官方公告为准。"

    return _external_reply(
        state,
        node=node,
        tool_name=tool_name,
        text=text,
        outcome=outcome,
        args_keys=["category", "search", "limit"],
        trace_extra={"openfda_recalls": True, "hits": len(entries)},
    )


def answer_shortage(state: ConsultationState, *, node: str = "general_route") -> dict | None:
    """FDA 药品短缺：只列状态、供应与厂商。"""
    if not is_shortage_question(state.latest_user_message):
        return None
    tool_name = "openfda_search_drug_shortages"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    drug = _english_drug_name(state.latest_user_message)
    if not drug:
        return None

    outcome = call_tool(tool_name, {"search": f'generic_name:"{drug}"', "limit": 3})
    payload = markdown_text((outcome.data or {}) if outcome.ok else "")
    entries = []
    for title, block in _markdown_blocks(payload):
        entries.append(
            {
                "title": title,
                "status": _markdown_field(block, "Status"),
                "availability": _markdown_field(block, "Availability"),
                "category": _markdown_field(block, "Therapeutic category"),
                "manufacturer": _markdown_field(block, "Manufacturer"),
            }
        )

    if entries:
        total = re.search(r"\*\*(\d+) total results\*\*", payload)
        lines = [
            f"FDA 药品短缺记录（共 {total.group(1)} 条）："
            if total
            else "FDA 药品短缺记录："
        ]
        for index, entry in enumerate(entries[:3], start=1):
            parts = [entry["title"]]
            if entry["status"]:
                parts.append(f"状态：{entry['status']}")
            if entry["availability"]:
                parts.append(f"供应：{entry['availability']}")
            if entry["category"]:
                parts.append(f"类别：{entry['category']}")
            if entry["manufacturer"]:
                parts.append(f"厂商：{_clip_plain(entry['manufacturer'], 40)}")
            lines.append(f"{index}. " + "；".join(parts))
        lines.append("来源：openFDA 药品短缺数据库。短缺状态会变化，请以官方公告为准。")
        text = "\n".join(lines)
    else:
        text = f"没有查到 {drug} 的 FDA 短缺记录（来源：openFDA）。"

    if not external_answer_is_safe(text):
        text = "外部药品短缺信息暂无法安全展示；请以官方公告为准。"

    return _external_reply(
        state,
        node=node,
        tool_name=tool_name,
        text=text,
        outcome=outcome,
        args_keys=["search", "limit"],
        trace_extra={"openfda_shortages": True, "hits": len(entries)},
    )


def answer_approval(state: ConsultationState, *, node: str = "general_route") -> dict | None:
    """Drugs@FDA 批准记录：只列申请号、申请方与首个产品状态。"""
    if not is_approval_question(state.latest_user_message):
        return None
    tool_name = "openfda_search_drug_approvals"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    drug = _english_drug_name(state.latest_user_message)
    if not drug:
        return None

    outcome = call_tool(
        tool_name, {"search": f'products.brand_name:"{drug}"', "limit": 2}
    )
    payload = markdown_text((outcome.data or {}) if outcome.ok else "")
    entries = []
    for title, block in _markdown_blocks(payload):
        head = re.search(
            r"\*\*Application:\*\*\s*([^|]+)\|\s*\*\*Sponsor:\*\*\s*(.+)", block
        )
        product = re.search(r"\*\*Products:\*\*\s*\n-\s*(.+)", block)
        entries.append(
            {
                "title": title,
                "application": head.group(1).strip() if head else "",
                "sponsor": _clip_plain(head.group(2).strip(), 40) if head else "",
                "product_status": (
                    product.group(1).split("|")[-1].strip() if product else ""
                ),
            }
        )

    if entries:
        total = re.search(r"\*\*(\d+) total results\*\*", payload)
        lines = [
            f"FDA 批准记录（共 {total.group(1)} 条）：" if total else "FDA 批准记录："
        ]
        for index, entry in enumerate(entries[:3], start=1):
            parts = [entry["title"]]
            if entry["application"]:
                parts.append(entry["application"])
            if entry["sponsor"]:
                parts.append(f"申请方：{entry['sponsor']}")
            if entry["product_status"]:
                parts.append(f"产品状态：{entry['product_status']}")
            lines.append(f"{index}. " + "；".join(parts))
        lines.append(
            "来源：openFDA Drugs@FDA。仅代表美国批准记录，不代表中国上市情况。"
        )
        text = "\n".join(lines)
    else:
        text = f"没有查到 {drug} 的 FDA 批准记录（来源：openFDA）。"

    if not external_answer_is_safe(text):
        text = "外部药品批准信息暂无法安全展示；请以官方公告为准。"

    return _external_reply(
        state,
        node=node,
        tool_name=tool_name,
        text=text,
        outcome=outcome,
        args_keys=["search", "limit"],
        trace_extra={"openfda_approvals": True, "hits": len(entries)},
    )


def answer_adverse_events(
    state: ConsultationState, *, node: str = "general_route"
) -> dict | None:
    """FAERS 自发报告：只展示总量与一条示例，并明确不能证明因果。"""
    if not is_adverse_event_question(state.latest_user_message):
        return None
    tool_name = "openfda_search_adverse_events"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    drug = _english_drug_name(state.latest_user_message)
    if not drug:
        return None

    outcome = call_tool(
        tool_name,
        {
            "category": "drug",
            "search": f'patient.drug.medicinalproduct:"{drug}"',
            "limit": 1,
            "fields": [
                "patient.reaction.reactionmeddrapt",
                "serious",
                "receiptdate",
                "patient.drug.medicinalproduct",
            ],
        },
    )
    payload = markdown_text((outcome.data or {}) if outcome.ok else "")
    total = re.search(r"\*\*(\d+) total results\*\*", payload)
    reactions = re.findall(r'"reactionmeddrapt":"([^"]+)"', payload)
    serious = re.search(r"\*\*Serious:\*\*\s*(\S+)", payload)
    date = re.search(r"\*\*Receiptdate:\*\*\s*(\d{8})", payload)

    if reactions:
        lines = [
            f"FAERS 不良反应报告（共 {total.group(1) if total else '?'} 条，展示 1 条示例）："
        ]
        detail = []
        if date:
            detail.append(f"报告日期：{_format_date(date.group(1))}")
        if serious:
            serious_text = "是" if serious.group(1) in {"1", "Yes", "true"} else serious.group(1)
            detail.append(f"严重：{serious_text}")
        detail.append("反应：" + "、".join(reactions[:4]))
        lines.append("；".join(detail))
        lines.append(
            "注意：自发报告不能证明因果，也不能代表发生率；仅作药物安全信号参考。"
        )
        lines.append("来源：openFDA FAERS。")
        text = "\n".join(lines)
    else:
        text = f"没有查到 {drug} 的 FAERS 报告（来源：openFDA）。"

    if not external_answer_is_safe(text):
        text = "外部不良反应信息暂无法安全展示；请咨询专业人员。"

    return _external_reply(
        state,
        node=node,
        tool_name=tool_name,
        text=text,
        outcome=outcome,
        args_keys=["category", "search", "limit", "fields"],
        trace_extra={"openfda_adverse_events": True, "hits": len(reactions)},
    )


def _health_topic_label(message: str) -> str:
    for chinese in CONDITION_ALIASES:
        if chinese in message:
            return chinese
    return _health_topic_query(message)


def _health_topic_query(message: str) -> str:
    for chinese, english in CONDITION_ALIASES.items():
        if chinese in message:
            return english
    text = re.sub(
        r"(什么是|是什么病|是什么|有什么症状|有哪些症状|怎么预防|如何预防|科普|的|？|?)",
        " ",
        message,
    )
    return " ".join(text.split()) or message.strip()


class _TopicTextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "form", "noscript"}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _fetch_medlineplus_text(url: str, limit: int = 6000) -> str:
    """只抓 MedlinePlus 页面正文；其他域名一律拒绝。"""
    host = urlparse(url).hostname or ""
    if host not in {"medlineplus.gov", "www.medlineplus.gov"}:
        return ""
    try:
        with httpx.Client(timeout=6.0, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "consult-lab-demo/1.0"})
            response.raise_for_status()
    except Exception:
        return ""

    # 重定向后仍必须落在 MedlinePlus 域名内。
    final_host = urlparse(str(response.url)).hostname or ""
    if final_host not in {"medlineplus.gov", "www.medlineplus.gov"}:
        return ""

    parser = _TopicTextExtractor()
    parser.feed(response.text)
    return " ".join(parser.parts)[:limit]


def _summarize_health_topic(topic: str, page_text: str) -> str:
    if os.getenv("USE_MODEL") != "1" or not page_text.strip():
        return ""
    try:
        result = chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是健康教育编辑。只根据给定内容，用 2-3 句简体中文"
                        "概括核心信息：定义、主要表现、预防。不要补充未出现的事实，"
                        "不要给诊断、治疗或用药建议。不超过 160 字。"
                    ),
                },
                {"role": "user", "content": f"主题：{topic}\n内容：{page_text}"},
            ],
            max_tokens=320,
            temperature=0.0,
            timeout=20.0,
            prompt_name="health.summary",
            prompt_version="v1",
        )
    except Exception:
        return ""

    summary = (result.content or "").strip()
    if not summary:
        return ""
    # 摘要本身也要过安全闸门；加来源标记复用同一套规则。
    if not translated_external_is_safe(summary + " 来源：MedlinePlus"):
        return ""
    return summary


def answer_health_topic(
    state: ConsultationState, *, node: str = "general_route"
) -> dict | None:
    """MedlinePlus 健康主题：抓页面正文后做中文摘要，失败退回搜索片段。"""
    if not is_health_topic_question(state.latest_user_message):
        return None
    tool_name = "medlineplus_search"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    query = _health_topic_query(state.latest_user_message)
    outcome = call_tool(tool_name, {"query": query, "limit": 3})
    payload = medlineplus_payload((outcome.data or {}) if outcome.ok else {})
    results = payload.get("results") or []
    summary = ""

    if results:
        titles = [
            re.sub(r"<[^>]+>", "", str(item.get("title") or "")).strip()
            for item in results[:3]
        ]
        summary = ""
        if os.getenv("USE_MODEL") == "1" and results[0].get("url"):
            page_text = _fetch_medlineplus_text(str(results[0]["url"]))
            summary = _summarize_health_topic(titles[0], page_text)

        if summary:
            label = _health_topic_label(state.latest_user_message)
            lines = [
                f"MedlinePlus 健康主题：{_clip_plain(label, 60)}",
                f"摘要：{summary}",
            ]
            related = [title for title in titles[1:] if title]
            if related:
                lines.append(
                    "相关主题：" + "；".join(_clip_plain(title, 50) for title in related)
                )
        else:
            lines = [f"MedlinePlus 健康主题（共 {payload.get('total', '?')} 条）："]
            for index, item in enumerate(results[:3], start=1):
                snippet = re.sub(
                    r"<[^>]+>", "", str(item.get("snippet") or "")
                ).strip()
                line = f"{index}. {_clip_plain(titles[index - 1], 60)}"
                if index == 1 and snippet:
                    line += f" — {_clip_plain(snippet, 220)}"
                lines.append(line)
        lines.append(
            "来源：MedlinePlus（美国国家医学图书馆）。仅作健康教育，不构成诊断或治疗建议。"
        )
        text = "\n".join(lines)
    else:
        text = (
            f"没有查到相关健康主题（MedlinePlus）：{query}。"
            "建议换英文关键词再试。"
        )

    if not translated_external_is_safe(text):
        text = "外部健康主题暂无法安全展示；建议直接访问 MedlinePlus 查询。"

    return _external_reply(
        state,
        node=node,
        tool_name=tool_name,
        text=text,
        outcome=outcome,
        args_keys=["query", "limit"],
        trace_extra={"medlineplus": True, "hits": len(results)},
    )


def _trial_query(message: str) -> str:
    text = re.sub(
        r"(帮我|请|查一下|搜一下|找一下|检索|有没有|有哪些|有什么|相关|关于|"
        r"临床试验|临床研究|研究招募|受试者招募|入组|clinical\s*trials?|上|的)",
        " ",
        message,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[？?。！!，,]", " ", text)
    return " ".join(text.split()) or message.strip()


def _trial_entries(text: str) -> list[dict]:
    lines = text.splitlines()
    entries = []
    for index, line in enumerate(lines):
        match = re.match(
            r"^- \*\*(NCT\d+)\*\*: (.+?) \[([A-Z_]+)\]", line
        )
        if not match:
            continue
        detail = (
            lines[index + 1].strip()
            if index + 1 < len(lines) and lines[index + 1].startswith("  ")
            else ""
        )
        entries.append(
            {
                "nct": match.group(1),
                "title": match.group(2),
                "status": match.group(3),
                "phase": detail.split("|")[0].strip() if detail else "",
                "enrollment": re.search(r"\bN=(\d+)\b", detail),
            }
        )
    return entries


def _trial_record_text(text: str) -> str:
    head = re.search(r"^# Study (NCT\d+): (.+)$", text, flags=re.MULTILINE)
    if not head:
        return ""
    status = re.search(r"\*\*Status:\*\*\s*([^|\n]+)", text)
    conditions = re.search(r"\*\*Conditions:\*\*\s*(.+)", text)
    sponsor = re.search(r"\*\*Sponsor:\*\*\s*(.+)", text)
    dates = re.search(r"\*\*Dates:\*\*\s*(.+)", text)

    lines = [f"{head.group(1)}：{_clip_plain(head.group(2), 80)}"]
    if status:
        lines.append(f"状态：{status.group(1).strip()}")
    if conditions:
        lines.append(f"条件：{_clip_plain(conditions.group(1), 60)}")
    if sponsor:
        lines.append(f"发起方：{_clip_plain(sponsor.group(1), 50)}")
    if dates:
        lines.append(f"时间：{_clip_plain(dates.group(1), 140)}")
    lines.append(
        "来源：ClinicalTrials.gov。试验信息不代表适合你，是否入组需由研究方评估。"
    )
    return "\n".join(lines)


def answer_clinical_trials(
    state: ConsultationState, *, node: str = "general_route"
) -> dict | None:
    """ClinicalTrials.gov：NCT 编号给详情，否则按主题检索。"""
    if not is_clinical_trial_question(state.latest_user_message):
        return None
    tool_name = "clinicaltrials_search_studies"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    nct_match = re.search(
        r"\bNCT\d{8}\b", state.latest_user_message, flags=re.IGNORECASE
    )
    record_tool = "clinicaltrials_get_study_record"
    if nct_match and DEFAULT_CATALOG.get(record_tool) is not None:
        outcome = call_tool(
            record_tool,
            {
                "nctId": nct_match.group(0).upper(),
                "locationLimit": 1,
                "outcomeLimit": 0,
                "referenceLimit": 0,
            },
        )
        payload = markdown_text((outcome.data or {}) if outcome.ok else "")
        text = _trial_record_text(payload) or "没有查到该 NCT 编号的试验详情。"
        if not external_answer_is_safe(text):
            text = "外部试验详情暂无法安全展示；建议直接访问 ClinicalTrials.gov 查询。"
        return _external_reply(
            state,
            node=node,
            tool_name=record_tool,
            text=text,
            outcome=outcome,
            args_keys=["nctId", "locationLimit", "outcomeLimit", "referenceLimit"],
            trace_extra={"clinicaltrials_record": True},
        )

    query = _trial_query(state.latest_user_message)
    outcome = call_tool(tool_name, {"query": query, "pageSize": 3})
    payload = markdown_text((outcome.data or {}) if outcome.ok else "")
    entries = _trial_entries(payload)

    if entries:
        found = re.search(r"Found (\d+) studies \((\d+) total matching\)", payload)
        lines = [
            f"ClinicalTrials.gov 检索（共 {found.group(2) if found else '?'} 条，"
            f"返回 {found.group(1) if found else len(entries)} 条）："
        ]
        for index, entry in enumerate(entries[:3], start=1):
            bits = [entry["status"]]
            if entry["phase"]:
                bits.append(entry["phase"])
            if entry["enrollment"]:
                bits.append(f"N={entry['enrollment'].group(1)}")
            line = (
                f"{index}. {entry['nct']} — {_clip_plain(entry['title'], 70)}"
                f"（{'；'.join(bits)}）"
            )
            lines.append(line)
        lines.append(
            "来源：ClinicalTrials.gov。试验信息不代表适合你，是否入组需由研究方评估。"
        )
        text = "\n".join(lines)
    else:
        text = (
            f"没有查到相关临床试验（ClinicalTrials.gov）：{query}。"
            "建议换英文关键词再试。"
        )

    if not external_answer_is_safe(text):
        text = "外部临床试验信息暂无法安全展示；建议直接访问 ClinicalTrials.gov 查询。"

    return _external_reply(
        state,
        node=node,
        tool_name=tool_name,
        text=text,
        outcome=outcome,
        args_keys=["query", "pageSize"],
        trace_extra={"clinicaltrials": True, "hits": len(entries)},
    )


def _research_query(message: str) -> str:
    text = re.sub(
        r"(帮我|请|查一下|搜一下|找一下|检索|pubmed|关于|有没有|有哪些|有什么|"
        r"的研究|研究|文献|论文|证据|指南|临床试验|上)",
        " ",
        message,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[？?。！!，,]", " ", text)
    return " ".join(text.split()) or message.strip()


def _pubmed_entries(text: str) -> list[dict]:
    entries = []
    for block in re.split(r"^####\s+", text, flags=re.MULTILINE)[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        body = "\n".join(lines[1:])

        def field(name: str) -> str:
            match = re.search(rf"\*\*{name}:\*\*\s*(.+)", body)
            return match.group(1).strip() if match else ""

        entries.append(
            {
                "title": lines[0].strip(),
                "pmid": field("PMID"),
                "source": field("Source"),
                "published": field("Published"),
                "doi": field("DOI"),
            }
        )
    return entries


def _pubmed_article_text(text: str) -> str:
    title = re.search(r"^###\s+(.+)$", text, flags=re.MULTILINE)
    if not title:
        return ""
    journal = re.search(r"\*\*Journal:\*\*\s*(.+)", text)
    doi = re.search(r"\*\*DOI:\*\*\s*(\S+)", text)
    abstract = re.search(
        r"^#### Abstract\s*\n(.+?)(?=^####|^## |\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )

    lines = [f"PubMed 文献：{_clip_plain(title.group(1), 80)}"]
    if journal:
        lines.append(f"期刊：{_clip_plain(journal.group(1), 80)}")
    pmid = re.search(r"\*\*PMID:\*\*\s*(\d+)", text)
    if pmid:
        lines.append(f"PMID：{pmid.group(1)}")
    if doi:
        lines.append(f"DOI：{doi.group(1)}")
    if abstract:
        lines.append("摘要：" + _clip_plain(abstract.group(1), 300))
    lines.append("来源：PubMed。仅作文献信息展示，不构成诊断或治疗建议。")
    return "\n".join(lines)


def answer_research(state: ConsultationState, *, node: str = "general_route") -> dict | None:
    """PubMed：带 PMID 取摘要，否则按主题检索。"""
    if not is_research_question(state.latest_user_message):
        return None
    tool_name = "pubmed_search_articles"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    pmid_match = re.search(
        r"\bPMID\s*[:：]?\s*(\d{6,9})\b",
        state.latest_user_message,
        flags=re.IGNORECASE,
    )
    fetch_tool = "pubmed_fetch_articles"
    if pmid_match and DEFAULT_CATALOG.get(fetch_tool) is not None:
        outcome = call_tool(
            fetch_tool,
            {"pmids": [pmid_match.group(1)], "includeMesh": False},
        )
        payload = markdown_text((outcome.data or {}) if outcome.ok else "")
        text = _pubmed_article_text(payload) or "没有查到该 PMID 的文献（来源：PubMed）。"
        if not external_answer_is_safe(text):
            text = "外部文献摘要暂无法安全展示；建议直接访问 PubMed 查询。"
        return _external_reply(
            state,
            node=node,
            tool_name=fetch_tool,
            text=text,
            outcome=outcome,
            args_keys=["pmids", "includeMesh"],
            trace_extra={"pubmed": True, "pmid": True},
        )

    query = _research_query(state.latest_user_message)
    outcome = call_tool(
        tool_name,
        {"query": query, "maxResults": 3, "summaryCount": 3},
    )
    payload = markdown_text((outcome.data or {}) if outcome.ok else "")
    entries = _pubmed_entries(payload)

    if not outcome.ok:
        # 工具失败不等于“没有文献”：必须明确说明依据暂时不可用。
        text = (
            "外部文献检索暂时不可用（暂无依据）。"
            "可以稍后重试，或直接访问 PubMed 查询。"
        )
    elif entries:
        total, returned = pubmed_counts(payload)
        lines = [
            f"PubMed 检索（共 {total} 条，"
            f"返回 {returned if returned != '?' else len(entries)} 条）："
        ]
        for index, entry in enumerate(entries[:3], start=1):
            meta = ", ".join(
                part for part in (entry["source"], entry["published"]) if part
            )
            ids = "；".join(
                part
                for part in (
                    f"PMID: {entry['pmid']}" if entry["pmid"] else "",
                    f"DOI: {entry['doi']}" if entry["doi"] else "",
                )
                if part
            )
            line = f"{index}. {entry['title']}"
            if meta:
                line += f" — {meta}"
            if ids:
                line += f"；{ids}"
            lines.append(line)
        lines.append("来源：PubMed。以上为文献题录，不构成诊断或治疗建议。")
        text = "\n".join(lines)
    else:
        text = (
            f"暂时没有检索到相关文献（PubMed）：{query}。"
            "建议换个英文关键词，或直接访问 PubMed 查询。"
        )

    if not external_answer_is_safe(text):
        text = "外部文献检索结果暂无法安全展示；建议直接访问 PubMed 查询。"

    return _external_reply(
        state,
        node=node,
        tool_name=tool_name,
        text=text,
        outcome=outcome,
        args_keys=["query", "maxResults", "summaryCount"],
        trace_extra={"pubmed": True, "hits": len(entries)},
    )


def route_general(state: ConsultationState) -> dict:
    """普通 → 直接回答并结束本轮；医疗 → 回到 decision。"""
    # 红旗科普优先：它比就医准备更安全敏感，先判它。
    if is_red_flag_question(state.latest_user_message):
        return answer_red_flags(state, node="general_route")

    # 外部工具类问题：只有工具已注册时才接管，否则原逻辑不变。
    drug_reply = answer_drug_label(state, node="general_route")
    if drug_reply is not None:
        return drug_reply
    recall_reply = answer_recall(state, node="general_route")
    if recall_reply is not None:
        return recall_reply
    shortage_reply = answer_shortage(state, node="general_route")
    if shortage_reply is not None:
        return shortage_reply
    approval_reply = answer_approval(state, node="general_route")
    if approval_reply is not None:
        return approval_reply
    adverse_reply = answer_adverse_events(state, node="general_route")
    if adverse_reply is not None:
        return adverse_reply
    trial_reply = answer_clinical_trials(state, node="general_route")
    if trial_reply is not None:
        return trial_reply
    research_reply = answer_research(state, node="general_route")
    if research_reply is not None:
        return research_reply
    topic_reply = answer_health_topic(state, node="general_route")
    if topic_reply is not None:
        return topic_reply

    # 就医准备类问题先走确定性工具，不依赖模型是否把它判成普通问题。
    if is_guideline_question(state.latest_user_message):
        return answer_guideline(state, node="general_route")

    # 已经进入医疗技能、仍有缺失字段时，短回答留在医疗链路，
    # 避免“无病史”这类回答被单路由当成普通聊天。
    if state.skill and state.missing_fields and looks_like_followup_answer(
        state.latest_user_message
    ):
        return {"next_action": "MEDICAL"}

    if os.getenv("USE_MODEL") != "1":
        return {"next_action": "MEDICAL"}

    try:
        routed, usage = route_reply(state.latest_user_message)
    except LLMError:
        return {"next_action": "MEDICAL"}

    if routed.intent != "general":
        return {"next_action": "MEDICAL"}

    if general_answer_is_safe(routed.answer):
        text = routed.answer.strip()
        source = "router"
    else:
        text = GENERAL_FALLBACK
        source = "fallback"

    # 只在会话第一轮做一次邀请；后续 general 回复不再重复贴邀请和快捷选项。
    opening = state.turn_id == 1 and not state.collected_facts
    if opening:
        text = f"{text}\n\n{OPENING_INVITE}"

    scope = current_trace()
    return {
        "next_action": "GENERAL",
        "conversation": [{"role": "agent", "text": text}],
        "suggested_replies": (
            list(REPLY_OPTIONS.get("主要症状", [])) if opening else []
        ),
        "trace": [
            {
                "node": "general_route",
                "source": "general",
                "general_answer_source": source,
                "router_intent": "general",
                "opening": opening,
                "answer_chars": len(text),
                "model_usage": _usage_dict(usage),
                "trace_id": scope.trace_id if scope else "",
            }
        ],
    }
