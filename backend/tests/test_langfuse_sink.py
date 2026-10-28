"""Phase 2 第三课：Langfuse 适配层的纯逻辑测试（不联网）。"""

import json

from backend.app.observability.langfuse_sink import (
    LangfuseSink,
    _current_record,
    _RecordIdGenerator,
)
from backend.app.observability.tracer import SpanRecord


def _record(**overrides) -> SpanRecord:
    base = dict(
        trace_id="a" * 32,
        span_id="b" * 16,
        parent_span_id=None,
        session_id="sess",
        turn_id=1,
        name="decision",
        kind="node",
        started_at="2026-10-28T00:00:00.000+00:00",
        ended_at="2026-10-28T00:00:01.000+00:00",
        duration_ms=1000,
        attributes={},
    )
    base.update(overrides)
    return SpanRecord(**base)


def test_id_generator_reuses_our_trace_and_span_ids():
    record = _record()
    token = _current_record.set(record)
    try:
        generator = _RecordIdGenerator()
        assert generator.generate_trace_id() == int("a" * 32, 16)
        assert generator.generate_span_id() == int("b" * 16, 16)
        assert generator.is_trace_id_random() is False
    finally:
        _current_record.reset(token)


def test_attributes_map_model_usage_and_prompt_version():
    record = _record(
        kind="llm",
        attributes={
            "gen_ai.request.model": "qwen-plus",
            "gen_ai.usage.input_tokens": 120,
            "gen_ai.usage.output_tokens": 8,
            "llm.cost_estimate": 0.0002,
            "prompt.name": "intake.extract",
            "prompt.version": "v1",
            "final_action": "CALL_TOOL",
        },
    )

    attributes = LangfuseSink._attributes(record)

    assert attributes["langfuse.observation.type"] == "generation"
    assert attributes["langfuse.observation.model.name"] == "qwen-plus"
    assert json.loads(attributes["langfuse.observation.usage_details"]) == {
        "input": 120,
        "output": 8,
    }
    assert json.loads(attributes["langfuse.observation.cost_details"]) == {"total": 0.0002}
    assert attributes["langfuse.observation.prompt.version"] == "v1"
    # metadata 逐键写入，值是 JSON 字符串。
    assert attributes["langfuse.observation.metadata.final_action"] == '"CALL_TOOL"'


def test_attributes_mark_error_level():
    record = _record(status="error", error="LLMError")

    attributes = LangfuseSink._attributes(record)

    assert attributes["langfuse.observation.level"] == "ERROR"
    assert attributes["langfuse.observation.status_message"] == "LLMError"