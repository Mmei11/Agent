"""Phase 2 第三课：采集端脱敏与 sink 组合。案例均为虚构。"""

import json

import pytest

from backend.app.observability import (
    JsonlSink,
    MemorySink,
    MultiSink,
    redact_text,
    span,
    start_trace,
)
from backend.app.observability.langfuse_sink import build_langfuse_sink
from backend.app.observability.redact import redact_value


def test_redact_text_masks_phone_id_email_and_name():
    text = "我叫张三，电话13800138000，身份证110101199001011234，邮箱zhang.san@example.com"

    clean = redact_text(text)

    for raw in ("13800138000", "110101199001011234", "zhang.san@example.com", "张三"):
        assert raw not in clean
    assert "[手机号]" in clean
    assert "[身份证]" in clean
    assert "[邮箱]" in clean
    assert "[姓名]" in clean


def test_redact_value_masks_sensitive_key_and_nested_values():
    value = {
        "phone": "13900139000",
        "note": "电话13800138000",
        "nested": {"email": "a@b.com"},
        "items": ["13800138000"],
    }

    clean = redact_value(value)
    dumped = json.dumps(clean, ensure_ascii=False)

    assert "13900139000" not in dumped
    assert "13800138000" not in dumped
    assert "a@b.com" not in dumped
    assert clean["phone"] == "[REDACTED]"


def test_trace_scope_redacts_before_sink_and_memory():
    sink = MemorySink()

    with start_trace("sess-pii", 1, sink=sink) as trace:
        with span("intake") as record:
            record.attributes["note"] = "电话13800138000"
            record.attributes["user_name"] = "张三"

    dumped_sink = json.dumps([item.to_dict() for item in sink.records], ensure_ascii=False)
    dumped_memory = json.dumps([item.to_dict() for item in trace.spans], ensure_ascii=False)

    assert "13800138000" not in dumped_sink
    assert "张三" not in dumped_sink
    assert "13800138000" not in dumped_memory
    assert sink.records[0].attributes["note"] == "电话[手机号]"
    assert sink.records[0].attributes["user_name"] == "[REDACTED]"


def test_jsonl_sink_never_contains_raw_pii(tmp_path):
    path = tmp_path / "traces.jsonl"

    with start_trace("sess-pii", 1, sink=JsonlSink(path)):
        with span("intake") as record:
            record.attributes["note"] = "身份证110101199001011234"

    text = path.read_text(encoding="utf-8")
    assert "110101199001011234" not in text
    assert "[身份证]" in text


def test_multi_sink_isolates_single_sink_failure():
    class BrokenSink:
        def emit(self, record):
            raise RuntimeError("one backend down")

    memory = MemorySink()

    with start_trace("sess-multi", 1, sink=MultiSink([BrokenSink(), memory])):
        with span("intake"):
            pass

    # 第一个 sink 挂了，第二个仍然收到数据（叶子 + 根）。
    assert {record.name for record in memory.records} == {"intake", "consultation.turn"}


def test_langfuse_sink_is_skipped_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    assert build_langfuse_sink() is None