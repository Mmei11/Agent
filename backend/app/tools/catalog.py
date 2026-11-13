"""Phase C：工具目录。

决策层、执行层和 MCP 都从同一份目录读取工具声明，避免三处各写一份。
目录只把“只读工具”暴露给模型；写入/敏感工具可以注册，但不会被自动选择。
"""

from __future__ import annotations

from typing import Iterable

from backend.app.tools.contract import ToolResult, ToolSpec
from backend.app.tools.knowledge_tools import KNOWLEDGE_TOOL_SPECS


class ToolCatalog:
    """工具的单一登记处。"""

    def __init__(self, specs: Iterable[ToolSpec] = ()):
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"工具重复注册：{spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> list[str]:
        return sorted(self._specs)

    def all(self) -> list[ToolSpec]:
        return [self._specs[name] for name in self.names()]

    def auto_callable(self) -> list[ToolSpec]:
        return [spec for spec in self.all() if spec.auto_callable]

    def prompt_entries(self) -> list[dict]:
        """给模型看的工具清单：只含允许自动调用的工具。"""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "risk_class": spec.risk_class,
                "scope": spec.scope,
                "input_schema": spec.params_model.model_json_schema(),
            }
            for spec in self.auto_callable()
        ]

    def call(self, name: str, params: dict) -> ToolResult:
        spec = self.get(name)
        if spec is None:
            return ToolResult(
                ok=False,
                name=name,
                error=f"未注册的工具：{name}",
                error_kind="invalid_params",
            )
        return spec.call(params)


DEFAULT_CATALOG = ToolCatalog(KNOWLEDGE_TOOL_SPECS.values())
