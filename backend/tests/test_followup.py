"""Day 7 组件测试：追问字段选择、问题生成、去重、优先级。案例均为虚构。"""

from backend.app.agent.followup import generate_question, select_next_field
from backend.app.agent.state import ConsultationState
from backend.app.agent.graph import build_graph


def test_select_by_priority():
    """多个缺失字段时，选优先级最高的。"""
    # 持续时间(2) vs 程度(5)，应选持续时间
    field = select_next_field(["持续时间", "程度"], [])
    assert field == "持续时间"


def test_skip_asked_field():
    """已问过的字段跳过，选下一个。"""
    # 持续时间已问过，选伴随表现(4)
    asked = ["请问这种情况持续多久了？"]
    field = select_next_field(["持续时间", "伴随表现"], asked)
    assert field == "伴随表现"


def test_all_asked_returns_none():
    """所有缺失字段都问过了，返回 None。"""
    asked = [
        "请问这种情况持续多久了？",
        "请问还有其他不适吗？",
        "请问不适的程度如何，是轻微、明显还是严重？",
    ]
    field = select_next_field(["持续时间", "伴随表现", "程度"], asked)
    assert field is None


def test_generate_question_for_known_field():
    """已知字段生成对应问题。"""
    q = generate_question("部位")
    assert "部位" in q


def test_generate_question_for_none():
    """没有字段可问时生成收束提示。"""
    q = generate_question(None)
    assert "整理结果" in q


def test_followup_dedup_in_graph():
    """图里追问三次不重复问同一字段。"""
    app = build_graph()
    result = app.invoke({"latest_user_message": "头部不适"})
    questions = result.get("asked_questions", [])
    assert len(questions) == 3
    # 三个问题各不相同
    assert len(set(questions)) == 3
