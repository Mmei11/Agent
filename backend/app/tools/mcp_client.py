"""Phase C 第 5 课：MCP client（stdio）。

MCP 负责“工具怎么被标准化描述与调用”，不负责“该不该让模型自由选工具”。
所以外部工具进来时一律按只读、外部、白名单处理：

- 默认关闭（MCP_ENABLED=0），不配置就不启动任何子进程；
- 每个 server 必须显式列出 allowed_tools，空列表等于全部拒绝；
- 只注册 read_only + external，write/sensitive 暂不支持；
- 某个 server 起不来就跳过，不阻断主流程。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Callable, Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, create_model

from backend.app.config import load_env_file
from backend.app.tools.catalog import DEFAULT_CATALOG, ToolCatalog
from backend.app.tools.breaker import CircuitBreaker
from backend.app.tools.contract import ToolSpec
from backend.app.tools.external_summaries import SUMMARIZERS

logger = logging.getLogger(__name__)

Transport = Literal["stdio", "http"]


class MCPClientError(RuntimeError):
    """连接、超时、协议或熔断导致的失败，由契约层转成结构化 ToolResult。"""


@dataclass
class MCPServerConfig:
    """一个 MCP server 的启动配置。allowed_tools 为空表示全部拒绝。"""

    name: str
    transport: Transport = "stdio"
    # stdio 用 command/args/env/cwd；http 用 url。两套字段按 transport 二选一。
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    timeout_s: float = 8.0
    start_timeout_s: float = 10.0

    @classmethod
    def from_dict(cls, payload: dict) -> "MCPServerConfig":
        if not isinstance(payload, dict):
            raise ValueError("MCP server 配置必须是对象")

        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("MCP server 缺少 name")

        transport = str(payload.get("transport") or "stdio")
        if transport not in ("stdio", "http"):
            raise ValueError(f"不支持的 transport={transport}")

        command = ""
        url = ""
        if transport == "http":
            url = str(payload.get("url") or "").strip()
            if not url:
                raise ValueError(f"MCP server {name} 缺少 url")
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"MCP server {name} 的 url 必须是 http(s)")
        else:
            command = str(payload.get("command") or "").strip()
            if not command:
                raise ValueError(f"MCP server {name} 缺少 command")

        return cls(
            name=name,
            transport=transport,
            command=command,
            args=[str(item) for item in payload.get("args") or []],
            env={str(k): str(v) for k, v in payload["env"].items()}
            if isinstance(payload.get("env"), dict)
            else None,
            cwd=str(payload["cwd"]) if payload.get("cwd") else None,
            url=url,
            allowed_tools=[str(item) for item in payload.get("allowed_tools") or []],
            timeout_s=float(payload.get("timeout_s") or 8.0),
            start_timeout_s=float(payload.get("start_timeout_s") or 10.0),
        )


def parse_server_configs(raw: str) -> list[MCPServerConfig]:
    """解析 MCP_SERVERS。坏条目跳过，重名只保留第一个。"""
    if not raw.strip():
        return []

    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("MCP_SERVERS 必须是 JSON 数组")

    configs: list[MCPServerConfig] = []
    seen: set[str] = set()
    for item in payload:
        try:
            config = MCPServerConfig.from_dict(item)
        except (ValueError, TypeError) as exc:
            logger.warning("忽略无效的 MCP server 配置：%s", exc)
            continue
        if config.name in seen:
            logger.warning("忽略重名的 MCP server：%s", config.name)
            continue
        seen.add(config.name)
        configs.append(config)
    return configs


def _content_text(result) -> str:
    chunks = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(str(text))
    return "\n".join(chunks).strip()


def normalize_result(result) -> dict:
    """把 MCP 的 CallToolResult 归一化成普通 dict。

    我们自己的 server 用 {"ok": true, "data": ...} 包了一层，这里顺手拆掉；
    其他 server 的 structuredContent 或纯文本也都能接住。
    """
    if getattr(result, "is_error", False):
        return {
            "error": _content_text(result) or "MCP 工具返回错误",
            "error_kind": "unavailable",
        }

    structured = getattr(result, "structured_content", None)
    text = _content_text(result)

    # 文本内容是协议的主通道；structuredContent 只在没有文本时使用。
    # 有些 server 会把字符串返回值再包成 {"result": "<json>"}，这里一并拆开。
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    elif isinstance(structured, dict) and structured:
        payload = structured
        if set(payload) == {"result"} and isinstance(payload["result"], str):
            try:
                payload = json.loads(payload["result"])
            except json.JSONDecodeError:
                pass
    else:
        payload = {}

    if isinstance(payload, dict) and payload.get("ok") is False:
        return {
            "error": payload.get("error") or "MCP 工具返回错误",
            "error_kind": payload.get("error_kind") or "unavailable",
        }
    if isinstance(payload, dict) and payload.get("ok") is True and "data" in payload:
        return payload.get("data") or {}

    return payload if isinstance(payload, dict) else {"text": text}


class MCPSession:
    """一个 server 的 long-lived 会话。

    MCP SDK 是 async 的，ToolSpec.handler 是同步的；这里用一条后台事件循环线程
    把异步会话桥接成同步调用，图节点和契约层不需要感知 async。
    """

    def __init__(self, config: MCPServerConfig, breaker: CircuitBreaker | None = None):
        self.config = config
        self.tools: list = []
        self._breaker = breaker or CircuitBreaker()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    def __enter__(self) -> "MCPSession":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def start(self) -> "MCPSession":
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name=f"mcp-{self.config.name}",
            daemon=True,
        )
        self._thread.start()

        try:
            self._submit(self._connect(), timeout=self.config.start_timeout_s)
        except Exception as exc:
            self.stop()
            raise MCPClientError(
                f"MCP server {self.config.name} 启动失败：{exc}"
            ) from exc
        return self

    async def _connect(self) -> None:
        self._stack = AsyncExitStack()

        if self.config.transport == "http":
            # 两种 transport 产出的都是 (read, write) 流，后面完全一致。
            read, write = await self._stack.enter_async_context(
                streamable_http_client(self.config.url)
            )
        else:
            params = StdioServerParameters(
                command=self.config.command,
                args=list(self.config.args),
                env=self.config.env,
                cwd=self.config.cwd,
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))

        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        self.tools = list((await self._session.list_tools()).tools)

    def _submit(self, coro, timeout: float):
        if self._loop is None:
            raise MCPClientError("MCP 会话未启动")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def call(self, name: str, arguments: dict | None = None) -> dict:
        if self._session is None:
            raise MCPClientError(f"MCP server {self.config.name} 尚未连接")
        if not self._breaker.allow():
            raise MCPClientError(f"MCP server {self.config.name} 熔断中")

        try:
            result = self._submit(
                self._session.call_tool(
                    name,
                    arguments or {},
                    read_timeout_seconds=self.config.timeout_s,
                ),
                timeout=self.config.timeout_s,
            )
        except FutureTimeout as exc:
            self._breaker.record_failure()
            raise TimeoutError(
                f"MCP {self.config.name}.{name} 超过 {self.config.timeout_s}s"
            ) from exc
        except Exception as exc:
            self._breaker.record_failure()
            raise MCPClientError(f"MCP {self.config.name}.{name} 调用失败：{exc}") from exc

        self._breaker.record_success()
        return normalize_result(result)

    def stop(self) -> None:
        if self._stack is not None:
            try:
                self._submit(self._stack.aclose(), timeout=5.0)
            except Exception:
                # 关闭失败不掩盖主流程错误，子进程由 daemon 线程兜底退出。
                pass
            self._stack = None
            self._session = None

        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._loop = None
        self._thread = None


_JSON_TYPES: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def params_model_from_schema(tool_name: str, schema: dict) -> type[BaseModel]:
    """把外部工具的 inputSchema 转成 Pydantic 模型，复用契约层的参数校验。"""
    fields: dict[str, tuple] = {}
    required = set((schema or {}).get("required") or [])
    for key, prop in ((schema or {}).get("properties") or {}).items():
        prop = prop or {}
        py_type = _JSON_TYPES.get(str(prop.get("type")), str)
        if key in required:
            fields[key] = (py_type, ...)
        else:
            fields[key] = (py_type | None, prop.get("default"))

    safe_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in tool_name)
    return create_model(f"MCP_{safe_name}_Params", **fields)


def build_tool_specs(
    session: MCPSession,
    config: MCPServerConfig,
    summarizers: dict[str, Callable[[dict], str]] | None = None,
) -> list[ToolSpec]:
    """按白名单把外部工具声明转成目录里的 ToolSpec。

    summarizers 可选：外部工具返回的是 Markdown 大文本，通用 JSON 截断
    只能看到标题；按工具名提供专用摘要，才能让 decision 上下文有信息量。
    """
    allowed = set(config.allowed_tools)
    specs: list[ToolSpec] = []

    for tool in session.tools:
        if tool.name not in allowed:
            continue

        schema = getattr(tool, "input_schema", None) or {}
        try:
            params_model = params_model_from_schema(tool.name, schema)
        except Exception as exc:
            logger.warning("忽略无法生成参数模型的 MCP 工具 %s：%s", tool.name, exc)
            continue

        specs.append(
            ToolSpec(
                name=tool.name,
                description=f"[外部:{config.name}] {tool.description or ''}".strip(),
                params_model=params_model,
                handler=_tool_handler(session, tool.name),
                # 外层契约的超时略大于 MCP 调用超时，让内层先报出更精确的原因。
                timeout_s=config.timeout_s + 1.0,
                idempotent=True,
                empty_result_mode="fail-open",
                risk_class="read_only",
                scope="external",
                summarize=(summarizers or {}).get(tool.name),
            )
        )
    return specs


def _tool_handler(session: MCPSession, tool_name: str):
    def call(params: BaseModel) -> dict:
        arguments = {
            key: value
            for key, value in params.model_dump().items()
            if value is not None
        }
        return session.call(tool_name, arguments)

    return call


_ACTIVE_SESSIONS: list[MCPSession] = []
_LOADED = False
_LOAD_LOCK = threading.Lock()


def load_external_tools(catalog: ToolCatalog | None = None) -> list[ToolSpec]:
    """把白名单里的外部工具注册进目录。默认关闭，失败跳过。"""
    global _LOADED

    with _LOAD_LOCK:
        if _LOADED:
            return []
        _LOADED = True

        load_env_file()
        if os.getenv("MCP_ENABLED", "0") != "1":
            return []

        try:
            configs = parse_server_configs(os.getenv("MCP_SERVERS", ""))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("MCP_SERVERS 解析失败，外部工具不加载：%s", exc)
            return []

        target = catalog or DEFAULT_CATALOG
        specs: list[ToolSpec] = []

        for config in configs:
            session = MCPSession(config)
            try:
                session.start()
            except MCPClientError as exc:
                logger.warning("跳过 MCP server %s：%s", config.name, exc)
                continue

            _ACTIVE_SESSIONS.append(session)
            for spec in build_tool_specs(session, config, SUMMARIZERS):
                try:
                    target.register(spec)
                except ValueError:
                    # 外部工具不能覆盖内部工具，重名的一律忽略。
                    logger.warning("外部工具与已有工具重名，已忽略：%s", spec.name)
                    continue
                specs.append(spec)

        return specs


def shutdown_external_tools() -> None:
    """关闭所有外部会话；测试或进程退出时调用。"""
    global _LOADED

    with _LOAD_LOCK:
        for session in _ACTIVE_SESSIONS:
            session.stop()
        _ACTIVE_SESSIONS.clear()
        _LOADED = False
