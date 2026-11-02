"""Phase 5 L2 审计测试：落盘字段、PII 脱敏、写盘失败隔离、trace span。"""

import json

from backend.app.observability import MemorySink, start_trace
from backend.app.safety.audit import AuditEvent, input_digest, write


def _event(text: str = "我今晚胸口很痛") -> AuditEvent:
    return AuditEvent(
        layer="policy",
        rule="P001",
        action="FINALIZE",
        session_id="s1",
        turn_id=2,
        input_digest=input_digest(text),
        detail="命中风险词：胸痛",
    )


def test_write_appends_one_json_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    write(_event(), path=path)
    write(_event(), path=path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[0])
    assert payload["layer"] == "policy"
    assert payload["rule"] == "P001"
    assert payload["action"] == "FINALIZE"
    assert payload["session_id"] == "s1"
    assert payload["turn_id"] == 2
    assert payload["timestamp"]


def test_digest_is_short_and_does_not_store_raw_text(tmp_path):
    text = "我叫张三，手机号13800138000，胸口很痛"
    payload = write(_event(text), path=tmp_path / "audit.jsonl")

    assert len(payload["input_digest"]) == 12
    assert text not in json.dumps(payload, ensure_ascii=False)
    assert "13800138000" not in json.dumps(payload, ensure_ascii=False)


def test_detail_is_redacted(tmp_path):
    event = AuditEvent(
        layer="input",
        rule="PII",
        action="REDACT",
        detail="手机号13800138000",
    )
    payload = write(event, path=tmp_path / "audit.jsonl")
    assert "13800138000" not in payload["detail"]
    assert "[手机号]" in payload["detail"]


def test_write_failure_does_not_raise(tmp_path):
    payload = write(_event(), path=tmp_path)
    assert payload["rule"] == "P001"


def test_write_marks_success_in_span(tmp_path):
    sink = MemorySink()
    with start_trace("s1", 1, sink=sink):
        write(_event(), path=tmp_path / "audit.jsonl")

    record = next(item for item in sink.records if item.name == "safety.escalation")
    assert record.attributes["safety.written"] is True


def test_write_failure_marks_false_in_span(tmp_path):
    sink = MemorySink()
    with start_trace("s1", 1, sink=sink):
        write(_event(), path=tmp_path)

    record = next(item for item in sink.records if item.name == "safety.escalation")
    assert record.attributes["safety.written"] is False
