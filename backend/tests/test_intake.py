"""Day 6 组件测试：结构化提取覆盖完整、缺失、模糊、否定、多轮追加。案例均为虚构。"""

from types import SimpleNamespace

import pytest

from backend.app.agent.intake import (
    compute_missing,
    extract_facts,
    extract_facts_auto,
    intake,
)
from backend.app.agent.state import ConsultationState


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch):
    """默认让本文件跑关键词路径，避免本机 USE_MODEL=1 影响单测。"""
    monkeypatch.delenv("USE_MODEL", raising=False)


def test_complete_extraction():
    """完整输入：五个字段命中，只缺已知背景。"""
    facts = extract_facts("头部不适两天，前额，程度轻，暂无其他不适")
    assert "主要症状" in facts
    assert "持续时间" in facts
    assert "部位" in facts
    assert "程度" in facts
    assert "伴随表现" in facts
    missing = compute_missing(facts)
    assert missing == ["已知背景"]


def test_missing_fields():
    """信息不足：只命中部分字段，缺失字段正确列出。"""
    facts = extract_facts("头部不适")
    assert facts["主要症状"] == ["头部不适"]
    missing = compute_missing(facts)
    assert "持续时间" in missing
    assert "程度" in missing
    assert "伴随表现" in missing
    assert "已知背景" in missing


def test_vague_description():
    """模糊表述：没有明确症状关键词，只有模糊的时间线索。"""
    facts = extract_facts("最近几天觉得不太舒服")
    assert "主要症状" not in facts
    assert "程度" not in facts
    assert "部位" not in facts


def test_negation():
    """否定句：'暂无其他不适' 提取为伴随表现，不当作缺失。"""
    facts = extract_facts("头部不适，暂无其他不适")
    assert "伴随表现" in facts
    assert "暂无" in facts["伴随表现"]


def test_empty_input():
    """空输入：不提取任何字段，全部缺失。"""
    facts = extract_facts("")
    assert facts == {}
    assert len(compute_missing(facts)) == 6


def test_whitespace_only():
    """仅空格：跟空输入一样。"""
    facts = extract_facts("   ")
    assert facts == {}


def test_multi_turn_append():
    """多轮追加：第二轮补充的字段合并进已有 collected_facts。"""
    state = ConsultationState(
        latest_user_message="前两天开始的，在前额",
        collected_facts={"主要症状": ["头部不适"]},
    )
    result = intake(state)
    assert "主要症状" in result["collected_facts"]
    assert "部位" in result["collected_facts"]
    assert "持续时间" in result["collected_facts"]


def test_dedup_same_field():
    """同一字段重复关键词不重复追加。"""
    state = ConsultationState(
        latest_user_message="头部不适",
        collected_facts={"主要症状": ["头部不适"]},
    )
    result = intake(state)
    assert result["collected_facts"]["主要症状"] == ["头部不适"]


def test_auto_uses_keyword_in_mock_mode(monkeypatch):
    """未启用模型时走关键词，trace 标记 keyword。"""
    meta = {}
    facts = extract_facts_auto("头部不适", meta)

    assert facts["主要症状"] == ["头部不适"]
    assert meta == {"extractor": "keyword"}


def test_auto_uses_llm_when_enabled(monkeypatch):
    """USE_MODEL=1 时走模型，并把用量写进 meta。"""
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
        lambda text: ({"主要症状": ["模型结果"]}, usage),
    )

    meta = {}
    facts = extract_facts_auto("随便一句话", meta)

    assert facts == {"主要症状": ["模型结果"]}
    assert meta["extractor"] == "llm"
    assert meta["usage"]["input_tokens"] == 10


def test_auto_falls_back_on_llm_error(monkeypatch):
    """模型层报错时回退关键词，并把原因留在 meta。"""
    monkeypatch.setenv("USE_MODEL", "1")
    import backend.app.agent.intake_llm as intake_llm

    def boom(text):
        raise RuntimeError("model down")

    monkeypatch.setattr(intake_llm, "extract_facts_with_meta", boom)

    meta = {}
    facts = extract_facts_auto("头部不适", meta)

    assert facts["主要症状"] == ["头部不适"]
    assert meta == {"extractor": "keyword", "fallback": True, "error": "RuntimeError"}
