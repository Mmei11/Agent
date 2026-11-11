"""Phase B：会话记忆卡的筛选、去重与视图裁剪。"""

from backend.app.memory.export import render_markdown
from backend.app.memory.session_memory import (
    DETAIL_MAX_ITEMS,
    memory_view,
    should_store_detail,
    update_memory,
)


def test_stores_first_person_detail_without_facts():
    assert should_store_detail("我更喜欢下午去", {}) is True
    assert should_store_detail("我下周要出差", {}) is True


def test_skips_message_that_produced_facts():
    assert should_store_detail("我头痛两天了", {"主要症状": ["头痛"]}) is False


def test_keeps_detail_hint_even_when_facts_were_extracted():
    """模型可能把“下周”抽成持续时间，但“出差”仍是需要记住的细节。"""
    assert should_store_detail("我下周要出差", {"持续时间": ["下周"]}) is True


def test_skips_memory_reference_question():
    assert should_store_detail("我刚才说什么时候去？", {}) is False


def test_skips_plain_question_without_detail_hint():
    assert should_store_detail("我需要空腹吗", {}) is False


def test_keeps_question_with_detail_hint():
    assert should_store_detail("我下周去可以吗", {}) is True


def test_update_memory_dedupes_and_caps():
    memory = {}
    for index in range(DETAIL_MAX_ITEMS + 3):
        memory = update_memory(
            memory,
            message=f"我喜欢第{index}个时间段",
            new_facts={},
            missing_fields=[],
            turn_id=index + 1,
        )

    details = memory["details"]
    assert len(details) == DETAIL_MAX_ITEMS
    assert details[-1]["text"] == f"我喜欢第{DETAIL_MAX_ITEMS + 2}个时间段"
    assert memory["recent_topic"] == details[-1]["text"]


def test_update_memory_refreshes_open_questions():
    memory = update_memory(
        {}, message="我下周要出差", new_facts={}, missing_fields=["部位"], turn_id=2
    )
    assert memory["open_questions"] == ["部位"]
    assert memory["turns"] == 2


def test_memory_view_keeps_recent_within_budget():
    memory = {
        "details": [
            {"text": "第一条", "turn": 1},
            {"text": "第二条", "turn": 2},
            {"text": "第三条", "turn": 3},
        ],
        "open_questions": ["部位"],
        "recent_topic": "第三条",
    }

    view = memory_view(memory, max_items=2, max_chars=100)

    assert [item["text"] for item in view["details"]] == ["第二条", "第三条"]
    assert view["open_questions"] == ["部位"]


def test_memory_view_respects_char_budget():
    memory = {
        "details": [
            {"text": "a" * 10, "turn": 1},
            {"text": "b" * 10, "turn": 2},
        ],
        "open_questions": [],
        "recent_topic": "",
    }

    view = memory_view(memory, max_items=8, max_chars=15)

    assert [item["text"] for item in view["details"]] == ["b" * 10]


def test_render_markdown_includes_turn_and_open_questions():
    text = render_markdown(
        "session-1",
        {
            "details": [{"text": "我下周要出差", "turn": 2}],
            "open_questions": ["部位"],
            "recent_topic": "我下周要出差",
        },
        turn_id=3,
    )

    assert "[第 2 轮] 我下周要出差" in text
    assert "- 部位" in text
    assert "最近话题" in text
