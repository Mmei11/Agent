"""Phase 7：结果生成后的继续追问。案例均为虚构。"""

from types import SimpleNamespace

import pytest

from backend.app.agent import answer as answer_module
from backend.app.agent.graph import build_graph
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMError
from backend.app.storage.saver import get_saver


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch):
    """默认走确定性回答，避免本机 USE_MODEL=1 影响单测。"""
    monkeypatch.delenv("USE_MODEL", raising=False)


@pytest.fixture(autouse=True)
def _medical_router(monkeypatch):
    """本文件的追问都按医疗处理，避免路由层发起真实模型调用。"""
    monkeypatch.setattr(
        answer_module,
        "route_reply",
        lambda message: (
            SimpleNamespace(intent="medical", answer=""),
            SimpleNamespace(
                model="fake",
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
                cost_estimate=0.0,
            ),
        ),
    )


def make_state(message="那需要注意什么") -> ConsultationState:
    return ConsultationState(
        session_id="answer-session",
        turn_id=2,
        latest_user_message=message,
        turn_kind="followup",
        collected_facts={"主要症状": ["头部不适"], "持续时间": ["两天"]},
        final_result={
            "status": "completed",
            "next_step": "建议就诊方向：神经内科",
            "source_note": "来源：科室分诊通用原则（未核实）",
        },
        conversation=[
            {"role": "user", "text": "头部不适两天"},
            {"role": "agent", "text": "建议就诊方向：神经内科"},
        ],
    )


def fake_usage():
    return SimpleNamespace(
        model="fake",
        input_tokens=10,
        output_tokens=5,
        latency_ms=3,
        cost_estimate=0.0,
    )


def test_followup_after_result_keeps_result(tmp_path):
    """出结果后继续发送：结果不变，回答追加到对话里，不再走决策/工具。"""
    with get_saver(str(tmp_path / "answer.sqlite")) as saver:
        app = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "answer"}}

        first = app.invoke(
            {"latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史"},
            config=config,
        )
        assert first["final_result"]["status"] == "completed"

        second = app.invoke({"latest_user_message": "那需要注意什么"}, config=config)

        assert second["final_result"]["status"] == "completed"
        assert second["turn_kind"] == "followup"
        assert len(second["conversation"]) == 4
        assert "神经内科" in second["rendered_text"]

        nodes = [entry["node"] for entry in second["trace"]]
        assert nodes[-3:] == ["intake", "risk_check", "answer"]


