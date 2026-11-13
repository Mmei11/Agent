"""Phase C 第 5 课：MCP client（stdio）。

单元测试不启动子进程；集成测试连项目自己的 MCP server，走真实协议往返。
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import types

from backend.app.tools.catalog import ToolCatalog
from backend.app.tools.knowledge_tools import KNOWLEDGE_TOOL_SPECS
from backend.app.tools.mcp_client import (
    CircuitBreaker,
    MCPClientError,
    MCPServerConfig,
    MCPSession,
    build_tool_specs,
    load_external_tools,
    normalize_result,
    params_model_from_schema,
    parse_server_configs,
    shutdown_external_tools,
)

ROOT = Path(__file__).resolve().parents[2]


def test_parse_stdio_config():
    configs = parse_server_configs(
        json.dumps(
            [
                {
                    "name": "knowledge",
                    "command": "python",
                    "args": ["-m", "server"],
                    "allowed_tools": ["department_route"],
                    "timeout_s": 5,
                }
            ]
        )
    )

    assert len(configs) == 1
    assert configs[0].name == "knowledge"
    assert configs[0].transport == "stdio"
    assert configs[0].allowed_tools == ["department_route"]
    assert configs[0].timeout_s == 5.0


def test_parse_skips_bad_and_duplicate_configs():
    configs = parse_server_configs(
        json.dumps(
            [
                {"name": "a", "command": "python"},
                {"name": "a", "command": "python"},
                {"name": "b", "command": "python", "transport": "http"},
                {"command": "python"},
            ]
        )
    )

    assert [config.name for config in configs] == ["a"]


def test_parse_http_config():
    configs = parse_server_configs(
        json.dumps(
            [
                {
                    "name": "web",
                    "transport": "http",
                    "url": "https://example.com/mcp",
                    "allowed_tools": ["lookup"],
                }
            ]
        )
    )

    assert configs[0].transport == "http"
    assert configs[0].url == "https://example.com/mcp"
    assert configs[0].command == ""


def test_parse_rejects_non_http_url():
    configs = parse_server_configs(
        json.dumps([{"name": "x", "transport": "http", "url": "ftp://example.com"}])
    )

    assert configs == []


def test_empty_servers_means_none():
    assert parse_server_configs("") == []


def test_params_model_marks_required_and_optional():
    model = params_model_from_schema(
        "demo",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
            "required": ["query"],
        },
    )

    params = model(query="头痛")
    assert params.top_k is None

    with pytest.raises(Exception):
        model()


def test_normalize_unwraps_ok_data_envelope():
    result = SimpleNamespace(
        is_error=False,
        structured_content=None,
        content=[
            SimpleNamespace(
                text=json.dumps({"ok": True, "data": {"department": "神经内科"}})
            )
        ],
    )

    assert normalize_result(result) == {"department": "神经内科"}


def test_normalize_keeps_server_error():
    result = SimpleNamespace(
        is_error=False,
        structured_content=None,
        content=[
            SimpleNamespace(
                text=json.dumps(
                    {"ok": False, "error": "执行超过 8s", "error_kind": "timeout"}
                )
            )
        ],
    )

    assert normalize_result(result) == {
        "error": "执行超过 8s",
        "error_kind": "timeout",
    }


def test_normalize_plain_text_falls_back_to_text():
    result = SimpleNamespace(
        is_error=False,
        structured_content=None,
        content=[SimpleNamespace(text="纯文本结果")],
    )

    assert normalize_result(result) == {"text": "纯文本结果"}


def test_normalize_unwraps_structured_result_wrapper():
    result = SimpleNamespace(
        is_error=False,
        structured_content={"result": json.dumps({"ok": True, "data": {"x": 1}})},
        content=[],
    )

    assert normalize_result(result) == {"x": 1}


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_circuit_breaker_opens_and_recovers():
    clock = _Clock()
    breaker = CircuitBreaker(threshold=2, cooldown_s=10, clock=clock)

    breaker.record_failure()
    assert breaker.allow()
    breaker.record_failure()
    assert not breaker.allow()

    clock.now += 10
    assert breaker.allow()

    breaker.record_success()
    breaker.record_failure()
    assert breaker.allow()


class _FakeSession:
    def __init__(self, tools):
        self.tools = tools

    def call(self, name, arguments):
        return {"echo": name, **arguments}


def _tool(name: str, description: str = "demo") -> types.Tool:
    return types.Tool(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def test_build_tool_specs_respects_whitelist():
    session = _FakeSession([_tool("allowed"), _tool("blocked")])
    config = MCPServerConfig(name="demo", command="python", allowed_tools=["allowed"])

    specs = build_tool_specs(session, config)

    assert [spec.name for spec in specs] == ["allowed"]
    spec = specs[0]
    assert spec.scope == "external"
    assert spec.risk_class == "read_only"
    assert spec.auto_callable
    assert spec.call({"query": "头痛"}).data == {"echo": "allowed", "query": "头痛"}


def test_empty_whitelist_registers_nothing():
    session = _FakeSession([_tool("a"), _tool("b")])
    config = MCPServerConfig(name="demo", command="python", allowed_tools=[])

    assert build_tool_specs(session, config) == []


def test_external_tools_are_off_by_default(monkeypatch):
    shutdown_external_tools()
    monkeypatch.delenv("MCP_ENABLED", raising=False)

    assert load_external_tools(ToolCatalog()) == []


@pytest.fixture(scope="module")
def live_session():
    config = MCPServerConfig(
        name="knowledge-test",
        command=sys.executable,
        args=["-m", "backend.app.tools.mcp_server"],
        cwd=str(ROOT),
        env={**os.environ, "KNOWLEDGE_BACKEND": "stub", "PYTHONPATH": str(ROOT)},
        allowed_tools=["department_route"],
        timeout_s=10.0,
        start_timeout_s=15.0,
    )
    session = MCPSession(config)
    session.start()
    yield session
    session.stop()


def test_live_server_lists_contract_tools(live_session):
    names = {tool.name for tool in live_session.tools}

    assert names == set(KNOWLEDGE_TOOL_SPECS)


def test_live_call_round_trip(live_session):
    data = live_session.call("department_route", {"query": "头痛"})

    assert data["department"] == "神经内科"


def test_live_spec_registers_as_external(live_session):
    config = MCPServerConfig(
        name="knowledge-test", command=sys.executable, allowed_tools=["department_route"]
    )

    specs = build_tool_specs(live_session, config)

    assert [spec.name for spec in specs] == ["department_route"]
    result = specs[0].call({"query": "头痛"})
    assert result.ok
    assert result.data["department"] == "神经内科"


def test_start_failure_is_wrapped():
    config = MCPServerConfig(
        name="broken",
        command=sys.executable,
        args=["-c", "raise SystemExit(1)"],
        start_timeout_s=3.0,
    )
    session = MCPSession(config)

    with pytest.raises(MCPClientError):
        session.start()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"HTTP server 未在 {timeout}s 内启动")


@pytest.fixture(scope="module")
def live_http_session():
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "backend.app.tools.mcp_server",
            "--http",
            "--port",
            str(port),
        ],
        cwd=str(ROOT),
        env={**os.environ, "KNOWLEDGE_BACKEND": "stub", "PYTHONPATH": str(ROOT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        _wait_port(port)
        config = MCPServerConfig(
            name="knowledge-http",
            transport="http",
            url=f"http://127.0.0.1:{port}/mcp",
            allowed_tools=["department_route"],
            timeout_s=10.0,
            start_timeout_s=15.0,
        )
        session = MCPSession(config)
        session.start()
        try:
            yield session
        finally:
            session.stop()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def test_http_server_lists_contract_tools(live_http_session):
    names = {tool.name for tool in live_http_session.tools}

    assert names == set(KNOWLEDGE_TOOL_SPECS)


def test_http_call_round_trip(live_http_session):
    data = live_http_session.call("department_route", {"query": "头痛"})

    assert data["department"] == "神经内科"


def test_http_spec_registers_as_external(live_http_session):
    config = MCPServerConfig(
        name="knowledge-http",
        transport="http",
        url="http://127.0.0.1:1/mcp",
        allowed_tools=["department_route"],
    )

    specs = build_tool_specs(live_http_session, config)

    assert [spec.name for spec in specs] == ["department_route"]
    assert specs[0].scope == "external"
    result = specs[0].call({"query": "头痛"})
    assert result.ok
    assert result.data["department"] == "神经内科"


def test_external_tool_reaches_catalog_prompt(live_http_session):
    config = MCPServerConfig(
        name="knowledge-http",
        transport="http",
        url="http://127.0.0.1:1/mcp",
        allowed_tools=["department_route"],
    )
    catalog = ToolCatalog()
    for spec in build_tool_specs(live_http_session, config):
        catalog.register(spec)

    entry = next(item for item in catalog.prompt_entries() if item["name"] == "department_route")

    assert entry["scope"] == "external"
    assert entry["risk_class"] == "read_only"
    assert catalog.call("department_route", {"query": "头痛"}).data["department"] == "神经内科"


def test_external_timeout_maps_to_structured_error():
    class _SlowSession(_FakeSession):
        def call(self, name, arguments):
            raise TimeoutError("模拟超时")

    config = MCPServerConfig(name="slow", command="python", allowed_tools=["slow"])
    spec = build_tool_specs(_SlowSession([_tool("slow")]), config)[0]

    result = spec.call({"query": "头痛"})

    assert not result.ok
    assert result.error_kind == "timeout"


def test_http_connection_failure_is_wrapped():
    port = _free_port()
    config = MCPServerConfig(
        name="down",
        transport="http",
        url=f"http://127.0.0.1:{port}/mcp",
        start_timeout_s=3.0,
    )

    with pytest.raises(MCPClientError):
        MCPSession(config).start()
