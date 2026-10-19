"""Day 12 结构化结果测试：四种结局的 schema 校验和渲染。案例均为虚构。"""

from backend.app.agent.result import (
    build_completed_result,
    build_error_result,
    build_risk_result,
    build_vague_result,
    render_text,
)


def test_completed_result_has_all_fields():
    """正常完成：结构化结果包含所有必要字段。"""
    result = build_completed_result(
        {"主要症状": ["头部不适"]},
        ["持续时间"],
        {"department_suggest": {"department": "神经内科"}},
    )
    assert result["status"] == "completed"
    assert result["known_facts"] == {"主要症状": ["头部不适"]}
    assert result["unknowns"] == ["持续时间"]
    assert result["tools_used"] == ["department_suggest"]
    assert "神经内科" in result["next_step"]
    assert result["risk_message"] is None
    assert "不提供确诊" in result["limitations"]


def test_completed_result_no_match():
    """工具无匹配：next_step 提示暂无法判断。"""
    result = build_completed_result(
        {"主要症状": ["未知症状"]},
        [],
        {"department_suggest": {"department": None, "reason": "暂无法判断"}},
    )
    assert "暂无法判断" in result["next_step"]


def test_risk_result():
    """风险路径：status=risk_route，有 risk_message。"""
    result = build_risk_result("R001")
    assert result["status"] == "risk_route"
    assert "R001" in result["risk_message"]
    assert "急救" in result["next_step"]


def test_vague_result():
    """含糊路径：status=risk_route，提示寻求帮助。"""
    result = build_vague_result()
    assert result["status"] == "risk_route"
    assert "寻求专业帮助" in result["risk_message"]


def test_error_result():
    """错误路径：status=error，next_step 含原因。"""
    result = build_error_result("工具超时")
    assert result["status"] == "error"
    assert "工具超时" in result["next_step"]


def test_render_completed_text():
    """渲染正常结果：包含已知信息和下一步。"""
    result = build_completed_result(
        {"主要症状": ["头部不适"]},
        ["持续时间"],
        {"department_suggest": {"department": "神经内科"}},
    )
    text = render_text(result)
    assert "已知信息" in text
    assert "头部不适" in text
    assert "尚不确定" in text
    assert "持续时间" in text
    assert "神经内科" in text


def test_render_risk_text():
    """渲染风险结果：只含 risk_message。"""
    result = build_risk_result("R001")
    text = render_text(result)
    assert "R001" in text


def test_render_error_text():
    """渲染错误结果：只含 next_step。"""
    result = build_error_result("工具超时")
    text = render_text(result)
    assert "工具超时" in text