def test_answer_uses_model(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_generate(state):
        return (
            SimpleNamespace(
                answer="一般不需要空腹，按就诊医院的要求准备即可，如需确认请咨询医院。",
                grounded=False,
            ),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "generate_with_meta", fake_generate)

    result = answer_module.answer_followup(make_state())

    assert "不需要空腹" in result["rendered_text"]
    assert "一般健康信息" in result["rendered_text"]
    assert "来源" not in result["rendered_text"]
    assert result["trace"][0]["source"] == "model"
    assert "final_result" not in result  # 不覆盖已有结构化结果


def test_grounded_answer_uses_result_basis(monkeypatch):
    """真的用到分诊结果时，才带“依据”；不展示未核实的出处。"""
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_generate(state):
        return (
            SimpleNamespace(
                answer="神经内科确实是这类情况的常见首诊科室，具体可由医生面诊判断。",
                grounded=True,
            ),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "generate_with_meta", fake_generate)

    result = answer_module.answer_followup(make_state())

    assert "依据：本轮分诊结果" in result["rendered_text"]
    assert "未核实" not in result["rendered_text"]
    assert result["trace"][0]["grounded"] is True


def test_answer_rewrites_differential_lists(monkeypatch):
    """列举疾病/鉴别诊断时整段改写，只保留就医方向。"""
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_generate(state):
        return (
            SimpleNamespace(
                answer="神经内科是常见首诊科室，用于排查如偏头痛、紧张性头痛、颅内压变化等可能原因。",
                grounded=True,
            ),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "generate_with_meta", fake_generate)

    result = answer_module.answer_followup(make_state())

    assert "偏头痛" not in result["rendered_text"]
    assert "具体原因需要医生面诊判断" in result["rendered_text"]
    assert result["trace"][0]["differential_replaced"] is True


def test_answer_reports_service_unavailable_on_model_error(monkeypatch):
    """模型连不上时不伪装成正常回答，明确提示可以重试。"""
    monkeypatch.setenv("USE_MODEL", "1")

    def boom(state):
        raise LLMError("down", retryable=True)

    monkeypatch.setattr(answer_module, "generate_with_meta", boom)

    result = answer_module.answer_followup(make_state())

    assert result["trace"][0]["source"] == "fallback"
    assert result["trace"][0]["service_unavailable"] is True
    assert "服务暂时不可用" in result["rendered_text"]
    assert "神经内科" not in result["rendered_text"]


def test_answer_replaces_unsafe_model_output(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")

    def unsafe(state):
        return (
            SimpleNamespace(answer="建议服用布洛芬 200mg，一天三次。", grounded=True),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "generate_with_meta", unsafe)

    result = answer_module.answer_followup(make_state())

    assert "布洛芬" not in result["rendered_text"]
    assert "具体处置建议" in result["rendered_text"]
    assert "神经内科" in result["rendered_text"]
    assert result["trace"][0]["output_passed"] is False
    assert result["trace"][0]["output_violations"]


def test_answer_replaces_guardrail_violation(monkeypatch):
    """规则边界没拦住、但 guardrails 判定为用药建议时，同样给安全退路。"""
    monkeypatch.setenv("USE_MODEL", "1")

    def unsafe(state):
        return (
            SimpleNamespace(answer="可以服用布洛芬来缓解。", grounded=True),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "generate_with_meta", unsafe)

    result = answer_module.answer_followup(make_state())

    assert "布洛芬" not in result["rendered_text"]
    assert "具体处置建议" in result["rendered_text"]
    assert result["trace"][0]["output_passed"] is False
    assert result["trace"][0]["output_violations"]


def test_answer_allows_low_risk_lifestyle_advice(monkeypatch):
    """低风险生活建议允许通过；只有药物/诊断/治疗操作才拦截。"""
    monkeypatch.setenv("USE_MODEL", "1")

    def safe(state):
        return (
            SimpleNamespace(
                answer=(
                    "可以先休息、适量补水、避开强光和噪音，并记录症状变化。"
                    "这些不是治疗方案，具体处理仍需医生面诊判断。"
                ),
                grounded=True,
            ),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "generate_with_meta", safe)

    result = answer_module.answer_followup(make_state())

    assert "休息" in result["rendered_text"]
    assert "补水" in result["rendered_text"]
    assert result["trace"][0]["output_passed"] is True
    assert result["trace"][0]["output_violations"] == []


def test_memory_reference_skips_router(monkeypatch):
    """记忆指代类追问不走单路由，直接进入带上下文的回答节点。"""
    monkeypatch.setenv("USE_MODEL", "1")

    def fail_router(message):
        raise AssertionError("memory reference should not go through router")

    monkeypatch.setattr(answer_module, "route_reply", fail_router)

    def fake_generate(state):
        return (
            SimpleNamespace(answer="你刚才说更喜欢下午去。", grounded=False),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "generate_with_meta", fake_generate)

    result = answer_module.answer_followup(make_state("我刚才说更喜欢什么时候去？"))

    assert "下午" in result["rendered_text"]
    assert result["trace"][0]["source"] == "model"
    assert result["trace"][0]["router_intent"] == "memory"
    assert result["trace"][0]["memory_reference"] is True


def test_memory_reference_takes_precedence_over_date(monkeypatch):
    """“我刚才说几月几号要去”问的是对话，不是今天的日期。"""
    monkeypatch.setenv("USE_MODEL", "1")

    def fake_generate(state):
        return (
            SimpleNamespace(answer="你刚才说下周三要去。", grounded=False),
            fake_usage(),
        )

    monkeypatch.setattr(answer_module, "generate_with_meta", fake_generate)

    result = answer_module.answer_followup(make_state("我刚才说几月几号要去？"))

    assert result["trace"][0]["memory_reference"] is True
    assert result["trace"][0]["source"] == "model"
    assert "下周三" in result["rendered_text"]


def test_memory_answer_uses_conversation_note():
    """记忆类回答不套“一般健康信息”的尾巴，改说明来自本轮对话。"""
    text = answer_module._with_context(
        "你刚才说下周要出差。",
        make_state().final_result,
        False,
        memory_reference=True,
    )

    assert "以上内容来自本轮对话记录" in text
    assert "一般健康信息" not in text


def test_graph_stores_non_fact_detail_in_memory(tmp_path):
    """没进结构化字段的第一人称细节，要进入会话记忆卡。"""
    with get_saver(str(tmp_path / "memory.sqlite")) as saver:
        app = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "memory"}}

        app.invoke(
            {"latest_user_message": "头部不适两天，前额，程度轻，伴有恶心，无病史"},
            config=config,
        )
        result = app.invoke({"latest_user_message": "我下周要出差"}, config=config)

        details = result["session_memory"]["details"]
        assert any("出差" in item["text"] for item in details)

        context = answer_module.build_context(ConsultationState(**result))
        assert any(
            "出差" in item["text"] for item in context["session_memory"]["details"]
        )


def test_blocked_answer_is_safe():
    """兜底文案本身要能通过输出层校验。"""
    from backend.app.safety.output_rail import check

    text = answer_module.blocked_answer(make_state().final_result)
    assert check(text, uses_knowledge=True).passed


def test_followup_after_risk_keeps_risk_result(tmp_path):
    with get_saver(str(tmp_path / "risk-answer.sqlite")) as saver:
        app = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "risk-answer"}}

        first = app.invoke({"latest_user_message": "模拟人物突然呼吸困难"}, config=config)
        assert first["final_result"]["status"] == "risk_route"

        second = app.invoke({"latest_user_message": "那现在该怎么办"}, config=config)

        assert second["final_result"]["status"] == "risk_route"
        assert second["turn_kind"] == "followup"
        assert len(second["conversation"]) == 4
