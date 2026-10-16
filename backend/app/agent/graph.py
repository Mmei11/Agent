"""Day 5-7：问诊 Agent 状态图。intake 用 Day 6 提取，followup 用 Day 7 动态追问。mock 决策器先走通。案例均为虚构。"""

from langgraph.graph import END, START, StateGraph

from backend.app.agent.followup import followup, select_next_field
from backend.app.agent.intake import intake
from backend.app.agent.state import ConsultationState
from backend.app.tools.registry import TOOL_REGISTRY

MAX_FOLLOWUPS = 3


def risk_check(state: ConsultationState) -> dict:
    """预设风险检查：命中时设置 risk_flags，后续条件边把普通流程旁路。"""
    flags = []
    if "呼吸困难" in state.latest_user_message:
        flags.append("R001")
    return {
        "risk_flags": flags,
        "trace": [{"node": "risk_check", "flags": flags}],
    }


def route_after_risk(state: ConsultationState) -> str:
    """命中风险直接走向输出节点渲染固定提示；否则进入正常决策。"""
    if state.risk_flags:
        return "output"
    return "decision"


def decision(state: ConsultationState) -> dict:
    """Mock 决策器：用规则代替模型动作选择。

    停止追问的条件（满足任一即不再追问）：
    - 追问已到上限
    - 缺失字段里没有值得问的了（select_next_field 返回 None）

    不追问时：工具没调过 → CALL_TOOL；调过了 → FINALIZE。
    Day 10 替换为真实模型。
    """
    under_limit = state.followup_count < MAX_FOLLOWUPS
    can_ask = select_next_field(state.missing_fields, state.asked_questions) is not None

    if under_limit and can_ask:
        next_action = "ASK_FOLLOWUP"
    elif "department_suggest" not in state.tool_results:
        next_action = "CALL_TOOL"
    else:
        next_action = "FINALIZE"
    return {
        "next_action": next_action,
        "trace": [{"node": "decision", "next_action": next_action}],
    }


def route_after_decision(state: ConsultationState) -> str:
    """按决策结果分路到追问、工具或输出。"""
    if state.next_action == "ASK_FOLLOWUP":
        return "followup"
    if state.next_action == "CALL_TOOL":
        return "tool"
    return "output"


def tool(state: ConsultationState) -> dict:
    """工具节点：从注册表调用受限工具，把结构化结果写回 state。

    Day 9：mock 决策器只调 department_suggest。
    Day 10 起模型可选择调用哪个工具。
    """
    results = dict(state.tool_results)
    tool_name = "department_suggest"
    tool_fn = TOOL_REGISTRY[tool_name]
    result = tool_fn(state.collected_facts)
    results[tool_name] = result
    return {
        "tool_results": results,
        "trace": [{"node": "tool", "called": tool_name, "result": result}],
    }


def output(state: ConsultationState) -> dict:
    """输出节点：风险路径返回固定提示，正常路径渲染结构化结果。"""
    if state.risk_flags:
        result = "检测到预设风险场景，请立即寻求专业帮助。（规则 R001）"
    else:
        dept_result = state.tool_results.get("department_suggest", {})
        dept = dept_result.get("department")
        if dept:
            result = f"建议方向：{dept}"
        else:
            result = "暂无法判断科室方向"
    return {
        "final_result": result,
        "trace": [{"node": "output", "final": result}],
    }


def build_graph():
    """组装并编译问诊状态图。"""
    graph = StateGraph(ConsultationState)

    graph.add_node("intake", intake)
    graph.add_node("risk_check", risk_check)
    graph.add_node("decision", decision)
    graph.add_node("followup", followup)
    graph.add_node("tool", tool)
    graph.add_node("output", output)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "risk_check")
    graph.add_conditional_edges("risk_check", route_after_risk)
    graph.add_conditional_edges("decision", route_after_decision)
    graph.add_edge("followup", "intake")
    graph.add_edge("tool", "decision")
    graph.add_edge("output", END)

    return graph.compile()


if __name__ == "__main__":
    app = build_graph()

    print("=== 普通路径（信息足够，直接调工具）===")
    normal = app.invoke({"latest_user_message": "头部不适两天，前额，程度轻，暂无其他不适"})
    print("最终结果：", normal["final_result"])
    print("轨迹：")
    for entry in normal["trace"]:
        print(" ", entry)

    print("\n=== 追问路径（信息不足，先追问再调工具）===")
    followup_result = app.invoke({"latest_user_message": "头部不适"})
    print("追问：", followup_result.get("asked_questions", []))
    print("缺失字段：", followup_result.get("missing_fields", []))
    print("轨迹：")
    for entry in followup_result["trace"]:
        print(" ", entry)

    print("\n=== 风险路径 ===")
    risk = app.invoke({"latest_user_message": "模拟人物突然呼吸困难"})
    print("最终结果：", risk["final_result"])
    print("轨迹：")
    for entry in risk["trace"]:
        print(" ", entry)
