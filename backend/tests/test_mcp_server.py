"""Phase 4：MCP server 暴露的工具与契约一致。"""

import asyncio
import json

import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from backend.app.tools import mcp_server
from backend.app.tools.contract import ToolResult
from backend.app.tools.knowledge_tools import KNOWLEDGE_TOOL_SPECS


def _ok(name: str, data: dict) -> ToolResult:
    return ToolResult(ok=True, name=name, data=data, latency_ms=3)


def _listed():
    return asyncio.run(mcp_server.server.list_tools())


def test_exposes_every_contract_tool():
    names = {tool.name for tool in _listed()}
    assert names == set(KNOWLEDGE_TOOL_SPECS)


def test_schema_stays_in_sync_with_params_model():
    """MCP 的 inputSchema 与 ToolSpec 的 Pydantic 模型必须一致。

    两边是各自导出的，任何一边改了字段而另一边没跟上，这条会红。
    """
    listed = {tool.name: tool for tool in _listed()}
    for name, spec in KNOWLEDGE_TOOL_SPECS.items():
        expected = spec.params_model.model_json_schema()
        actual = listed[name].input_schema
        assert set(actual["properties"]) == set(expected["properties"]), name
        assert set(actual["required"]) == set(expected.get("required", [])), name


def test_success_is_returned_as_json_content(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "call_tool",
        lambda name, params: _ok(name, {"department": "神经内科", "hits": []}),
    )
    result = asyncio.run(
        mcp_server.server.call_tool("department_route", {"query": "头痛"})
    )
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is True
    assert payload["data"]["department"] == "神经内科"


def test_failure_is_content_not_exception(monkeypatch):
    """工具失败时客户端收到结构化内容，而不是连接断开。"""
    monkeypatch.setattr(
        mcp_server,
        "call_tool",
        lambda name, params: ToolResult(
            ok=False, name=name, error="执行超过 5.0s", error_kind="timeout"
        ),
    )
    result = asyncio.run(
        mcp_server.server.call_tool("department_route", {"query": "头痛"})
    )
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert payload["error_kind"] == "timeout"


def test_contract_arguments_are_passed_through(monkeypatch):
    captured = {}

    def fake(name, params):
        captured.update(params)
        return _ok(name, {})

    monkeypatch.setattr(mcp_server, "call_tool", fake)
    asyncio.run(
        mcp_server.server.call_tool(
            "department_route", {"query": "头痛", "risk_level": "high", "top_k": 2}
        )
    )
    assert captured == {"query": "头痛", "risk_level": "high", "top_k": 2}


def test_client_roundtrip_over_memory_streams(monkeypatch):
    """走一遍真正的 MCP 协议：初始化、列工具、调工具。"""
    monkeypatch.setattr(
        mcp_server,
        "call_tool",
        lambda name, params: _ok(name, {"department": "神经内科"}),
    )

    async def roundtrip():
        async with create_client_server_memory_streams() as (
            client_streams,
            server_streams,
        ):
            lowlevel = mcp_server.server._lowlevel_server
            task = asyncio.create_task(
                lowlevel.run(*server_streams, lowlevel.create_initialization_options())
            )
            try:
                async with ClientSession(*client_streams) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == set(
                        KNOWLEDGE_TOOL_SPECS
                    )

                    result = await session.call_tool(
                        "department_route", {"query": "头痛"}
                    )
                    return json.loads(result.content[0].text)
            finally:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

    payload = asyncio.run(roundtrip())
    assert payload["data"]["department"] == "神经内科"