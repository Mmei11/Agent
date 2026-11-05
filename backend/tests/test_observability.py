"""Phase 2 第一课：观测数据模型的行为约定。案例均为虚构。"""

import json
from datetime import datetime, timezone

import pytest

from backend.app.observability import (
    JsonlSink,
    MemorySink,
    current_trace,
    span,
    start_trace,
)


def test_span_tree_links_parent_and_child():
    sink = MemorySink()

    with start_trace("session-1", 1, sink=sink) as trace:
        with span("intake", kind="node") as parent:
            with span("llm.intake", kind="llm") as child:
                child.attributes["gen_ai.request.model"] = "qwen-plus"
            parent.attributes["fact_count"] = 2
        with span("risk_check", kind="node"):
            pass

    by_name = {record.name: record for record in sink.records}
    assert {record.trace_id for record in sink.records} == {trace.trace_id}
    assert all(record.session_id == "session-1" for record in sink.records)
    assert by_name["consultation.turn"].parent_span_id is None
    assert by_name["intake"].parent_span_id == by_name["consultation.turn"].span_id
    assert by_name["risk_check"].parent_span_id == by_name["consultation.turn"].span_id
    assert by_name["llm.intake"].parent_span_id == by_name["intake"].span_id
    assert by_name["llm.intake"].attributes["gen_ai.request.model"] == "qwen-plus"
    assert by_name["intake"].attributes["fact_count"] == 2
    assert all(record.status == "ok" for record in sink.records)
    assert all(record.ended_at for record in sink.records)


def test_exception_marks_span_error_and_keeps_tree_intact():
    sink = MemorySink()

    with start_trace("session-2", 1, sink=sink):
        with pytest.raises(ValueError):
            with span("tool", kind="tool"):
                raise ValueError("tool exploded")
        # 异常 span 必须出栈，否则下一个兄弟节点会被错挂到它下面。
        with span("output", kind="node"):
            pass

    by_name = {record.name: record for record in sink.records}
    assert by_name["tool"].status == "error"
    assert by_name["tool"].error == "ValueError"
    assert by_name["output"].parent_span_id == by_name["consultation.turn"].span_id


def test_sink_failure_never_breaks_main_flow():
    class BrokenSink:
        def emit(self, record):
            raise RuntimeError("observability backend down")

    reached = []

    with start_trace("session-3", 1, sink=BrokenSink()) as trace:
        with span("intake", kind="node"):
            reached.append("intake finished")

    assert reached == ["intake finished"]
    # sink 挂了，内存里仍留有记录（叶子 span + 根 span），后续查询不受影响。
    assert len(trace.spans) == 2


def test_span_outside_trace_is_noop():
    assert current_trace() is None

    with span("background_job", kind="node") as noop:
        noop.attributes["anything"] = 1

    assert noop.attributes == {"anything": 1}


def test_jsonl_sink_writes_one_line_per_span(tmp_path):
    path = tmp_path / "traces.jsonl"

    with start_trace("session-4", 1, sink=JsonlSink(path)):
        with span(
            "llm.decision",
            kind="llm",
            attributes={"gen_ai.usage.input_tokens": 120, "called_at": datetime.now(timezone.utc)},
        ):
            pass

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # 叶子 span + 根 span
    payload = next(json.loads(line) for line in lines if json.loads(line)["name"] == "llm.decision")
    assert payload["name"] == "llm.decision"
    assert payload["kind"] == "llm"
    assert payload["session_id"] == "session-4"
    assert payload["attributes"]["gen_ai.usage.input_tokens"] == 120


def test_turn_id_and_trace_id_change_per_request_but_session_stays():
    sink = MemorySink()

    with start_trace("session-5", 1, sink=sink) as first:
        with span("intake"):
            pass
    with start_trace("session-5", 2, sink=sink) as second:
        with span("intake"):
            pass

    assert first.trace_id != second.trace_id
    assert {record.session_id for record in sink.records} == {"session-5"}
    # 每条 trace 有根 span + 业务 span，所以 turn_id 各出现两次。
    assert {record.turn_id for record in sink.records} == {1, 2}

def test_trace_name_is_stable_across_spans():
    sink = MemorySink()

    with start_trace("session-1", 0, sink=sink, root_name="http.request"):
        with span("api.auth", kind="gate"):
            pass

    assert {record.trace_name for record in sink.records} == {"http.request"}
