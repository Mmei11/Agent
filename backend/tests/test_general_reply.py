"""普通问题：日期确定性回答 + 单路由。案例均为虚构。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.app.agent import answer as answer_module
from backend.app.agent.router import build_router_prompt
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError


def make_state(message: str, status: str = "completed") -> ConsultationState:
    return ConsultationState(
        session_id="general-session",
        turn_id=2,
        latest_user_message=message,
        turn_kind="followup",
        collected_facts={"主要症状": ["头痛"]},
        final_result={
            "status": status,
            "next_step": "建议就诊方向：神经内科",
            "source_note": "来源：科室分诊通用原则（未核实）",
        },
        conversation=[
            {"role": "user", "text": "头痛两天"},
            {"role": "agent", "text": "建议就诊方向：神经内科"},
        ],
    )


def fake_usage():
    return SimpleNamespace(
        model="fake",
        input_tokens=5,
        output_tokens=5,
        latency_ms=1,
        cost_estimate=0.0,
    )


def test_date_question_uses_deterministic_answer(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def boom(message):
        raise AssertionError("日期问题不应该路由或调用模型")

    monkeypatch.setattr(answer_module, "route_reply", boom)

    result = answer_module.answer_followup(make_state("今天是什么日子呀？几月几号"))

    assert result["trace"][0]["source"] == "general"
    assert result["trace"][0]["general_answer_source"] == "deterministic"
    assert "今天是" in result["rendered_text"]
    assert "星期" in result["rendered_text"]


def test_router_general_answers(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_route(message):
        return (
            SimpleNamespace(
                intent="general",
                answer="你好呀！我是 Consult/Lab 的模拟问诊助手，可以帮你整理就医方向。",
            ),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "route_reply", fake_route)

    result = answer_module.answer_followup(make_state("你好呀，你是谁？"))

    assert result["trace"][0]["source"] == "general"
    assert result["trace"][0]["general_answer_source"] == "router"
    assert "模拟问诊助手" in result["rendered_text"]
    assert "final_result" not in result


def test_router_rejects_unsafe_answer(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_route(message):
        return (
            SimpleNamespace(
                intent="general",
                answer="建议服用布洛芬 200mg，一天三次。",
            ),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "route_reply", fake_route)

    result = answer_module.answer_followup(make_state("你好呀，你是谁？"))

    assert "布洛芬" not in result["rendered_text"]
    assert result["trace"][0]["general_answer_source"] == "fallback"


def test_router_medical_falls_through(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_route(message):
        return SimpleNamespace(intent="medical", answer=""), fake_usage()

    def boom(state):
        raise LLMError("down", retryable=True)

    monkeypatch.setattr(answer_module, "route_reply", fake_route)
    monkeypatch.setattr(answer_module, "generate_with_meta", boom)

    result = answer_module.answer_followup(make_state("我该怎么办"))

    assert result["trace"][0]["router_intent"] == "medical"
    assert result["trace"][0]["source"] != "general"


def test_router_failure_defaults_to_medical(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def boom_route(message):
        raise LLMError("down", retryable=True)

    def boom_answer(state):
        raise LLMError("down", retryable=True)

    monkeypatch.setattr(answer_module, "route_reply", boom_route)
    monkeypatch.setattr(answer_module, "generate_with_meta", boom_answer)

    result = answer_module.answer_followup(make_state("我该怎么办"))

    assert result["trace"][0]["router_intent"] == "medical"
    assert result["trace"][0]["source"] != "general"


def test_router_prompt_includes_current_date():
    now = datetime.now(timezone(timedelta(hours=8)))
    prompt = build_router_prompt()

    assert now.strftime("%Y-%m-%d") in prompt
    assert "不要使用训练数据里的日期" in prompt
