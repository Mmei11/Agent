"""Day 5-10：问诊 Agent 状态图。决策器支持模型和预设两种模式。案例均为虚构。"""

from langgraph.graph import END, START, StateGraph  # LangGraph 图的组件

# 从 decision.py 导入决策相关函数
from backend.app.agent.decision import (
    choose_auto,  # 模型决策，未启用或失败时回退规则
    gate_by_confidence,  # 置信度不足时收回决定权
    validate_action,  # 校验动作能否执行，不能就兜底
)
from backend.app.agent.followup import followup, select_next_field  # 追问节点和字段选择
from backend.app.agent.intake import intake  # 入口节点，提取字段
from backend.app.agent.result import (
    build_completed_result,
    build_error_result,
    build_risk_result,
    build_vague_result,
    render_text,
)
from backend.app.agent.state import ConsultationState  # 状态类型
from backend.app.observability import current_trace, traced_node
from backend.app.safety.rule_engine import final_check, screen  # 风险检查和最终输出检查
from backend.app.tools.registry import TOOL_REGISTRY  # 工具注册表

MAX_FOLLOWUPS = 3  # 普通追问上限，spec 要求的工程边界


def _trace_id() -> str:
    """当前请求的 trace id；直接调图（单测/脚本）时为空串。"""
    scope = current_trace()
    return scope.trace_id if scope else ""


def risk_check(state: ConsultationState) -> dict:
    """风险检查：对用户消息做风险筛查。命中风险走固定分支，含糊标记不确定。"""
    result = screen(state.latest_user_message)  # 调安全模块检查
    flags = []
    if result["level"] == "risk":
        flags.append(result["rule"])  # 命中风险 → R001
    elif result["level"] == "vague":
        flags.append(result["rule"])  # 含糊 → V001，保守分流
    return {
        "risk_flags": flags,
        # 摘要只留级别和规则号，完整 screen 结果不再复制进 checkpoint。
        "trace": [
            {
                "node": "risk_check",
                "risk_level": result["level"],
                "rule": result["rule"],
                "trace_id": _trace_id(),
            }
        ],
    }


def route_after_risk(state: ConsultationState) -> str:
    """命中风险或含糊直接走向输出；否则进入正常决策。"""
    if state.risk_flags:
        return "output"
    return "decision"


