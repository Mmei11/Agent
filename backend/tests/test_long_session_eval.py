"""Phase B：长会话评测的探针判定。"""

from eval.long_session import answer_of, evaluate_probe


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_answer_of_prefers_rendered_text():
    assert answer_of(FakeResponse({"rendered_text": "直接回答"})) == "直接回答"


def test_answer_of_falls_back_to_last_agent_message():
    data = {
        "conversation": [
            {"role": "user", "text": "问"},
            {"role": "agent", "text": "答"},
        ]
    }
    assert answer_of(FakeResponse(data)) == "答"


def test_probe_hits_summary_layer():
    state = {
        "session_memory": {"summary": "用户下周三要出差", "details": []},
        "collected_facts": {},
        "conversation": [],
    }

    result = evaluate_probe(["下周三"], "你之前说下周三要出差", state)

    assert result["answer_hit"] is True
    assert result["summary_hit"] is True
    assert result["details_hit"] is False


def test_probe_records_details_source():
    state = {
        "session_memory": {"summary": "", "details": [{"text": "我更喜欢下午去"}]},
        "collected_facts": {},
        "conversation": [],
    }

    result = evaluate_probe(["下午"], "你说更喜欢下午去", state)

    assert result["answer_hit"] is True
    assert result["details_hit"] is True
    assert result["summary_hit"] is False


def test_probe_miss_is_recorded():
    state = {"session_memory": {}, "collected_facts": {}, "conversation": []}

    result = evaluate_probe(["下周三"], "没有记录", state)

    assert result["answer_hit"] is False
    assert result["summary_hit"] is False
