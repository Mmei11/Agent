"""Day 5-10：问诊 Agent 状态图。决策器支持模型和预设两种模式。案例均为虚构。"""

import os

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
    build_refusal_result,
    build_risk_result,
    build_vague_result,
    render_text,
)
from backend.app.agent.state import ConsultationState  # 状态类型
from backend.app.agent.opening import route_general  # 未出结果时的普通/医疗预路由
from backend.app.agent.answer import answer_followup  # 结果生成后的追问回答
from backend.app.observability import current_trace, span, traced_node
from backend.app.safety.audit import AuditEvent, input_digest, write as write_audit
from backend.app.safety.output_rail import check as check_output
from backend.app.safety.policy import decide as decide_policy
from backend.app.safety.rule_engine import final_check  # 最终输出检查
from backend.app.tools.knowledge_tools import DEPARTMENT_ROUTE
from backend.app.tools.registry import call_tool as _live_call_tool
from backend.app.tools.registry import stub_call_tool

# CI 与门禁自检没有向量库，用确定性替身跑通流程；本地与生产默认走真实检索。
call_tool = (
    stub_call_tool
    if os.getenv("KNOWLEDGE_BACKEND", "pgvector") == "stub"
    else _live_call_tool
)

MAX_FOLLOWUPS = 3  # 普通追问上限，spec 要求的工程边界


def _trace_id() -> str:
    """当前请求的 trace id；直接调图（单测/脚本）时为空串。"""
    scope = current_trace()
    return scope.trace_id if scope else ""


def risk_check(state: ConsultationState) -> dict:
    """读取输入层结论，按策略优先级决定是否升级，并写审计。"""
    level = state.input_level or "safe"
    labels = list(state.input_labels)
    flags: list[str] = []
    if level == "risk":
        flags.append("R001")
    elif level == "vague":
        flags.append("V001")
    elif level == "jailbreak":
        flags.extend(label for label in labels if label.startswith("I"))

    vague_turns = state.vague_turns + 1 if level == "vague" else 0
    probe = state.model_copy(update={"risk_flags": flags, "vague_turns": vague_turns})
    with span("safety.policy", kind="safety", attributes={"policy.level": level}) as record:
        policy_decision = decide_policy(None, probe, "")
        record.attributes["policy.rule"] = policy_decision.rule
        record.attributes["policy.action"] = policy_decision.action

    if policy_decision.audit:
        write_audit(
            AuditEvent(
                layer="policy",
                rule=policy_decision.rule or "",
                action=policy_decision.action or "",
                session_id=state.session_id,
                turn_id=state.turn_id,
                input_digest=input_digest(state.latest_user_message),
                detail=policy_decision.reason,
            )
        )

    return {
        "risk_flags": flags,
        "vague_turns": vague_turns,
        # 摘要只留级别和规则号，完整输入结论不再复制进 checkpoint。
        "trace": [
            {
                "node": "risk_check",
                "input_level": level,
                "risk_level": level,
                "rule": flags[0] if flags else None,
                "policy_rule": policy_decision.rule,
                "policy_action": policy_decision.action,
                "trace_id": _trace_id(),
            }
        ],
    }


def route_after_risk(state: ConsultationState) -> str:
    """命中风险或含糊直接走向输出；已有结果时进入追问回答；否则先预路由。"""
    if state.risk_flags:
        return "output"
    if state.turn_kind == "followup":
        return "answer"
    return "general_route"


def route_after_general(state: ConsultationState) -> str:
    """预路由结果：普通问题结束本轮，医疗问题回到字段决策。"""
    return "general" if state.next_action == "GENERAL" else "medical"


