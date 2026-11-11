"""图内预路由：未出结果时，普通问题先回答，医疗问题再收集字段。案例均为虚构。"""

from types import SimpleNamespace

import pytest

from backend.app.agent import opening
from backend.app.agent.graph import build_graph
from backend.app.agent.opening import route_general
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError


def make_state(
    message: str, facts: dict | None = None, turn_id: int = 2
) -> ConsultationState:
    return ConsultationState(
        latest_user_message=message,
        turn_id=turn_id,
        collected_facts=facts or {},
        missing_fields=["主要症状", "持续时间"],
    )


def fake_usage():
    return SimpleNamespace(
        model="fake",
        input_tokens=5,
        output_tokens=5,
        latency_ms=1,
        cost_estimate=0.0,
    )


def fake_general(message):
    return (
        SimpleNamespace(
            intent="general",
            answer="早上好呀！我是 Consult/Lab 的模拟问诊助手。",
        ),
        fake_usage(),
    )


def test_general_route_without_facts_adds_invitation(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(opening, "route_reply", fake_general)

    result = route_general(make_state("早上好", turn_id=1))

    text = result["conversation"][0]["text"]
    assert result["next_action"] == "GENERAL"
    assert text.startswith("早上好呀")
    assert "身体状态" in text
    assert result["suggested_replies"] == ["头部不适", "腹痛", "发热"]
    assert result["trace"][0]["opening"] is True


def test_general_route_later_turn_has_no_invitation(monkeypatch):
    """后续 general 回复只回答本身，不再重复邀请和快捷选项。"""
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(opening, "route_reply", fake_general)

    result = route_general(make_state("今天感觉状态不错", turn_id=2))

    text = result["conversation"][0]["text"]
    assert result["next_action"] == "GENERAL"
    assert "身体状态" not in text
    assert result["suggested_replies"] == []
    assert result["trace"][0]["opening"] is False


def test_general_route_with_facts_has_no_invitation(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(opening, "route_reply", fake_general)

    result = route_general(make_state("今天心情不好", facts={"主要症状": ["头痛"]}))

    text = result["conversation"][0]["text"]
    assert result["next_action"] == "GENERAL"
    assert "身体状态" not in text
    assert result["suggested_replies"] == []
    assert result["trace"][0]["opening"] is False


def test_general_route_medical_returns_to_decision(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_medical(message):
        return SimpleNamespace(intent="medical", answer=""), fake_usage()

    monkeypatch.setattr(opening, "route_reply", fake_medical)

    result = route_general(make_state("我该怎么办"))

    assert result == {"next_action": "MEDICAL"}


def test_general_route_failure_defaults_medical(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def boom(message):
        raise LLMError("down", retryable=True)

    monkeypatch.setattr(opening, "route_reply", boom)

    assert route_general(make_state("我该怎么办")) == {"next_action": "MEDICAL"}


def test_general_route_model_off_skips_router(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "0")

    def boom(message):
        raise AssertionError("模型关闭时不应该调用路由")

    monkeypatch.setattr(opening, "route_reply", boom)

    assert route_general(make_state("早上好")) == {"next_action": "MEDICAL"}


def test_graph_general_route_answers_without_field_question(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(opening, "route_reply", fake_general)

    app = build_graph()
    result = app.invoke({"latest_user_message": "早上好"})

    assert result["next_action"] == "GENERAL"
    assert result["final_result"] == {}
    assert "身体状态" in result["conversation"][-1]["text"]
    assert [item["node"] for item in result["trace"]] == [
        "intake",
        "risk_check",
        "general_route",
    ]


def test_graph_medical_route_still_asks_fields(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "0")

    app = build_graph()
    result = app.invoke({"latest_user_message": "我头痛"})

    assert result["next_action"] == "ASK_FOLLOWUP"
    assert "请问这种情况持续多久了？" in result["conversation"][-1]["text"]
