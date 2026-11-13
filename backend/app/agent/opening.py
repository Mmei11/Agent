"""图内预路由：任何未出结果的轮次，先判断普通/医疗。

普通问题直接回复（没有收集到信息时附带邀请描述），医疗问题回到 decision
继续字段收集。这样问候、倾诉、流程问题不会在中间轮次被当成症状追问。
"""

import os
import re

from backend.app.agent.followup import REPLY_OPTIONS
from backend.app.agent.general import (
    GENERAL_FALLBACK,
    RED_FLAG_FALLBACK,
    external_answer_is_safe,
    general_answer_is_safe,
    is_drug_label_question,
    is_guideline_question,
    is_red_flag_question,
    is_research_question,
    red_flag_answer_is_safe,
)
from backend.app.agent.router import route as route_reply
from backend.app.agent.state import ConsultationState
from backend.app.drugs.lookup import load_labels
from backend.app.llm.client import LLMError, LLMResult
from backend.app.observability import current_trace
from backend.app.tools.catalog import DEFAULT_CATALOG
from backend.app.tools.external_summaries import (
    markdown_text,
    parse_openfda_label,
    pubmed_counts,
    safe_snippet,
)
from backend.app.tools.registry import call_tool as _live_call_tool
from backend.app.tools.registry import stub_call_tool

# 没有向量库时用确定性替身跑通流程；与 graph.py 的开关保持一致。
call_tool = (
    stub_call_tool
    if os.getenv("KNOWLEDGE_BACKEND", "pgvector") == "stub"
    else _live_call_tool
)

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


def _external_reply(
    state: ConsultationState,
    *,
    node: str,
    tool_name: str,
    text: str,
    outcome,
    args_keys: list[str],
    trace_extra: dict,
) -> dict:
    """外部工具回答的统一外壳：文案 + 原始结果 + 两条 trace。"""
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


def answer_research(state: ConsultationState, *, node: str = "general_route") -> dict | None:
    """PubMed 文献检索：只返回题录与 PMID/DOI，不给临床结论。"""
    if not is_research_question(state.latest_user_message):
        return None
    tool_name = "pubmed_search_articles"
    if DEFAULT_CATALOG.get(tool_name) is None:
        return None

    query = _research_query(state.latest_user_message)
    outcome = call_tool(
        tool_name,
        {"query": query, "maxResults": 3, "summaryCount": 3},
    )
    payload = markdown_text((outcome.data or {}) if outcome.ok else "")
    entries = _pubmed_entries(payload)

    if entries:
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
        lines.append("以上为文献题录，不构成诊断或治疗建议。")
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
    research_reply = answer_research(state, node="general_route")
    if research_reply is not None:
        return research_reply

    # 就医准备类问题先走确定性工具，不依赖模型是否把它判成普通问题。
    if is_guideline_question(state.latest_user_message):
        return answer_guideline(state, node="general_route")

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