def decision(state: ConsultationState) -> dict:
    """决策节点：模型（或规则）选动作，置信度闸门，再由程序校验权限。

    trace 记录模型选了什么、依据和置信度、最终执行什么、是否被降级。
    """
    result = choose_auto(state, MAX_FOLLOWUPS)
    trusted_choice, downgraded = gate_by_confidence(
        result.action, result.confidence, state, MAX_FOLLOWUPS
    )
    final_action = validate_action(trusted_choice, state, MAX_FOLLOWUPS)

    return {
        "next_action": final_action,  # 最终执行的动作，决定走哪条边
        "trace": [
            {
                "node": "decision",
                "model_choice": result.action,  # 模型或规则原本选了什么
                "reason": result.reason,  # 模型给的依据
                "confidence": result.confidence,  # 规则决策为 None
                "final_action": final_action,  # 闸门和校验后的动作
                "downgraded_by_confidence": downgraded,  # 是否因低置信度被收回
                "same": result.action == final_action,  # False 说明被改过
                "model_usage": result.usage,  # token 用量与成本估算，供 M4 统计
                "trace_id": _trace_id(),
            }
        ],
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
    results = dict(state.tool_results)  # 复制已有工具结果，不改原 state
    tool_name = "department_suggest"  # 要调用的工具名（Day 10 起模型来选）
    tool_fn = TOOL_REGISTRY[tool_name]  # 从注册表取出对应的函数
    try:
        result = tool_fn(state.collected_facts)  # 调用工具，传入已收集的字段
    except Exception as exc:
        # 工具异常写成结构化错误，不让异常直接变成 API 500。
        error_message = f"{tool_name} 工具执行失败（{type(exc).__name__}）"
        results[tool_name] = {"error": error_message}
        return {
            "tool_results": results,
            "trace": [
                {
                    "node": "tool",
                    "called": tool_name,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "trace_id": _trace_id(),
                }
            ],
        }
    results[tool_name] = result  # 把结构化结果写回字典
    return {
        "tool_results": results,
        # 工具返回值已经在 tool_results 里，这里只记调用名与成败。
        "trace": [
            {
                "node": "tool",
                "called": tool_name,
                "ok": True,
                "trace_id": _trace_id(),
            }
        ],
    }


def output(state: ConsultationState) -> dict:
    """输出节点：生成结构化结果，渲染文案，最终检查边界。"""
    tool_error = next(
        (
            result["error"]
            for result in state.tool_results.values()
            if isinstance(result, dict) and result.get("error")
        ),
        None,
    )
    if state.risk_flags:  # 命中风险或含糊
        rule = state.risk_flags[0]
        if rule == "V001":  # 含糊 → 保守提示
            structured = build_vague_result()
        else:  # 明确风险 → 固定提示
            structured = build_risk_result(rule)
    elif tool_error:
        # 工具节点报告了异常时，输出统一的结构化 error 结果。
        structured = build_error_result(tool_error)
    else:  # 正常路径 → 已知、未知、工具、下一步
        structured = build_completed_result(
            state.collected_facts, state.missing_fields, state.tool_results
        )

    # 渲染文案（不含 limitations，先检查核心内容）
    rendered = render_text(structured)

    # 最终检查：只检查系统生成的建议（next_step 和 risk_message），不检查用户原话
    check_text = structured.get("next_step", "") + " " + (structured.get("risk_message") or "")
    boundary = final_check(check_text)
    if boundary["level"] != "safe":
        structured = build_error_result(boundary["reason"])
        rendered = "本系统不提供确诊、处方或用药建议，请咨询专业人员。"
    else:
        # 检查通过后追加 limitations（说明性文字，不含实际建议）
        rendered = rendered + "\n\n" + structured.get("limitations", "")

    return {
        "final_result": structured,  # 结构化字典，给测试和前端解析
        "rendered_text": rendered,  # 渲染文案，给用户直接看
        "conversation": [{"role": "agent", "text": rendered}],
        "trace": [
            {
                "node": "output",
                "result_status": structured.get("status"),
                "boundary_level": boundary["level"],
                "boundary_rule": boundary.get("rule"),
                "trace_id": _trace_id(),
            }
        ],
    }


def build_graph(checkpointer=None):
    """组装并编译问诊状态图。

    checkpointer=None 时无持久化（原有行为，不破坏测试）；
    传入 SqliteSaver 时启用会话持久化，按 thread_id 存储和恢复状态。
    """
    graph = StateGraph(ConsultationState)  # 用 ConsultationState 作为图的状态类型

    # 注册六个节点：埋点包在注册处，节点函数本身保持纯业务逻辑。
    graph.add_node("intake", traced_node("intake")(intake))  # 入口：提取字段
    graph.add_node("risk_check", traced_node("risk_check")(risk_check))  # 风险检查
    graph.add_node("decision", traced_node("decision")(decision))  # 决策：模型或预设选动作
    graph.add_node("followup", traced_node("followup")(followup))  # 追问：选字段生成问题
    graph.add_node("tool", traced_node("tool")(tool))  # 工具：调注册表里的受限工具
    graph.add_node("output", traced_node("output")(output))  # 输出：渲染最终结果

    # 连边
    graph.add_edge(START, "intake")  # 起点 → 入口
    graph.add_edge("intake", "risk_check")  # 入口 → 风险检查（固定顺序）
    graph.add_conditional_edges("risk_check", route_after_risk)  # 风险检查后分路：命中→output，未命中→decision
    graph.add_conditional_edges("decision", route_after_decision)  # 决策后分路：追问/工具/输出
    graph.add_edge("followup", END)  # 一次请求只追问一次，等待用户下一轮回答
    graph.add_edge("tool", "decision")  # 工具 → 回到决策（循环）
    graph.add_edge("output", END)  # 输出 → 结束

    return graph.compile(checkpointer=checkpointer)  # 编译图，传入 checkpointer 启用持久化


if __name__ == "__main__":
    from backend.app.storage.saver import get_saver

    # 用 SqliteSaver 启用会话持久化
    with get_saver() as saver:
        app = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "demo-session-1"}}

        print("=== 第一轮：用户只说症状 ===")
        r1 = app.invoke(
            {"latest_user_message": "头部不适"},
            config=config,
        )
        print("已收集：", r1.get("collected_facts", {}))
        print("追问过：", r1.get("asked_questions", []))
        print("缺失：", r1.get("missing_fields", []))

        print("\n=== 第二轮：用户补充信息（同一会话）===")
        r2 = app.invoke(
            {"latest_user_message": "前两天开始的，在前额"},
            config=config,  # 同一个 thread_id → 自动恢复第一轮的状态
        )
        print("已收集：", r2.get("collected_facts", {}))
        print("追问过：", r2.get("asked_questions", []))
        print("最终结果：", r2.get("final_result", {}))

        print("\n=== 不同会话互不污染 ===")
        r3 = app.invoke(
            {"latest_user_message": "腹痛三天"},
            config={"configurable": {"thread_id": "demo-session-2"}},
        )
        print("会话2 已收集：", r3.get("collected_facts", {}))
        # 会话2 不应该看到会话1 的"头部不适"
        assert "头部不适" not in str(r3.get("collected_facts", {}))
