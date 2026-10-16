"""Day 5 最小流程测试：普通路径和风险路径的核心断言。案例均为虚构。"""

from backend.app.agent.graph import build_graph


def test_normal_path_calls_tool_then_finalizes():
    """信息足够时：decision → tool → decision → output，最终有结果。"""
    app = build_graph()
    result = app.invoke({
        "latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史"
    })

    assert result["risk_flags"] == []
    assert result["tool_results"]["department_suggest"] == "内科方向（模拟）"
    assert result["final_result"] == "建议方向：内科方向（模拟）"

    nodes = [entry["node"] for entry in result["trace"]]
    assert nodes == [
        "intake",
        "risk_check",
        "decision",
        "tool",
        "decision",
        "output",
    ]


def test_risk_path_bypasses_decision():
    """风险路径：risk_check 命中后直接走向 output，不经过 decision。"""
    app = build_graph()
    result = app.invoke({"latest_user_message": "模拟人物突然呼吸困难"})

    assert result["risk_flags"] == ["R001"]
    assert "department_suggest" not in result.get("tool_results", {})
    assert result["final_result"] == "检测到预设风险场景，请立即寻求专业帮助。（规则 R001）"

    nodes = [entry["node"] for entry in result["trace"]]
    assert nodes == ["intake", "risk_check", "output"]


def test_followup_path_asks_then_tools():
    """追问路径：信息不足时先追问，达到上限后调工具结束。"""
    app = build_graph()
    result = app.invoke({"latest_user_message": "头部不适"})

    assert result["followup_count"] == 3
    assert result["tool_results"]["department_suggest"] == "内科方向（模拟）"

    nodes = [entry["node"] for entry in result["trace"]]
    # 应包含三次 followup，最后走 tool → decision → output
    followup_count = nodes.count("followup")
    assert followup_count == 3
    assert nodes[-2:] == ["decision", "output"]


def test_low_priority_field_only_one_followup():
    """只缺低优先级字段：问一次后不再追问，直接调工具。"""
    app = build_graph()
    result = app.invoke({
        "latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适"
    })

    # 只缺已知背景，问一次后 select_next_field 返回 None，不再追问
    assert result["followup_count"] == 1
    assert result["tool_results"]["department_suggest"] == "内科方向（模拟）"


def test_missing_fields_not_duplicated():
    """missing_fields 每轮覆盖，不追加重复。"""
    app = build_graph()
    result = app.invoke({"latest_user_message": "头部不适"})

    assert result["missing_fields"] == ["持续时间", "程度", "伴随表现", "已知背景"]
    # 确保没有重复元素
    assert len(result["missing_fields"]) == len(set(result["missing_fields"]))


def test_no_infinite_loop():
    """任意输入都不会死循环：最终都到达 output。"""
    app = build_graph()
    for message in ["头部不适", "", "呼吸困难", "头痛三天严重恶心"]:
        result = app.invoke({"latest_user_message": message})
        assert "final_result" in result
        assert result["final_result"]  # 非空
