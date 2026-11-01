"""Day 5-12 流程测试：普通、风险、追问、否定、含糊、结构化结果。案例均为虚构。"""

import pytest

from backend.app.agent import graph as graph_module
from backend.app.agent.graph import build_graph
from backend.app.storage.saver import get_saver
from backend.app.tools.contract import ToolResult


@pytest.fixture(autouse=True)
def fake_knowledge_tool(monkeypatch):
    """图测试不依赖数据库与外部模型：知识工具固定返回神经内科。"""

    def _call(name, params):
        return ToolResult(
            ok=True,
            name=name,
            data={"department": "神经内科", "reason": None, "hits": []},
            latency_ms=1,
        )

    monkeypatch.setattr(graph_module, "call_tool", _call)


def test_normal_path_calls_tool_then_finalizes():
    """信息足够时：decision → tool → decision → output，最终有结构化结果。"""
    app = build_graph()
    result = app.invoke({
        "latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适，无病史"
    })

    assert result["risk_flags"] == []
    assert result["tool_results"]["department_route"]["department"] == "神经内科"
    final = result["final_result"]
    assert final["status"] == "completed"
    assert final["tools_used"] == ["department_route"]
    assert "神经内科" in final["next_step"]

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
    assert "department_route" not in result.get("tool_results", {})
    final = result["final_result"]
    assert final["status"] == "risk_route"
    assert "R001" in final["risk_message"]

    nodes = [entry["node"] for entry in result["trace"]]
    assert nodes == ["intake", "risk_check", "output"]


def test_followup_path_asks_then_tools(tmp_path):
    """每条请求只问一次；三轮后在下一次回答时整理结果。"""
    with get_saver(str(tmp_path / "graph.sqlite")) as saver:
        app = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "followup-path"}}
        first = app.invoke({"latest_user_message": "头部不适"}, config=config)
        assert first["followup_count"] == 1
        assert first["final_result"] == {}
        assert len(first["conversation"]) == 2

        app.invoke({"latest_user_message": "前两天开始的"}, config=config)
        third = app.invoke({"latest_user_message": "在前额"}, config=config)
        assert third["followup_count"] == 3
        assert third["final_result"] == {}
        result = app.invoke({"latest_user_message": "程度轻"}, config=config)
        assert result["tool_results"]["department_route"]["department"] == "神经内科"
        assert result["final_result"]["status"] == "completed"


def test_low_priority_field_only_one_followup(tmp_path):
    """只缺背景时先问一次，用户回答后才整理结果。"""
    with get_saver(str(tmp_path / "one-followup.sqlite")) as saver:
        app = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "one-followup"}}
        first = app.invoke({"latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适"}, config=config)
        assert first["followup_count"] == 1
        assert first["final_result"] == {}
        result = app.invoke({"latest_user_message": "无病史"}, config=config)
        assert result["tool_results"]["department_route"]["department"] == "神经内科"


def test_missing_fields_not_duplicated():
    """missing_fields 每轮覆盖，不追加重复。"""
    app = build_graph()
    result = app.invoke({"latest_user_message": "头部不适"})

    assert result["missing_fields"] == ["持续时间", "程度", "伴随表现", "已知背景"]
    assert len(result["missing_fields"]) == len(set(result["missing_fields"]))


def test_no_infinite_loop():
    """任意输入都在有限步骤内追问或输出。"""
    app = build_graph()
    for message in ["头部不适", "", "呼吸困难", "头痛三天严重恶心"]:
        result = app.invoke({"latest_user_message": message})
        assert len(result["trace"]) <= 10
        assert result["final_result"] or result["asked_questions"]


def test_negation_goes_normal_path():
    """否定句'没有呼吸困难'不命中风险，走正常路径。"""
    app = build_graph()
    result = app.invoke({"latest_user_message": "没有呼吸困难，头部不适两天，前额，程度轻，暂无其他不适，无病史"})
    assert result["risk_flags"] == []
    final = result["final_result"]
    assert final["status"] == "completed"
    assert "神经内科" in final["next_step"]


def test_vague_goes_output_with_hint():
    """含糊表述'有点喘'走输出，提示寻求专业帮助。"""
    app = build_graph()
    result = app.invoke({"latest_user_message": "模拟人物好像有点喘"})
    assert "V001" in result["risk_flags"]
    final = result["final_result"]
    assert final["status"] == "risk_route"
    assert "寻求专业帮助" in final["risk_message"]
