"""Phase 4：工具契约与三个知识工具。"""

import time

import pytest
from pydantic import BaseModel, Field

from backend.app.knowledge.retrieval import Hit
from backend.app.tools import knowledge_tools as kt
from backend.app.tools.contract import ToolSpec, tool_schema
from backend.app.tools.registry import call_tool


class _Params(BaseModel):
    value: int = Field(ge=0)


def _spec(handler, timeout_s: float = 1.0) -> ToolSpec:
    return ToolSpec(
        name="probe",
        description="测试用",
        params_model=_Params,
        handler=handler,
        timeout_s=timeout_s,
    )


def test_invalid_params_are_reported_not_raised():
    result = _spec(lambda params: {}).call({"value": -1})
    assert result.ok is False
    assert result.error_kind == "invalid_params"


def test_handler_exception_becomes_unavailable():
    def boom(params):
        raise RuntimeError("库挂了")

    result = _spec(boom).call({"value": 1})
    assert result.ok is False
    assert result.error_kind == "unavailable"
    assert "RuntimeError" in result.error


def test_slow_handler_times_out():
    def slow(params):
        time.sleep(0.3)
        return {"ok": True}

    result = _spec(slow, timeout_s=0.05).call({"value": 1})
    assert result.ok is False
    assert result.error_kind == "timeout"


def test_success_carries_data_and_latency():
    result = _spec(lambda params: {"echo": params.value}).call({"value": 7})
    assert result.ok is True
    assert result.data == {"echo": 7}
    assert result.latency_ms >= 0


def test_schema_is_declarable():
    schema = tool_schema(_spec(lambda params: {}))
    assert schema["name"] == "probe"
    assert schema["input_schema"]["type"] == "object"
    assert schema["timeout_s"] == 1.0
    assert schema["idempotent"] is True
    assert schema["empty_result_mode"] == "fail-open"


def test_unknown_tool_returns_structured_failure():
    result = call_tool("does_not_exist", {})
    assert result.ok is False
    assert result.error_kind == "invalid_params"


# ---------- 三个知识工具 ----------


def _hit(id: str = "rf-x", department: str | None = None, score: float = 0.8) -> Hit:
    return Hit(
        id=id,
        title="标题",
        content="内容",
        source_id="src-project-derived",
        department=department,
        scope_text="适用范围",
        version="v1",
        similarity=0.7,
        keyword_score=0.5,
        score=score,
        matched_by="both",
    )


def test_department_route_returns_top_department(monkeypatch):
    monkeypatch.setattr(kt, "retrieve", lambda *a, **k: [_hit("route-x", "神经内科")])
    result = kt.department_route(kt.DepartmentRouteParams(query="头痛"))
    assert result["department"] == "神经内科"
    assert result["reason"] is None
    assert result["hits"][0]["source"]["verified"] is False


def test_department_route_refuses_to_guess(monkeypatch):
    monkeypatch.setattr(kt, "retrieve", lambda *a, **k: [])
    result = kt.department_route(kt.DepartmentRouteParams(query="说不上来"))
    assert result["department"] is None
    assert result["reason"] == "no_match"


def test_red_flag_empty_is_uncertain_not_safe(monkeypatch):
    monkeypatch.setattr(kt, "retrieve", lambda *a, **k: [])
    result = kt.red_flag_lookup(kt.RedFlagLookupParams(query="有点不舒服"))
    assert result["uncertain"] is True
    assert result["flag_ids"] == []


def test_red_flag_hits_are_listed(monkeypatch):
    monkeypatch.setattr(kt, "retrieve", lambda *a, **k: [_hit("rf-dyspnea")])
    result = kt.red_flag_lookup(kt.RedFlagLookupParams(query="喘不上气"))
    assert result["uncertain"] is False
    assert result["flag_ids"] == ["rf-dyspnea"]


def test_guideline_reports_no_match(monkeypatch):
    monkeypatch.setattr(kt, "retrieve", lambda *a, **k: [])
    assert kt.guideline_retrieve(kt.GuidelineRetrieveParams(query="准备什么"))["no_match"] is True


def test_red_flag_spec_declares_fail_close():
    assert kt.KNOWLEDGE_TOOL_SPECS["red_flag_lookup"].empty_result_mode == "fail-close"
    assert kt.KNOWLEDGE_TOOL_SPECS["department_route"].empty_result_mode == "fail-open"