"""Day 9 工具测试：正常、无匹配、空参数、visit_preparation。案例均为虚构。"""

from backend.app.tools.registry import TOOL_REGISTRY, department_suggest, visit_preparation


def test_department_suggest_normal():
    """正常匹配：头部不适 → 神经内科。"""
    result = department_suggest({"主要症状": ["头部不适"]})
    assert result == {"department": "神经内科"}


def test_department_suggest_no_match():
    """无匹配：症状不在映射表里，返回暂无法判断。"""
    result = department_suggest({"主要症状": ["嗓子紧"]})
    assert result["department"] is None
    assert result["reason"] == "暂无法判断"


def test_department_suggest_empty_facts():
    """空参数：collected_facts 里没有主要症状。"""
    result = department_suggest({})
    assert result["department"] is None


def test_department_suggest_multiple_symptoms():
    """多个症状：取第一个匹配的。"""
    result = department_suggest({"主要症状": ["嗓子紧", "腹痛"]})
    assert result == {"department": "消化内科"}


def test_visit_preparation_with_facts():
    """有已知信息：清单包含已收集的字段。"""
    result = visit_preparation({
        "主要症状": ["头痛"],
        "持续时间": ["两天"],
        "部位": ["前额"],
    })
    assert len(result["checklist"]) == 3
    assert "主要不适：头痛" in result["checklist"]


def test_visit_preparation_empty_facts():
    """空参数：返回通用准备清单。"""
    result = visit_preparation({})
    assert len(result["checklist"]) == 1
    assert "请准备描述" in result["checklist"][0]


def test_tool_registry_has_both_tools():
    """注册表包含两个工具。"""
    assert "department_suggest" in TOOL_REGISTRY
    assert "visit_preparation" in TOOL_REGISTRY
