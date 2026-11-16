"""Phase 4：三个知识工具，共用同一条双路检索。

三个工具的区别只在于检索哪个 category，以及"查不到"意味着什么。
"""

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.knowledge.retrieval import retrieve
from backend.app.knowledge.schema import Source, load_sources
from backend.app.tools.contract import ToolSpec, clip_summary

# 工具名只在这里写一次，别的模块引用常量，避免字符串写错了才发现
DEPARTMENT_ROUTE = "department_route"
RED_FLAG_LOOKUP = "red_flag_lookup"
GUIDELINE_RETRIEVE = "guideline_retrieve"


class DepartmentRouteParams(BaseModel):
    query: str = Field(min_length=1, description="患者的症状或问题原话片段")
    risk_level: Literal["low", "medium", "high"] | None = Field(
        default=None, description="已知的风险等级，用于收窄检索范围"
    )
    top_k: int = Field(default=3, ge=1, le=10)


class RedFlagLookupParams(BaseModel):
    query: str = Field(min_length=1, description="患者的症状或问题原话片段")
    top_k: int = Field(default=3, ge=1, le=10)


class GuidelineRetrieveParams(BaseModel):
    query: str = Field(min_length=1, description="患者的症状或问题原话片段")
    top_k: int = Field(default=3, ge=1, le=10)


def _hit_payload(hit, sources: dict[str, Source]) -> dict:
    """把检索结果摊平成可序列化的字典，出处与核实状态一起带出去。"""
    source = sources.get(hit.source_id)
    return {
        "id": hit.id,
        "title": hit.title,
        "content": hit.content,
        "department": hit.department,
        "scope_text": hit.scope_text,
        "version": hit.version,
        "score": round(hit.score, 4),
        "matched_by": hit.matched_by,
        "source": {
            "id": hit.source_id,
            "title": source.title if source else "",
            "verified": source.verified if source else False,
            "review_status": source.review_status if source else "pending",
            "url": source.url if source else "",
        },
    }


def _source_note(hit: dict) -> str:
    source = hit.get("source") or {}
    title = source.get("title") or source.get("id") or "未注明出处"
    return f"{title}（{'已核实' if source.get('verified') else '未核实'}）"


def summarize_department(data: dict) -> str:
    """科室工具的信号是方向和出处；正文留给渲染层，不进 prompt。"""
    department = data.get("department")
    hits = data.get("hits") or []
    if department is None and not hits:
        return "科室方向：未命中（no_match），不要臆造科室。"

    top = hits[0] if hits else {}
    detail = (
        f"首要依据：{top.get('title') or top.get('id')}；"
        f"出处：{_source_note(top)}；命中 {len(hits)} 条。"
        if hits
        else "无命中条目，只说明已知方向。"
    )
    return clip_summary(f"科室方向：{department or top.get('department')}；{detail}")


def summarize_red_flag(data: dict) -> str:
    """红旗工具的信号是“不确定也算一种结果”，摘要必须保留这层含义。"""
    hits = data.get("hits") or []
    if not hits:
        return "红旗检索：未命中；状态不确定，不等于安全。"

    titles = "；".join(str(hit.get("title") or hit.get("id")) for hit in hits[:3])
    return clip_summary(
        f"红旗检索：命中 {len(hits)} 条（{titles}）；仅作提示，不改变风险等级。"
    )


def summarize_guideline(data: dict) -> str:
    """就医指引的信号是具体条目要点，最多保留三条，每条单独截断。"""
    hits = data.get("hits") or []
    if not hits:
        return "就医指引：未命中（no_match），不要拿相近条目凑数。"

    lines = ["就医指引："]
    for hit in hits[:3]:
        content = str(hit.get("content") or hit.get("title") or "").strip()
        if content:
            lines.append(f"- {clip_summary(content, 100)}")
    return clip_summary("\n".join(lines))


def _search(query: str, category: str, top_k: int, risk_level: str | None = None) -> list[dict]:
    sources = load_sources()
    hits = retrieve(query, top_k=top_k, category=category, risk_level=risk_level)
    return [_hit_payload(hit, sources) for hit in hits]


def department_route(params: DepartmentRouteParams) -> dict:
    """给出科室方向。查不到就明说，不拿最相近的一条凑数。"""
    hits = _search(params.query, "department_route", params.top_k, params.risk_level)
    if not hits:
        return {"department": None, "reason": "no_match", "hits": []}
    return {"department": hits[0]["department"], "reason": None, "hits": hits}


def red_flag_lookup(params: RedFlagLookupParams) -> dict:
    """检索危险信号。

    注意：hits 为空时 uncertain=True。查不到不等于安全，上层必须走保守分支，
    这也是这个工具声明 fail-close 的原因。
    """
    hits = _search(params.query, "red_flag", params.top_k)
    return {
        "flag_ids": [hit["id"] for hit in hits],
        "uncertain": not hits,
        "hits": hits,
    }


def guideline_retrieve(params: GuidelineRetrieveParams) -> dict:
    """检索通用就医指引。这里没有红旗语义，查不到就是没有可说的。"""
    hits = _search(params.query, "visit_prep", params.top_k)
    return {"no_match": not hits, "hits": hits}


KNOWLEDGE_TOOL_SPECS = {
    "department_route": ToolSpec(
        name="department_route",
        description="根据症状描述给出带出处的科室方向。查无结果时 department 为 None，不臆造。",
        params_model=DepartmentRouteParams,
        handler=department_route,
        timeout_s=8.0,
        empty_result_mode="fail-open",
        risk_class="read_only",
        scope="internal",
        summarize=summarize_department,
    ),
    "red_flag_lookup": ToolSpec(
        name="red_flag_lookup",
        description="检索需要立即就医的危险信号。查无结果只代表不确定，不代表安全。",
        params_model=RedFlagLookupParams,
        handler=red_flag_lookup,
        timeout_s=5.0,
        empty_result_mode="fail-close",
        risk_class="read_only",
        scope="internal",
        summarize=summarize_red_flag,
    ),
    "guideline_retrieve": ToolSpec(
        name="guideline_retrieve",
        description="检索通用就医指引与就诊准备事项。",
        params_model=GuidelineRetrieveParams,
        handler=guideline_retrieve,
        timeout_s=5.0,
        empty_result_mode="fail-open",
        risk_class="read_only",
        scope="internal",
        summarize=summarize_guideline,
    ),
}