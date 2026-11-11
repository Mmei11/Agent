"""Phase B：滚动摘要的触发、合并与降级。"""

from types import SimpleNamespace

from backend.app.agent.intake import intake
from backend.app.agent.state import ConsultationState
from backend.app.memory.summary import (
    SUMMARY_MAX_CHARS,
    fallback_summary,
    rollup_memory,
    should_rollup,
)


def make_turns(count: int) -> list[dict]:
    return [
        {"role": "user" if index % 2 == 0 else "agent", "text": f"第{index}条"}
        for index in range(count)
    ]


def test_should_rollup_only_outside_recent_window():
    assert should_rollup({}, 20) is True
    assert should_rollup({}, 12) is False


def test_rollup_calls_summarizer_and_advances_index():
    turns = make_turns(20)
    calls = {}

    def fake(previous, items):
        calls["previous"] = previous
        calls["turns"] = items
        return "用户头痛两天，偏好下午就诊。"

    memory = rollup_memory(
        {"summary": "旧摘要", "summary_upto_index": 0},
        turns,
        summarizer=fake,
    )

    assert memory["summary"] == "用户头痛两天，偏好下午就诊。"
    assert memory["summary_upto_index"] == 14  # 20 条减去最近 6 条
    assert memory["summary_source"] == "model"
    assert calls["previous"] == "旧摘要"
    assert len(calls["turns"]) == 14


def test_rollup_skips_below_threshold():
    memory = {"summary": "旧摘要"}
    assert rollup_memory(memory, make_turns(10), summarizer=lambda p, t: "新") == memory


def test_rollup_keeps_previous_summary_on_failure():
    def boom(previous, turns):
        raise RuntimeError("model down")

    memory = rollup_memory(
        {"summary": "旧摘要", "summary_upto_index": 0},
        make_turns(20),
        summarizer=boom,
    )

    assert memory["summary"] == "旧摘要"
    assert memory["summary_upto_index"] == 0


def test_rollup_uses_extractive_fallback_without_previous_summary():
    def boom(previous, turns):
        raise RuntimeError("model down")

    memory = rollup_memory({}, make_turns(20), summarizer=boom)

    assert memory["summary"]
    assert memory["summary_upto_index"] == 14
    assert memory["summary_source"] == "fallback"


def test_fallback_summary_is_capped():
    text = fallback_summary([{"role": "user", "text": "a" * 1000}])
    assert len(text) <= SUMMARY_MAX_CHARS


def test_intake_rolls_up_long_conversation(monkeypatch):
    """长对话进入 intake 时触发摘要，并把结果写回 session_memory。"""
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setenv("SUMMARY_ENABLED", "1")
    import backend.app.agent.intake_llm as intake_llm
    import backend.app.memory.summary_llm as summary_llm

    usage = SimpleNamespace(
        model="fake",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
        cost_estimate=0.0,
    )
    monkeypatch.setattr(
        intake_llm, "extract_facts_with_meta", lambda text: ({}, {}, usage)
    )
    monkeypatch.setattr(
        summary_llm,
        "summarize_with_meta",
        lambda previous, turns: ("更早对话摘要", usage),
    )

    state = ConsultationState(
        latest_user_message="继续",
        conversation=[
            {"role": "user" if index % 2 == 0 else "agent", "text": f"第{index}条"}
            for index in range(20)
        ],
    )

    result = intake(state)

    assert result["session_memory"]["summary"] == "更早对话摘要"
    assert result["session_memory"]["summary_upto_index"] == 14
    assert result["trace"][0]["summary_updated"] is True
