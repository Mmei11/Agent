"""Day 7 组件测试：追问字段选择、问题生成、去重、优先级。案例均为虚构。"""

from types import SimpleNamespace

from backend.app.agent.followup import (
    clean_replies,
    followup,
    generate_question,
    select_next_field,
    suggest_replies,
)
from backend.app.agent.state import ConsultationState
from backend.app.agent.graph import build_graph
from backend.app.llm.client import LLMError
from backend.app.storage.saver import get_saver


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


def test_followup_dedup_in_graph(tmp_path):
    """跨三轮追问，每轮一个问题且不重复。"""
    with get_saver(str(tmp_path / "followups.sqlite")) as saver:
        app = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "followups"}}
        for _ in range(3):
            result = app.invoke({"latest_user_message": "头部不适"}, config=config)
        questions = result["asked_questions"]
        assert len(questions) == 3
        assert len(set(questions)) == 3
        assert result["final_result"] == {}
        completed = app.invoke({"latest_user_message": "暂时说不清"}, config=config)
        assert completed["final_result"]["status"] == "completed"


def test_clean_replies_filters_what_user_already_said():
    """已经说了"头部不适"，部位选项里的"头部"就不要再出现。"""
    state = ConsultationState(collected_facts={"主要症状": ["头部不适"]})
    assert clean_replies(["头部", "前额", "腹部"], state) == ["前额", "腹部"]


def test_clean_replies_normalizes_and_caps():
    """去标点、去重、最多保留三条。"""
    state = ConsultationState()
    candidates = [" 前额。", "前额", "左边", "右边", "后脑"]
    assert clean_replies(candidates, state) == ["前额", "左边", "右边"]


def test_clean_replies_drops_overlong_options():
    state = ConsultationState()
    assert clean_replies(["这个选项明显超过十二个汉字的上限", "前额"], state) == ["前额"]


def test_suggest_replies_uses_model(monkeypatch):
    """模型模式下用生成的候选，而不是静态选项。"""
    from backend.app.agent import followup_llm

    monkeypatch.setenv("USE_MODEL", "1")
    seen = {}

    def fake_generate(state, field, question):
        seen["field"] = field
        return (
            SimpleNamespace(replies=["头部", "前额", "后脑", "两侧太阳穴"]),
            SimpleNamespace(
                model="fake",
                input_tokens=10,
                output_tokens=5,
                latency_ms=3,
                cost_estimate=0.0,
            ),
        )

    monkeypatch.setattr(followup_llm, "generate_with_meta", fake_generate)
    state = ConsultationState(collected_facts={"主要症状": ["头部不适"]})

    replies, meta = suggest_replies(state, "部位", "请问具体在哪个部位？")

    assert seen["field"] == "部位"
    assert meta["source"] == "model"
    assert replies == ["前额", "后脑", "两侧太阳穴"]


def test_suggest_replies_falls_back_on_model_error(monkeypatch):
    """模型失败时回退静态选项，并同样过滤已说过的内容。"""
    from backend.app.agent import followup_llm

    monkeypatch.setenv("USE_MODEL", "1")

    def boom(state, field, question):
        raise LLMError("down", retryable=True)

    monkeypatch.setattr(followup_llm, "generate_with_meta", boom)
    state = ConsultationState(collected_facts={"主要症状": ["头部不适"]})

    replies, meta = suggest_replies(state, "部位", "请问具体在哪个部位？")

    assert meta["source"] == "fallback"
    assert replies == ["腹部", "胸部"]


def test_followup_node_uses_generated_replies(monkeypatch):
    from backend.app.agent import followup_llm

    monkeypatch.setenv("USE_MODEL", "1")

    def fake_generate(state, field, question):
        return (
            SimpleNamespace(replies=["前额", "后脑", "两侧太阳穴"]),
            SimpleNamespace(
                model="fake",
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                cost_estimate=0.0,
            ),
        )

    monkeypatch.setattr(followup_llm, "generate_with_meta", fake_generate)
    state = ConsultationState(
        latest_user_message="头部不适",
        collected_facts={"主要症状": ["头部不适"]},
        missing_fields=["部位"],
    )

    result = followup(state)

    assert result["suggested_replies"] == ["前额", "后脑", "两侧太阳穴"]
    assert result["trace"][0]["reply_meta"]["source"] == "model"
