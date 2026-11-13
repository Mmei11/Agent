"""Phase C 第 7 课：red_flag_lookup 的合法场景——红旗科普。

用户主动问“什么情况要急诊”时，可以列出警示；
但结论只能是“出现这些情况请立即就医”，不能是“你没有风险”。
"""

import pytest

from backend.app.agent import opening
from backend.app.agent.general import (
    RED_FLAG_FALLBACK,
    is_red_flag_question,
    red_flag_answer_is_safe,
)
from backend.app.agent.opening import answer_red_flags, route_general
from backend.app.agent.state import ConsultationState
from backend.app.tools.contract import ToolResult


def _state(message: str) -> ConsultationState:
    return ConsultationState(latest_user_message=message)


def _tool_result(hits: list[dict]) -> ToolResult:
    return ToolResult(
        ok=True,
        name="red_flag_lookup",
        data={"flag_ids": [hit["id"] for hit in hits], "uncertain": not hits, "hits": hits},
        latency_ms=3,
    )


def _hit(content: str) -> dict:
    return {
        "id": "rf-demo",
        "title": content[:10],
        "content": content,
        "source": {"id": "src-project-derived", "title": "项目归纳", "verified": False},
    }


@pytest.mark.parametrize(
    "message",
    [
        "出现什么情况需要立即去医院？",
        "有哪些危险信号要马上急诊？",
        "什么情况下要赶紧就医",
        "这算不算危险信号？",
    ],
)
def test_red_flag_questions_are_recognized(message):
    assert is_red_flag_question(message)


@pytest.mark.parametrize(
    "message",
    [
        "头痛两天了",
        "就医前要带什么材料？",
        "今天几号？",
        "我需要挂什么科？",
    ],
)
def test_ordinary_questions_are_not_red_flag(message):
    assert not is_red_flag_question(message)


def test_answer_lists_warning_signs(monkeypatch):
    hits = [_hit("突然剧烈头痛"), _hit("意识不清或昏迷")]

    def fake_call_tool(name, params):
        assert name == "red_flag_lookup"
        return _tool_result(hits)

    monkeypatch.setattr(opening, "call_tool", fake_call_tool)
    state = _state("有哪些危险信号要马上急诊？")

    result = answer_red_flags(state)

    assert "立即就医" in result["rendered_text"]
    assert "突然剧烈头痛" in result["rendered_text"]
    assert "意识不清或昏迷" in result["rendered_text"]
    assert result["tool_results"]["red_flag_lookup"]["hits"] == hits
    assert result["trace"][0]["red_flag_qa"] is True
    assert result["trace"][1]["called"] == "red_flag_lookup"
    # 科普路径不写风险等级：风险分流仍由 rule_engine 更早完成
    assert "risk_flags" not in result


def test_empty_hits_fail_closed(monkeypatch):
    monkeypatch.setattr(opening, "call_tool", lambda name, params: _tool_result([]))

    result = answer_red_flags(_state("有哪些危险信号？"))

    assert "无法确认" in result["rendered_text"]
    assert "及时就医" in result["rendered_text"]
    assert "没有危险" not in result["rendered_text"]


def test_unsafe_content_falls_back(monkeypatch):
    hits = [_hit("建议服用止痛药，每次一片")]

    monkeypatch.setattr(opening, "call_tool", lambda name, params: _tool_result(hits))

    result = answer_red_flags(_state("有哪些危险信号？"))

    assert result["rendered_text"] == RED_FLAG_FALLBACK


def test_red_flag_answer_rejects_reassurance():
    assert not red_flag_answer_is_safe("不用担心，你没有危险。")
    assert red_flag_answer_is_safe("出现意识不清请立即就医。")


def test_route_general_uses_red_flag_path(monkeypatch):
    monkeypatch.setattr(opening, "call_tool", lambda name, params: _tool_result([_hit("呼吸困难")]))

    result = route_general(_state("什么情况下要马上就医？"))

    assert result["next_action"] == "GENERAL"
    assert result["trace"][0]["red_flag_qa"] is True
    assert result["tool_call_count"] == 1
