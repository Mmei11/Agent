"""Phase 4：MCP server，把契约里的工具暴露给外部 Agent。

工具声明与图内调用共用同一份 ToolSpec：参数 Schema 由 Pydantic 导出，
执行走同一个 call_tool，所以 MCP 客户端拿到的校验、超时与失败语义,
和内嵌调用完全一致。

启动：
    uv run python -m backend.app.tools.mcp_server
"""

import asyncio
import json
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from backend.app.tools.registry import call_tool

server = MCPServer(
    name="consultation-knowledge",
    title="Simulated Consultation Knowledge",
    description="虚构问诊演示项目的受限知识工具。不提供确诊、处方或用药建议。",
)


def _content(result) -> str:
    """把 ToolResult 转成 MCP 的文本内容。

    失败也返回正常内容而不是抛异常：客户端能看到结构化的 error_kind，
    而不是一个断掉的连接。
    """
    if not result.ok:
        payload = {
            "ok": False,
            "error": result.error,
            "error_kind": result.error_kind,
        }
    else:
        payload = {"ok": True, "data": result.data, "latency_ms": result.latency_ms}
    return json.dumps(payload, ensure_ascii=False)


@server.tool(
    name="department_route",
    description="根据症状描述给出带出处的科室方向。查无结果时 department 为 None，不臆造。",
)
def department_route_tool(
    query: Annotated[str, Field(min_length=1, description="患者的症状或问题原话片段")],
    risk_level: Annotated[
        Literal["low", "medium", "high"] | None,
        Field(description="已知的风险等级，用于收窄检索范围"),
    ] = None,
    top_k: Annotated[int, Field(ge=1, le=10)] = 3,
) -> str:
    return _content(
        call_tool(
            "department_route",
            {"query": query, "risk_level": risk_level, "top_k": top_k},
        )
    )


@server.tool(
    name="red_flag_lookup",
    description="检索需要立即就医的危险信号。查无结果只代表不确定，不代表安全。",
)
def red_flag_lookup_tool(
    query: Annotated[str, Field(min_length=1, description="患者的症状或问题原话片段")],
    top_k: Annotated[int, Field(ge=1, le=10)] = 3,
) -> str:
    return _content(call_tool("red_flag_lookup", {"query": query, "top_k": top_k}))


@server.tool(
    name="guideline_retrieve",
    description="检索通用就医指引与就诊准备事项。",
)
def guideline_retrieve_tool(
    query: Annotated[str, Field(min_length=1, description="患者的症状或问题原话片段")],
    top_k: Annotated[int, Field(ge=1, le=10)] = 3,
) -> str:
    return _content(call_tool("guideline_retrieve", {"query": query, "top_k": top_k}))


def main() -> None:
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()