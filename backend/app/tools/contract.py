"""Phase 4：工具契约。

一个工具必须说清五件事：叫什么、干什么、参数长什么样、多久算超时、
失败与"查无结果"分别意味着什么。这里只定义契约，不绑定任何具体工具。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ValidationError

FailureKind = Literal["invalid_params", "timeout", "unavailable"]

# 空结果的安全含义。red_flag_lookup 这类工具必须 fail-close：
# 查不到不等于"没有风险"，只能当作"不确定"往保守方向走。
EmptyResultMode = Literal["fail-close", "fail-open"]

# 风险等级决定“能不能让模型自动调”：
# - read_only：只读查询，可以自动调；
# - write：会改变外部状态，必须人工确认；
# - sensitive：涉及身份、支付、定位等，默认不接入。
ToolRisk = Literal["read_only", "write", "sensitive"]

# 作用域决定工具在哪里运行：进程内实现，还是通过 MCP 接到外部服务。
ToolScope = Literal["internal", "external"]

# 工具结果进模型上下文前的字数上限。原始结果仍完整留在 state 里给渲染和审计用。
TOOL_SUMMARY_MAX_CHARS = 400


def clip_summary(text: str, limit: int = TOOL_SUMMARY_MAX_CHARS) -> str:
    """按字符数截断摘要，留一个省略号表示后面还有内容。"""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的归一化结果。失败也返回对象，不抛异常给上层。"""

    ok: bool
    name: str
    data: dict | None = None
    error: str | None = None
    error_kind: FailureKind | None = None
    latency_ms: int = 0


@dataclass(frozen=True)
class ToolSpec:
    """工具的完整契约。注册表里存的就是它，而不是裸函数。"""

    name: str
    description: str  # 给模型看的，决定它调不调这个工具
    params_model: type[BaseModel]
    handler: Callable[[BaseModel], dict]
    timeout_s: float = 5.0
    idempotent: bool = True
    empty_result_mode: EmptyResultMode = "fail-open"
    risk_class: ToolRisk = "read_only"
    scope: ToolScope = "internal"
    # 把原始结果压成进 prompt 的摘要。None 时由目录用通用 JSON 截断兜底。
    summarize: Callable[[dict], str] | None = None

    @property
    def auto_callable(self) -> bool:
        """只有只读工具允许被模型自动选择；写入/敏感工具必须人工确认。"""
        return self.risk_class == "read_only"

    def call(self, raw_params: dict) -> ToolResult:
        """校验参数 → 限时执行 → 归一化。三类失败分开报，不混成一个 error 字符串。"""
        started = time.perf_counter()

        try:
            params = self.params_model(**(raw_params or {}))
        except ValidationError as exc:
            # 参数不合法说明 Schema 没挡住上游，属于调用方的问题，重试没意义
            return ToolResult(
                ok=False, name=self.name, error=str(exc), error_kind="invalid_params"
            )

        # 不用 with：__exit__ 会等待线程结束，超时就形同虚设。
        pool = ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self.handler, params)
        try:
            data = future.result(timeout=self.timeout_s)
        except TimeoutError:
            future.cancel()
            return ToolResult(
                ok=False,
                name=self.name,
                error=f"执行超过 {self.timeout_s}s",
                error_kind="timeout",
                latency_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                name=self.name,
                error=f"{type(exc).__name__}: {exc}",
                error_kind="unavailable",
                latency_ms=_elapsed_ms(started),
            )
        finally:
            # 超时的线程可能还在跑，不阻塞等待；由它自己结束。
            pool.shutdown(wait=False)

        return ToolResult(
            ok=True, name=self.name, data=data, latency_ms=_elapsed_ms(started)
        )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def tool_schema(spec: ToolSpec) -> dict:
    """把契约转成可给外部消费的声明，MCP 的 tools/list 也是这个形状。"""
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.params_model.model_json_schema(),
        "timeout_s": spec.timeout_s,
        "idempotent": spec.idempotent,
        "empty_result_mode": spec.empty_result_mode,
        "risk_class": spec.risk_class,
        "scope": spec.scope,
        "auto_callable": spec.auto_callable,
    }