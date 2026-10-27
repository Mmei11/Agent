"""Phase 2 第二课：图节点与模型调用的埋点行为。案例均为虚构。"""

from types import SimpleNamespace

import pytest

from backend.app.agent.graph import build_graph
from backend.app.llm.client import LLMError, chat
from backend.app.observability import MemorySink, start_trace


def test_graph_nodes_emit_spans_and_link_trace_id():
    sink = MemorySink()
    app = build_graph()

    with start_trace("sess-graph", 1, sink=sink) as trace:
        result = app.invoke(
            {
                "latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史",
                "session_id": "sess-graph",
            }
        )

    assert [record.name for record in sink.records] == [
        "intake",
        "risk_check",
        "decision",
        "tool",
        "decision",
        "output",
    ]
    assert all(record.kind == "node" for record in sink.records)
    assert all(record.session_id == "sess-graph" for record in sink.records)
    # mock 模式没有模型调用，所有节点都是顶层 span。
    assert all(record.parent_span_id is None for record in sink.records)
    assert result["trace_id"] == trace.trace_id
    assert all(entry["trace_id"] == trace.trace_id for entry in result["trace"])


def test_state_trace_keeps_only_summaries():
    app = build_graph()

    with start_trace("sess-summary", 1, sink=MemorySink()):
        result = app.invoke(
            {
                "latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史",
                "session_id": "sess-summary",
            }
        )

    intake_entry = result["trace"][0]
    tool_entry = next(entry for entry in result["trace"] if entry["node"] == "tool")

    # 完整字段值在 collected_facts，工具结果在 tool_results，不再复制进 trace。
    assert "extracted" not in intake_entry
    assert "missing" not in intake_entry
    assert "result" not in tool_entry
    assert tool_entry["called"] == "department_suggest"
    assert tool_entry["ok"] is True


def test_chat_span_records_gen_ai_attributes(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_MODEL", raising=False)

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                model="qwen-plus",
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3),
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    sink = MemorySink()

    with start_trace("sess-llm", 1, sink=sink):
        result = chat(
            [{"role": "user", "content": "hello"}],
            client=fake_client,
            prompt_name="demo.prompt",
            prompt_version="v3",
        )

    record = sink.records[0]
    assert record.name == "llm.chat"
    assert record.kind == "llm"
    assert record.attributes["gen_ai.request.model"] == "qwen-plus"
    assert record.attributes["gen_ai.usage.input_tokens"] == 11
    assert record.attributes["gen_ai.usage.output_tokens"] == 3
    assert record.attributes["prompt.name"] == "demo.prompt"
    assert record.attributes["prompt.version"] == "v3"
    assert result.content == "ok"


def test_llm_failure_marks_span_error_and_keeps_retryable():
    class ServerError(Exception):
        status_code = 500

    class BrokenCompletions:
        def create(self, **kwargs):
            raise ServerError("upstream down")

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=BrokenCompletions()))
    sink = MemorySink()

    with pytest.raises(LLMError):
        with start_trace("sess-error", 1, sink=sink):
            chat([{"role": "user", "content": "hello"}], client=fake_client)

    record = sink.records[0]
    assert record.status == "error"
    assert record.error == "LLMError"
    assert record.attributes["error.retryable"] is True