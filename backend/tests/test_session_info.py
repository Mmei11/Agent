"""用户询问本次会话已记录信息时的确定性回答。案例均为虚构。"""

import pytest

from backend.app.agent import answer as answer_module
from backend.app.agent.session_info import build_answer, is_session_info_query
from backend.app.agent.state import ConsultationState


def make_state(message: str, status: str = "risk_route") -> ConsultationState:
    return ConsultationState(
        session_id="session-info",
        turn_id=2,
        latest_user_message=message,
        turn_kind="followup",
        collected_facts={
            "主要症状": ["头痛", "爆痛", "呼吸停止"],
            "持续时间": ["三天了"],
            "部位": ["前额", "心脏"],
            "程度": ["轻微", "爆痛"],
            "伴随表现": ["恶心"],
            "已知背景": ["无病史"],
            "加重缓解因素": ["走路时头痛加重，休息后缓解"],
            "时间模式": ["每天晚上更明显"],
            "测量值": ["三十八度五"],
        },
        missing_fields=[],
        risk_flags=["R001"] if status == "risk_route" else [],
        final_result={
            "status": status,
            "next_step": "请立即联系急救或前往最近医院",
            "risk_message": "检测到预设风险场景（规则 R001），请立即寻求专业帮助。"
            if status == "risk_route"
            else None,
        },
        conversation=[
            {"role": "user", "text": "我心脏现在爆痛"},
            {"role": "agent", "text": "风险提示"},
        ],
    )


@pytest.mark.parametrize(
    "message",
    [
        "你知晓我哪些信息呢？",
        "我现在症状有哪些呢？帮我列举一下",
        "我有哪些症状",
        "帮我列举一下我的症状",
        "尚缺哪些信息",
        "我的信息",
    ],
)
def test_session_info_detection(message):
    assert is_session_info_query(message)


@pytest.mark.parametrize(
    "message",
    [
        "我头疼有哪些症状",
        "我的症状严重吗",
        "我头疼吃什么药",
        "你好呀，你是谁",
    ],
)
def test_non_session_info_messages(message):
    assert not is_session_info_query(message)


def test_session_info_answer_lists_facts():
    text = build_answer(make_state("你知晓我哪些信息呢？"))

    assert "我目前记录的问诊信息如下" in text
    assert "- 主要症状：头痛、爆痛、呼吸停止" in text
    assert "- 部位：前额、心脏" in text
    assert "尚待确认：无" in text
    assert "急症风险" in text
    assert "具体处置建议" not in text
    assert "无法给出" not in text


def test_completed_session_has_no_risk_note():
    text = build_answer(make_state("你知晓我哪些信息呢？", status="completed"))

    assert "急症风险" not in text
    assert "尽快就医" not in text


@pytest.mark.parametrize(
    "message",
    ["你知晓我哪些信息呢？", "我现在症状有哪些呢？帮我列举一下"],
)
def test_answer_node_uses_session_info_without_model(monkeypatch, message):
    monkeypatch.setenv("USE_MODEL", "1")

    def boom(*args, **kwargs):
        raise AssertionError("会话信息查询不应该调用模型")

    monkeypatch.setattr(answer_module, "generate_with_meta", boom)
    monkeypatch.setattr(answer_module, "generate_drug_answer", boom)

    result = answer_module.answer_followup(make_state(message))

    assert result["trace"][0]["source"] == "session_info"
    assert "主要症状：头痛、爆痛、呼吸停止" in result["rendered_text"]
    assert "具体处置建议" not in result["rendered_text"]
    assert "final_result" not in result
