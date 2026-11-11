"""Phase B：上下文装配与预算裁剪。"""

from backend.app.agent.state import ConsultationState
from backend.app.context.assembler import (
    assemble_answer_context,
    assemble_reference_context,
    trim_conversation,
)


def test_trim_conversation_keeps_recent_items():
    items = [{"role": "user", "text": f"第{i}条"} for i in range(10)]

    kept = trim_conversation(items, max_items=3, max_chars=1000)

    assert [item["text"] for item in kept] == ["第7条", "第8条", "第9条"]


def test_trim_conversation_respects_char_budget():
    items = [{"role": "user", "text": "a" * 10} for _ in range(5)]

    kept = trim_conversation(items, max_items=5, max_chars=25)

    assert len(kept) == 2


def test_assemble_answer_context_includes_session_memory():
    state = ConsultationState(
        latest_user_message="我刚才说什么时候去？",
        session_memory={
            "details": [{"text": "我更喜欢下午去", "turn": 2}],
            "open_questions": [],
            "recent_topic": "我更喜欢下午去",
        },
    )

    context = assemble_answer_context(state)

    assert context["question"] == "我刚才说什么时候去？"
    assert context["session_memory"]["details"][0]["text"] == "我更喜欢下午去"
    assert "recent_conversation" in context


def test_assemble_reference_context_includes_memory_and_conversation():
    state = ConsultationState(
        conversation=[{"role": "user", "text": "我下周要出差"}],
        session_memory={
            "details": [{"text": "我下周要出差", "turn": 2}],
            "open_questions": [],
        },
    )

    context = assemble_reference_context(state)

    assert context["session_memory"]["details"][0]["text"] == "我下周要出差"
    assert context["recent_conversation"][0]["text"] == "我下周要出差"
