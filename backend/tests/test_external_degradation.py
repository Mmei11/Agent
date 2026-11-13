"""Phase C 第 7 课：外部工具失败时的主流程降级。

外部只读工具超时/不可用 → status=degraded，“暂无依据”；
内部工具失败仍然保持 error，不掩盖真实故障。
"""

from pydantic import BaseModel

from backend.app.agent import graph as graph_module
from backend.app.agent.graph import output, tool
from backend.app.agent.state import ConsultationState
from backend.app.tools.catalog import ToolCatalog
from backend.app.tools.contract import ToolResult, ToolSpec


class _Params(BaseModel):
    query: str = ""


def test_external_timeout_degrades_to_no_basis():
    state = ConsultationState(
        latest_user_message="头痛两天",
        collected_facts={"主要症状": ["头痛"]},
        missing_fields=[],
        tool_results={
            "external_faq": {
                "error": "执行超过 8s",
                "error_kind": "timeout",
                "scope": "external",
            }
        },
    )

    result = output(state)["final_result"]

    assert result["status"] == "degraded"
    assert "外部知识依据" in result["next_step"]
    assert result["tools_used"] == ["external_faq"]


def test_internal_tool_failure_stays_error():
    state = ConsultationState(
        latest_user_message="头痛两天",
        tool_results={
            "department_route": {
                "error": "执行超过 8s",
                "error_kind": "timeout",
                "scope": "internal",
            }
        },
    )

    result = output(state)["final_result"]

    assert result["status"] == "error"


def test_tool_error_records_scope(monkeypatch):
    spec = ToolSpec(
        name="external_faq",
        description="外部示例工具",
        params_model=_Params,
        handler=lambda params: {},
        risk_class="read_only",
        scope="external",
    )
    monkeypatch.setattr(
        graph_module, "DEFAULT_CATALOG", ToolCatalog([spec])
    )
    monkeypatch.setattr(
        graph_module,
        "call_tool",
        lambda name, args: ToolResult(
            ok=False, name=name, error="执行超过 8s", error_kind="timeout"
        ),
    )
    state = ConsultationState(
        next_tool="external_faq", next_tool_args={"query": "头痛"}
    )

    result = tool(state)

    assert result["tool_results"]["external_faq"]["scope"] == "external"
    assert result["tool_results"]["external_faq"]["error_kind"] == "timeout"
