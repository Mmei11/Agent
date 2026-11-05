"""Phase 2 第三课：把 SpanRecord 通过 OTLP 送到 Langfuse。

为什么用标准 OpenTelemetry 协议，而不是 Langfuse 私有 SDK：
- 换后端（Phoenix、Tempo、Honeycomb）只换 exporter，埋点不动；
- 可以保留真实的 started_at / ended_at，面板上的耗时才是准的。
"""

import atexit
import base64
import json
import os
from contextvars import ContextVar
from datetime import datetime
from importlib.metadata import version

from backend.app.observability.tracer import SpanRecord

# 把当前正在导出的记录放进上下文，供自定义 IdGenerator 读取。
_current_record: ContextVar[SpanRecord | None] = ContextVar(
    "langfuse_current_record", default=None
)


class _RecordIdGenerator:
    """让 OTel 使用我们已有的 trace_id / span_id，而不是重新随机生成。

    这样本地 JSONL 里的 id 和 Langfuse 里的 id 完全一致，
    state.trace_id 也能直接跳转到对应 trace。
    """

    def __init__(self) -> None:
        from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

        self._fallback = RandomIdGenerator()

    def generate_span_id(self) -> int:
        record = _current_record.get()
        return int(record.span_id, 16) if record else self._fallback.generate_span_id()

    def generate_trace_id(self) -> int:
        record = _current_record.get()
        return int(record.trace_id, 16) if record else self._fallback.generate_trace_id()

    def is_trace_id_random(self) -> bool:
        # 我们显式提供 trace_id，OTel 不能再走“随机生成”的优化路径。
        return _current_record.get() is None


def build_langfuse_sink():
    """配置齐全时返回 LangfuseSink；缺配置或初始化失败时返回 None。"""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "http://127.0.0.1:3000")
    if not public_key or not secret_key:
        return None
    try:
        return LangfuseSink(
            host=host,
            public_key=public_key,
            secret_key=secret_key,
        )
    except Exception:
        # 观测组件初始化失败不能拖垮主流程。
        return None


class LangfuseSink:
    def __init__(self, *, host: str, public_key: str, secret_key: str):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import Compression, OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{host.rstrip('/')}/api/public/otel/v1/traces",
            headers={
                "Authorization": f"Basic {auth}",
                # 这几个头与 Langfuse 官方 SDK 保持一致，缺少时网关会拒绝请求。
                "x-langfuse-sdk-name": "python",
                "x-langfuse-sdk-version": version("langfuse"),
                "x-langfuse-public-key": public_key,
                # Langfuse 要求直连 exporter 显式声明 ingestion 版本。
                "x-langfuse-ingestion-version": "4",
            },
            compression=Compression.Gzip,
            timeout=5,
        )
        provider = TracerProvider(
            resource=Resource.create({"service.name": "consultation-agent"}),
            id_generator=_RecordIdGenerator(),
        )
        # 后台批量发送：emit 只入队，不在请求链路里等网络。
        provider.add_span_processor(BatchSpanProcessor(exporter))
        self._provider = provider
        # scope 名以 langfuse-sdk 开头，Langfuse 才会按 SDK 约定解码 metadata。
        self._tracer = provider.get_tracer("langfuse-sdk-consultation")
        atexit.register(self.flush)

    def emit(self, record: SpanRecord) -> None:
        if not record.ended_at:
            return

        from opentelemetry.trace import SpanKind, Status, StatusCode

        token = _current_record.set(record)
        try:
            span = self._tracer.start_span(
                name=record.name,
                # 根 span 不传 context：trace_id 由 IdGenerator 强制成我们的 id。
                context=self._parent_context(record) if record.parent_span_id else None,
                start_time=self._to_ns(record.started_at),
                kind=SpanKind.CLIENT if record.kind == "llm" else SpanKind.INTERNAL,
                attributes=self._attributes(record),
            )
            if record.status == "error":
                span.set_status(Status(StatusCode.ERROR, record.error or "error"))
            span.end(end_time=self._to_ns(record.ended_at))
        finally:
            _current_record.reset(token)

    def flush(self) -> None:
        """把队列里的 span 立刻发出去；失败时静默降级。"""
        try:
            self._provider.force_flush(timeout_millis=3000)
        except Exception:
            pass

    @staticmethod
    def _to_ns(timestamp: str) -> int:
        """OTel 的时间戳是 Unix 纳秒整数，不是 datetime。"""
        return int(datetime.fromisoformat(timestamp).timestamp() * 1_000_000_000)

    @staticmethod
    def _parent_context(record: SpanRecord):
        from opentelemetry.trace import (
            NonRecordingSpan,
            SpanContext,
            TraceFlags,
            set_span_in_context,
        )

        trace_id = int(record.trace_id, 16)
        parent_span_id = int(record.parent_span_id, 16)
        parent = SpanContext(
            trace_id=trace_id,
            span_id=parent_span_id,
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        return set_span_in_context(NonRecordingSpan(parent))

    @staticmethod
    def _attributes(record: SpanRecord) -> dict:
        raw = record.attributes or {}
        if record.kind == "llm":
            observation_type = "generation"
        elif record.kind == "tool":
            observation_type = "tool"
        else:
            observation_type = "span"

        attributes = {
            "session.id": record.session_id or "anonymous",
            "langfuse.trace.name": record.trace_name or "consultation.turn",
            "langfuse.observation.type": observation_type,
            "consultation.turn_id": record.turn_id,
            "consultation.trace_id": record.trace_id,
            # 同时写一份 trace 级 metadata，方便在 Langfuse 里按业务 id 找。
            "langfuse.trace.metadata.consultation_trace_id": json.dumps(record.trace_id),
        }

        for key, value in raw.items():
            # v4 的 metadata 约定是逐键写 langfuse.observation.metadata.<key>，
            # 值是 JSON 字符串；单个大 JSON 对象不会被 UI 展开。
            attributes[f"langfuse.observation.metadata.{key}"] = json.dumps(
                value, ensure_ascii=False, default=str
            )
            # 基本类型也平铺一份，方便在筛选器里直接按属性过滤。
            if isinstance(value, (str, int, float, bool)):
                attributes[key] = value
            elif isinstance(value, list) and all(
                isinstance(item, (str, int, float, bool)) for item in value
            ):
                attributes[key] = value
            else:
                attributes[key] = json.dumps(value, ensure_ascii=False, default=str)

        model = raw.get("gen_ai.request.model")
        if model:
            attributes["langfuse.observation.model.name"] = model

        usage = {}
        if raw.get("gen_ai.usage.input_tokens") is not None:
            usage["input"] = raw["gen_ai.usage.input_tokens"]
        if raw.get("gen_ai.usage.output_tokens") is not None:
            usage["output"] = raw["gen_ai.usage.output_tokens"]
        if usage:
            attributes["langfuse.observation.usage_details"] = json.dumps(usage)

        if raw.get("llm.cost_estimate") is not None:
            attributes["langfuse.observation.cost_details"] = json.dumps(
                {"total": raw["llm.cost_estimate"]}
            )
        if raw.get("prompt.name"):
            attributes["langfuse.observation.prompt.name"] = raw["prompt.name"]
        if raw.get("prompt.version"):
            attributes["langfuse.observation.prompt.version"] = raw["prompt.version"]

        if record.status == "error":
            attributes["langfuse.observation.level"] = "ERROR"
            attributes["langfuse.observation.status_message"] = record.error or "error"

        return attributes