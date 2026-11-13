"""Phase 1 第三课第一站：证据留存与决策上下文。不联网、不花钱。"""

from types import SimpleNamespace

from backend.app.agent.decision_llm import build_context
from backend.app.agent.intake import intake
from backend.app.agent.intake_llm import to_evidence_dict
from backend.app.agent.state import ConsultationState
from backend.app.llm.schemas import FIELD_MAP, Fact, IntakeFacts


def make_facts(**fields) -> IntakeFacts:
    data = {name: [] for name in FIELD_MAP}
    data.update(fields)
    return IntakeFacts(**data)


def test_evidence_dict_keeps_evidence_text():
    """证据按中文字段名归档，存的是原文片段，不是 value。"""
    facts = make_facts(
        chief_complaint=[Fact(value="胀痛", evidence="一阵一阵胀痛", confidence=0.9)],
        location=[Fact(value="前额", evidence="前额", confidence=0.8)],
    )

    assert to_evidence_dict(facts) == {
        "主要症状": ["一阵一阵胀痛"],
        "部位": ["前额"],
    }


def test_evidence_dict_skips_empty_evidence():
    """空证据不写进结果，避免下游拿到空字符串。"""
    facts = make_facts(
        chief_complaint=[Fact(value="头痛", evidence="  ", confidence=0.5)]
    )

    assert to_evidence_dict(facts) == {}


def test_intake_merges_evidence_across_turns(monkeypatch):
    """证据和字段值一样跨轮合并，重复证据只留一份。"""
    monkeypatch.setenv("USE_MODEL", "1")
    import backend.app.agent.intake_llm as intake_llm

    usage = SimpleNamespace(
        model="qwen-plus",
        input_tokens=10,
        output_tokens=5,
        latency_ms=120,
        cost_estimate=None,
    )
    monkeypatch.setattr(
        intake_llm,
        "extract_facts_with_meta",
        lambda text, **kwargs: (
            {"主要症状": ["胀痛"]},
            {"主要症状": ["一阵一阵胀痛", "旧证据"]},
            usage,
        ),
    )

    state = ConsultationState(
        latest_user_message="前额一阵一阵胀痛",
        fact_evidence={"主要症状": ["旧证据"]},
    )
    result = intake(state)

    assert result["fact_evidence"]["主要症状"] == ["旧证据", "一阵一阵胀痛"]


def test_context_carries_what_the_model_needs():
    """决策上下文必须带上用户原话、字段、证据、工具结果和计数。"""
    state = ConsultationState(
        latest_user_message="前天傍晚开始的，前额一阵一阵胀痛",
        collected_facts={"主要症状": ["胀痛"], "部位": ["前额"]},
        fact_evidence={"主要症状": ["一阵一阵胀痛"], "部位": ["前额"]},
        asked_questions=["请问主要的不适是什么？", "请问这种情况持续多久了？"],
        tool_results={"department_route": {"department": "神经内科"}},
        followup_count=2,
    )

    context = build_context(state, max_followups=3)

    assert context["latest_user_message"] == "前天傍晚开始的，前额一阵一阵胀痛"
    assert context["last_question"] == "请问这种情况持续多久了？"
    assert context["collected_facts"]["部位"] == ["前额"]
    assert context["fact_evidence"]["部位"] == ["前额"]
    # 工具结果进 prompt 前被摘要成字符串，原始结果仍在 state 里
    assert "神经内科" in context["tool_results"]["department_route"]
    assert context["followup_count"] == 2
    assert context["max_followups"] == 3


def test_context_has_no_last_question_on_first_turn():
    """第一轮还没有问句时，last_question 是 None，不是空字符串。"""
    context = build_context(
        ConsultationState(latest_user_message="头部不适"), max_followups=3
    )

    assert context["last_question"] is None


def test_context_carries_memory_and_recent_conversation():
    """决策上下文带上记忆卡与最近对话，帮助理解“刚才说的那个”。"""
    state = ConsultationState(
        latest_user_message="就按刚才说的时间",
        session_memory={
            "details": [{"text": "我下周要出差", "turn": 2}],
            "open_questions": [],
        },
        conversation=[
            {"role": "user", "text": "我下周要出差"},
            {"role": "agent", "text": "好的"},
        ],
    )

    context = build_context(state, max_followups=3)

    assert context["session_memory"]["details"][0]["text"] == "我下周要出差"
    assert context["recent_conversation"][0]["text"] == "我下周要出差"


def test_context_never_exposes_risk_flags():
    """风险标记不会到达决策节点，传进去只会误导模型。"""
    state = ConsultationState(
        latest_user_message="头部不适",
        risk_flags=["R001"],
    )

    context = build_context(state, max_followups=3)

    assert "risk_flags" not in context