"""Phase 4：三个知识工具，共用同一条双路检索。

三个工具的区别只在于检索哪个 category，以及"查不到"意味着什么。
"""

from typing import Literal

from pydantic import BaseModel, Field

from backend.app.knowledge.retrieval import retrieve
from backend.app.knowledge.schema import Source, load_sources
from backend.app.tools.contract import ToolSpec


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
        },
    }


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
    ),
    "red_flag_lookup": ToolSpec(
        name="red_flag_lookup",
        description="检索需要立即就医的危险信号。查无结果只代表不确定，不代表安全。",
        params_model=RedFlagLookupParams,
        handler=red_flag_lookup,
        timeout_s=5.0,
        empty_result_mode="fail-close",
    ),
    "guideline_retrieve": ToolSpec(
        name="guideline_retrieve",
        description="检索通用就医指引与就诊准备事项。",
        params_model=GuidelineRetrieveParams,
        handler=guideline_retrieve,
        timeout_s=5.0,
        empty_result_mode="fail-open",
    ),
}