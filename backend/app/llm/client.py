"""Phase 1：统一模型调用出口。全项目只有这里 import openai。

上层只认 LLMResult，不认具体 SDK 的响应对象；
换供应商、换 SDK、加网关，都只改这个文件。
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path

import instructor
from openai import APIConnectionError, APITimeoutError, OpenAI
from pydantic import BaseModel

from backend.app.observability import span

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
DEFAULT_TIMEOUT = 10.0  # 单次尝试的超时，不是整次调用的预算
DEFAULT_MAX_RETRIES = 1  # 运输层重试交给 SDK，内容层修复留给 instructor


@dataclass(frozen=True)
class LLMResult:
    """一次模型调用的归一化结果。"""

    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    cost_estimate: float | None


class LLMError(RuntimeError):
    """模型调用失败。retryable 表示同一请求稍后重发是否有意义。"""

    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def load_env_file() -> None:
    """读取项目根目录的 .env；终端已有同名变量时以终端为准。"""
    env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    if not env_path.exists():
        return

    allowed_names = {
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "MODEL_PRICE_INPUT_PER_1K",
        "MODEL_PRICE_OUTPUT_PER_1K",
    }
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or name not in allowed_names:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(name, value)


def estimate_cost(input_tokens: int | None, output_tokens: int | None) -> float | None:
    """按环境变量里的单价估算成本；没配单价时返回 None，不瞎猜价格。"""
    price_in = os.getenv("MODEL_PRICE_INPUT_PER_1K")
    price_out = os.getenv("MODEL_PRICE_OUTPUT_PER_1K")
    if not price_in or not price_out:
        return None
    try:
        return round(
            (input_tokens or 0) / 1000 * float(price_in)
            + (output_tokens or 0) / 1000 * float(price_out),
            6,
        )
    except (TypeError, ValueError):
        return None


_client: OpenAI | None = None


def get_client() -> OpenAI:
    """懒加载单例。测试时通过 chat(client=...) 注入假对象，不走这里。"""
    global _client
    if _client is None:
        load_env_file()
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise LLMError("缺少 DASHSCOPE_API_KEY", retryable=False)
        _client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL),
            max_retries=DEFAULT_MAX_RETRIES,
        )
    return _client


def _is_retryable(exc: Exception) -> bool:
    """判断这个错误再试一次有没有意义。"""
    status = getattr(exc, "status_code", None)
    if status is None:
        # 没有状态码不代表都能重试：只认网络类异常，代码 bug 重试也没用。
        return isinstance(exc, (APIConnectionError, APITimeoutError))
    return status in (408, 429) or status >= 500


_structured_client = None


def _llm_span_attributes(
    *,
    model: str,
    operation: str,
    temperature: float,
    max_tokens: int,
    prompt_name: str = "",
    prompt_version: str = "",
) -> dict:
    """按 OpenTelemetry GenAI 语义命名，换观测平台时映射关系不变。"""
    attributes = {
        "gen_ai.system": "dashscope",
        "gen_ai.operation.name": operation,
        "gen_ai.request.model": model,
        "gen_ai.request.temperature": temperature,
        "gen_ai.request.max_tokens": max_tokens,
    }
    if prompt_name:
        attributes["prompt.name"] = prompt_name
    if prompt_version:
        attributes["prompt.version"] = prompt_version
    return attributes


def _record_usage(record, result: LLMResult) -> None:
    """把用量写进当前 span；拿不到的值不写，避免出现误导性的 null。"""
    record.attributes["gen_ai.response.model"] = result.model
    if result.input_tokens is not None:
        record.attributes["gen_ai.usage.input_tokens"] = result.input_tokens
    if result.output_tokens is not None:
        record.attributes["gen_ai.usage.output_tokens"] = result.output_tokens
    if result.cost_estimate is not None:
        record.attributes["llm.cost_estimate"] = result.cost_estimate
    record.attributes["llm.latency_ms"] = result.latency_ms


def get_structured_client():
    """instructor 包装同一个 OpenAI 客户端，共享地址、Key 和运输层重试。"""
    global _structured_client
    if _structured_client is None:
        _structured_client = instructor.from_openai(get_client(), mode=instructor.Mode.JSON)
    return _structured_client


def chat_structured(
    messages: list[dict],
    response_model: type[BaseModel],
    *,
    model: str | None = None,
    max_retries: int = 2,
    max_tokens: int = 800,
    temperature: float = 0.0,
    timeout: float = DEFAULT_TIMEOUT,
    structured_client=None,
    prompt_name: str = "",
    prompt_version: str = "",
) -> tuple[BaseModel, LLMResult]:
    """请求结构化输出，返回（校验后的对象, 归一化用量）。

    max_retries 是内容层重试：校验失败时把错误回喂模型。
    注意：只统计最后一次成功尝试的 usage，失败重试的 token 不在其中。
    """
    active_client = structured_client or get_structured_client()
    active_model = model or os.getenv("DASHSCOPE_MODEL", DEFAULT_MODEL)
    attributes = _llm_span_attributes(
        model=active_model,
        operation="chat",
        temperature=temperature,
        max_tokens=max_tokens,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
    )

    with span("llm.chat_structured", kind="llm", attributes=attributes) as record:
        started = time.perf_counter()
        try:
            parsed, completion = active_client.chat.completions.create_with_completion(
                model=active_model,
                messages=messages,
                response_model=response_model,
                max_retries=max_retries,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
                extra_body={"enable_thinking": False},
            )
        except Exception as exc:
            # 失败原因留在 span 上；异常仍按原有契约包装成 LLMError。
            record.attributes["error.retryable"] = _is_retryable(exc)
            raise LLMError(
                f"结构化输出失败：{type(exc).__name__}",
                retryable=_is_retryable(exc),
            ) from exc

        latency_ms = round((time.perf_counter() - started) * 1000)
        choices = getattr(completion, "choices", None)
        usage = getattr(completion, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)

        result = LLMResult(
            content=choices[0].message.content if choices else "",
            model=getattr(completion, "model", None) or active_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_estimate=estimate_cost(input_tokens, output_tokens),
        )
        _record_usage(record, result)
        return parsed, result


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    response_format: dict | None = None,
    max_tokens: int = 512,
    temperature: float = 0.0,
    timeout: float = DEFAULT_TIMEOUT,
    client: OpenAI | None = None,
    prompt_name: str = "",
    prompt_version: str = "",
) -> LLMResult:
    """发一次模型请求，把响应归一化成 LLMResult。"""
    active_client = client or get_client()
    active_model = model or os.getenv("DASHSCOPE_MODEL", DEFAULT_MODEL)
    attributes = _llm_span_attributes(
        model=active_model,
        operation="chat",
        temperature=temperature,
        max_tokens=max_tokens,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
    )

    kwargs = {
        "model": active_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": timeout,
        # 显式关闭思考模式，锁定非思考输出价；否则供应商默认值变化会直接抬高成本。
        "extra_body": {"enable_thinking": False},
    }
    if response_format is not None:
        kwargs["response_format"] = response_format

    with span("llm.chat", kind="llm", attributes=attributes) as record:
        started = time.perf_counter()
        try:
            response = active_client.chat.completions.create(**kwargs)
        except Exception as exc:
            record.attributes["error.retryable"] = _is_retryable(exc)
            # 只暴露异常类型，不带 messages 和 Key，避免敏感内容进日志。
            raise LLMError(
                f"模型调用失败：{type(exc).__name__}",
                retryable=_is_retryable(exc),
            ) from exc

        latency_ms = round((time.perf_counter() - started) * 1000)
        choices = getattr(response, "choices", None)
        if not choices:
            record.attributes["error.retryable"] = False
            raise LLMError("模型返回空 choices", retryable=False)

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        result = LLMResult(
            content=choices[0].message.content or "",
            model=getattr(response, "model", None) or active_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_estimate=estimate_cost(input_tokens, output_tokens),
        )
        _record_usage(record, result)
        return result