"""Phase 2：可观测性。对外只暴露这一组入口，上层不关心 sink 是什么。"""

from backend.app.observability.tracer import (
    JsonlSink,
    MemorySink,
    MultiSink,
    NullSink,
    SpanRecord,
    TraceScope,
    current_trace,
    get_sink,
    span,
    start_trace,
)
from backend.app.observability.instrument import traced_node
from backend.app.observability.redact import redact_record, redact_text

__all__ = [
    "JsonlSink",
    "MemorySink",
    "MultiSink",
    "NullSink",
    "SpanRecord",
    "TraceScope",
    "current_trace",
    "get_sink",
    "redact_record",
    "redact_text",
    "span",
    "start_trace",
    "traced_node",
]