def decision(state: ConsultationState) -> dict:
    """决策节点：模型（或规则）选动作，置信度闸门，策略层复核，再由程序校验权限。

    trace 记录模型选了什么、策略层是否覆盖、最终执行什么、是否被降级。
    """
    result = choose_auto(state, MAX_FOLLOWUPS)
    trusted_choice, downgraded = gate_by_confidence(
        result.action, result.confidence, state, MAX_FOLLOWUPS
    )
    policy_decision = decide_policy(None, state, trusted_choice)
    final_action = (
        policy_decision.action
        if policy_decision.action
        else validate_action(trusted_choice, state, MAX_FOLLOWUPS)
    )

    return {
        "next_action": final_action,  # 最终执行的动作，决定走哪条边
        "trace": [
            {
                "node": "decision",
                "model_choice": result.action,  # 模型或规则原本选了什么
                "reason": result.reason,  # 模型给的依据
                "confidence": result.confidence,  # 规则决策为 None
                "policy_rule": policy_decision.rule,
                "policy_action": policy_decision.action,
                "final_action": final_action,  # 策略、闸门和校验后的动作
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


KNOWLEDGE_TOOL_NAME = DEPARTMENT_ROUTE  # 目前唯一接进图的知识工具


def _query_from_facts(collected_facts: dict) -> str:
    """把已收集的字段拼成一句检索问句。没有事实时返回空串，由调用方处理。"""
    parts: list[str] = []
    for values in collected_facts.values():
        if isinstance(values, list):
            parts.extend(str(value) for value in values if value)
        elif values:
            parts.append(str(values))
    return " ".join(parts).strip()


def tool(state: ConsultationState) -> dict:
    """工具节点：按契约调用知识工具，把结构化结果写回 state。

    Day 9 直接调注册表里的裸函数；Phase 4 起走 ToolSpec，参数校验、
    超时和失败分类都由契约层保证。
    """
    results = dict(state.tool_results)  # 复制已有工具结果，不改原 state
    tool_name = KNOWLEDGE_TOOL_NAME
    trace_id = _trace_id()

    query = _query_from_facts(state.collected_facts)
    if not query:  # 没有任何事实可查，直接按"无依据"处理，不发请求
        results[tool_name] = {"department": None, "reason": "no_input", "hits": []}
        return {
            "tool_results": results,
            "trace": [
                {"node": "tool", "called": tool_name, "ok": True, "trace_id": trace_id}
            ],
        }

    outcome = call_tool(tool_name, {"query": query})
    if not outcome.ok:
        # 失败也写结构化结果：output 节点看到 error 就走错误分支。
        results[tool_name] = {"error": outcome.error, "error_kind": outcome.error_kind}
        return {
            "tool_results": results,
            "trace": [
                {
                    "node": "tool",
                    "called": tool_name,
                    "ok": False,
                    "error_type": outcome.error_kind,
                    "trace_id": trace_id,
                }
            ],
        }

    results[tool_name] = outcome.data  # 把结构化结果写回字典
    return {
        "tool_results": results,
        # 工具返回值已经在 tool_results 里，这里只记调用名与成败。
        "trace": [
            {
                "node": "tool",
                "called": tool_name,
                "ok": True,
                "latency_ms": outcome.latency_ms,
                "trace_id": trace_id,
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
    if state.input_level == "jailbreak":
        # 输入试图改写系统规则：不执行，直接固定拒绝。
        structured = build_refusal_result()
    elif state.risk_flags:  # 命中风险或含糊
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

    # 第一道：最终检查只检查系统生成的建议（next_step 和 risk_message），
    # 不检查用户原话。
    check_text = structured.get("next_step", "") + " " + (structured.get("risk_message") or "")
    boundary = final_check(check_text)
    if boundary["level"] != "safe":
        structured = build_error_result(boundary["reason"])
        rendered = "本系统不提供确诊、处方或用药建议，请咨询专业人员。"
    else:
        # 检查通过后追加 limitations（说明性文字，不含实际建议）
        rendered = rendered + "\n\n" + structured.get("limitations", "")

    # 第二道：guardrails 校验器复核完整文案；失败整段替换为安全兜底。
    with span("safety.output", kind="safety") as record:
        output_verdict = check_output(
            rendered, uses_knowledge=bool(structured.get("source_note"))
        )
        record.attributes["output.passed"] = output_verdict.passed
        record.attributes["output.violations"] = len(output_verdict.violations)
    if not output_verdict.passed:
        write_audit(
            AuditEvent(
                layer="output",
                rule="F003",
                action="REPLACE",
                session_id=state.session_id,
                turn_id=state.turn_id,
                input_digest=input_digest(state.latest_user_message),
                detail="; ".join(output_verdict.violations),
            )
        )
        structured = build_error_result("输出内容超出安全边界")
        rendered = output_verdict.text

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
                "output_passed": output_verdict.passed,
                "output_violations": list(output_verdict.violations),
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
    graph.add_node("general_route", route_general)  # 普通/医疗预路由；模型调用自带 span
    graph.add_node("decision", traced_node("decision")(decision))  # 决策：模型或预设选动作
    graph.add_node("followup", traced_node("followup")(followup))  # 追问：选字段生成问题
    graph.add_node("tool", traced_node("tool")(tool))  # 工具：调注册表里的受限工具
    graph.add_node("output", traced_node("output")(output))  # 输出：渲染最终结果
    graph.add_node("answer", traced_node("answer")(answer_followup))  # 追问回答

    # 连边
    graph.add_edge(START, "intake")  # 起点 → 入口
    graph.add_edge("intake", "risk_check")  # 入口 → 风险检查（固定顺序）
    graph.add_conditional_edges("risk_check", route_after_risk)  # 风险→output；追问→answer；否则→general_route
    graph.add_conditional_edges(
        "general_route",
        route_after_general,
        {"general": END, "medical": "decision"},
    )
    graph.add_conditional_edges("decision", route_after_decision)  # 决策后分路：追问/工具/输出
    graph.add_edge("followup", END)  # 一次请求只追问一次，等待用户下一轮回答
    graph.add_edge("tool", "decision")  # 工具 → 回到决策（循环）
    graph.add_edge("output", END)  # 输出 → 结束
    graph.add_edge("answer", END)  # 追问回答 → 结束

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
