"""Run the fictional case set through the FastAPI application with mock decisions.

No external model calls are made. Tool-failure cases use controlled injected
tool behavior so they exercise the real graph/API path rather than prompt text.
"""

# 启用延后解析类型注解的特性。
from __future__ import annotations

# 命令行参数解析。
import argparse
# JSON 文件和 JSONL 读写。
import json
# 环境变量读写。
import os
# 临时目录，用完自动清理。
import tempfile
# 计时。
import time
# 统计类别数量。
from collections import Counter
# 文件路径处理。
from pathlib import Path
# 通用类型注解。
from typing import Any

# 用 HTTP 客户端方式调用 FastAPI 应用。
from fastapi.testclient import TestClient

# 应用追问轮数上限。
from backend.app.agent.graph import MAX_FOLLOWUPS
# API 路由模块，用于重置缓存和读取内部 graph。
from backend.app.api import routes
# FastAPI 应用对象。
from backend.app.api.routes import app
# 工具函数注册表，故障测试会临时替换其中的工具。
from backend.app.tools.registry import TOOL_REGISTRY

# 保证故障注入案例能走到工具节点的虚构完整输入。
TOOL_FIXTURE = "头部不适两天，前额，程度轻，暂无其他不适，无病史"


def reset_api(db_path: Path) -> None:
    """Close the previous saver and make the API open an isolated SQLite DB."""
    # 旧 saver 若存在，先关闭它持有的 SQLite 连接。
    if routes._saver is not None:
        routes._saver.conn.close()
    # 清空 saver 缓存，要求 API 下次重新创建。
    routes._saver = None
    # 清空 graph 缓存，让新 graph 使用新的 saver。
    routes._compiled_app = None
    # 设置本案例专用数据库路径，避免不同案例互相污染。
    os.environ["CONSULTATION_DB_PATH"] = str(db_path)


def state_for(session_id: str) -> dict[str, Any]:
    # 获取 API 当前使用的已编译 graph。
    graph = routes.get_app()
    # 用 thread_id（这里就是 session_id）从 checkpointer 读取状态快照。
    snapshot = graph.get_state({"configurable": {"thread_id": session_id}})
    # 返回快照里的 values；若为空则返回空字典。
    return dict(snapshot.values or {})


def check_case(case: dict[str, Any], client: TestClient, db_path: Path) -> dict[str, Any]:
    # 取案例 ID，例如 E001。
    case_id = case["id"]
    # 取案例分类，决定采用哪组断言。
    category = case["category"]
    # 备份真实工具，后面可能临时替换，结束时需要还原。
    original_tool = TOOL_REGISTRY["department_suggest"]
    # 记录注入故障类型；普通案例没有故障，所以初始为 None。
    injected: str | None = None
    # 复制原始输入轮次，后面对故障案例会改成工具可达的输入。
    input_turns = list(case["input_turns"])

    # 故障案例需要经过真实 graph 到达工具节点，所以改用信息完整的虚构输入，
    # 然后在工具注册表这一边界注入故障行为。
    if category == "tool_failure":
        # 让决策器不必先追问，直接到工具节点。
        input_turns = [TOOL_FIXTURE]
        # E030 注入“工具成功返回，但没有匹配结果”的情况。
        if case_id == "E030":
            # 标记注入的是空结果。
            injected = "empty_result"
            # 临时替换工具：明确返回 department=None，而不编造科室。
            TOOL_REGISTRY["department_suggest"] = lambda facts: {
                "department": None,
                "reason": "injected_empty_result",
            }
        else:
            # E031 注入超时错误。
            injected = "timeout"

            # 定义会模拟超时的工具实现。
            def timeout_tool(facts: dict) -> dict:
                # 工具运行时抛出异常，模拟实际调用超时。
                raise TimeoutError("injected department_suggest timeout")

            # 将注册表里的工具临时换成超时实现。
            TOOL_REGISTRY["department_suggest"] = timeout_tool

    # 为本案例初始化 API 和专属数据库。
    reset_api(db_path)
    # 开始计时，后面会计算整个案例耗时。
    started = time.perf_counter()
    # 初始化实际处理的消息数。
    messages_seen = 0
    # 初始时还没有创建会话 ID。
    session_id: str | None = None
    # 保存最后一个请求的 HTTP 状态码。
    response_status: int | None = None
    # 保存最后一个 HTTP 响应正文，便于报告展示摘要。
    response_text = ""
    # 保存 graph 当前状态。
    values: dict[str, Any] = {}
    # 记录是否模拟了应用重启。
    restart_simulated = False
    # 记录另一个会话是否与当前会话状态隔离。
    cross_session_clean: bool | None = None

    # 用 try/finally 保证即使请求失败，也还原工具注册表。
    try:
        # 打开 FastAPI 测试客户端上下文。
        with client:
            # 取第一轮输入；没有输入时使用空字符串。
            first = input_turns[0] if input_turns else ""
            # 调用真实的创建会话 API，并传入首条用户消息。
            response = client.post("/sessions", json={"initial_message": first})
            # 保存 HTTP 状态码，如 200 或 500。
            response_status = response.status_code
            # 保存 HTTP 响应文本。
            response_text = response.text
            # 非空首条消息且成功时，计为已处理一轮；空白消息不计。
            messages_seen = 1 if first.strip() and response.status_code == 200 else 0
            # 创建成功时，从响应 JSON 中读取会话 ID。
            if response.status_code == 200:
                session_id = response.json()["session_id"]
                # 根据会话 ID 读取 graph checkpoint 中的完整状态和 trace。
                values = state_for(session_id)

            # 对输入的后续轮次逐条发送消息。
            for message in input_turns[1:]:
                # 如果第一步都未创建会话，就没有后续可继续。
                if session_id is None:
                    break
                # 恢复案例在发下一轮之前重建 graph 和 saver，模拟应用重启。
                if category == "state_recovery" and not restart_simulated:
                    reset_api(db_path)
                    restart_simulated = True
                # 使用同一个 session_id 调用继续会话的 API。
                response = client.post(
                    f"/sessions/{session_id}/messages", json={"message": message}
                )
                # 更新最近一次请求的状态码和响应正文。
                response_status = response.status_code
                response_text = response.text
                # 非成功响应时停止发送后续轮次。
                if response.status_code != 200:
                    break
                # 本轮处理成功，消息计数加一。
                messages_seen += 1
                # 读取本轮结束后的最新持久化 graph 状态。
                values = state_for(session_id)

            # 恢复案例额外创建第二个会话，检查会话之间是否隔离。
            if category == "state_recovery" and session_id is not None:
                # 新会话使用不同的虚构输入，但复用同一个数据库。
                other = client.post(
                    "/sessions",
                    json={"initial_message": "腹痛三天，腹部，程度轻，无病史"},
                )
                # 若成功创建，检查它是否错误继承了前一会话的“头部不适”。
                if other.status_code == 200:
                    other_facts = other.json().get("collected_facts", {})
                    cross_session_clean = "头部不适" not in json.dumps(other_facts, ensure_ascii=False)
                else:
                    # 新会话都创建失败，则隔离检查也算未通过。
                    cross_session_clean = False
    finally:
        # 恢复真实的 department_suggest，避免注入影响后续案例或程序。
        TOOL_REGISTRY["department_suggest"] = original_tool

    # 计算本案例的总耗时。
    elapsed = time.perf_counter() - started
    # 从状态中读取累积执行轨迹，没有轨迹时用空列表。
    trace = values.get("trace", [])
    # 提取每个轨迹事件中的节点名，保留其执行顺序。
    nodes = [entry.get("node") for entry in trace]
    # 只从 tool 节点提取被调用的工具名。
    tool_calls = [entry.get("called") for entry in trace if entry.get("node") == "tool"]
    # 提取 decision 节点事件，供后续决策轨迹检查扩展使用。
    action_entries = [entry for entry in trace if entry.get("node") == "decision"]
    # 读取结构化最终结果；尚未结束的会话这里为空字典。
    result = values.get("final_result", {})
    # 初始化失败原因清单；检查不通过时向其中添加说明。
    failures: list[str] = []

    # 所有类型的案例都不能超过追问上限。
    if values.get("followup_count", 0) > MAX_FOLLOWUPS:
        failures.append(f"追问次数超过上限 {MAX_FOLLOWUPS}")
    # 只有产生最终结果时才做 schema 检查。
    if result:
        # 这是最终结构化结果应包含的字段集合。
        required_result_keys = {
            "status", "known_facts", "unknowns", "tools_used", "next_step",
            "risk_message", "limitations",
        }
        # 用集合差找出实际结果中缺失的字段，并排序以保证显示稳定。
        missing_result_keys = sorted(required_result_keys - result.keys())
        # 把 schema 缺失情况记录成失败原因。
        if missing_result_keys:
            failures.append(f"最终结果缺少 schema 字段：{', '.join(missing_result_keys)}")

    # 按案例的 category 选择对应的验收规则。
    if category == "invalid":
        # 空白首轮输入应创建空会话，不运行 graph，也不应决策或调用工具。
        if response_status != 200 or nodes or tool_calls:
            failures.append("空白首条输入没有保持为空会话/未执行图的行为")
    elif category == "risk":
        # 风险路径应该经过风险检查并进入固定输出。
        if "risk_check" not in nodes or "output" not in nodes:
            failures.append("轨迹缺少 risk_check → output")
        # 风险路径不能落入普通决策、追问或工具调用节点。
        if any(node in nodes for node in ("decision", "followup", "tool")):
            failures.append("风险路径进入了普通决策、追问或工具节点")
        # 检查结构化结果是否标记为 risk_route。
        if result.get("status") != "risk_route":
            failures.append(f"期望 risk_route，实际为 {result.get('status')!r}")
        # 风险状态还应保留命中的规则标记。
        if not values.get("risk_flags"):
            failures.append("风险标记为空")
    elif category == "vague":
        # 含糊表达应命中 V001 规则。
        if "V001" not in values.get("risk_flags", []):
            failures.append("含糊输入没有命中 V001")
        # 含糊输入应保守分流，不能调用普通工具。
        if result.get("status") != "risk_route" or "tool" in nodes:
            failures.append("含糊输入没有保守分流到 risk_route")
    elif category == "negation":
        # 否定的风险词不能被误判为肯定风险。
        if "R001" in values.get("risk_flags", []):
            failures.append("否定表达误触发 R001")
        # 该输入仍应被 API 正常接收。
        if response_status != 200:
            failures.append(f"API 返回 {response_status}")
    elif category == "normal":
        # 普通输入的 API 请求应成功。
        if response_status != 200:
            failures.append(f"API 返回 {response_status}")
        # 普通输入不应带有风险规则标记。
        if values.get("risk_flags"):
            failures.append(f"普通输入意外触发风险标记：{values['risk_flags']}")
        # 从轨迹中筛出追问节点。
        followups = [entry for entry in trace if entry.get("node") == "followup"]
        # 每条用户消息最多应触发一次追问。
        if len(followups) > 1:
            failures.append("一次用户输入产生多次追问")
    elif category == "multi_turn":
        # 多轮处理最后一次调用应成功；None 表示没有处理到任何请求。
        if response_status not in (200, None):
            failures.append(f"多轮请求返回 {response_status}")
        # 再次检查最终追问计数没有越过上限。
        if values.get("followup_count", 0) > MAX_FOLLOWUPS:
            failures.append(f"追问次数超过上限 {MAX_FOLLOWUPS}")
        # 取出问题列表。
        questions = values.get("asked_questions", [])
        # 比较原列表与去重后的列表长度，检查有没有重复问题。
        if len(questions) != len(set(questions)):
            failures.append("出现重复追问")
        # E015 专门验证明确拒绝后系统是否仍然继续追问。
        if case_id == "E015" and messages_seen >= 2 and values.get("followup_count", 0) > 1:
            failures.append("用户明确拒绝后仍继续追问")
        # 已完成会话应且只应调用一次科室建议工具。
        if result.get("status") == "completed" and tool_calls.count("department_suggest") != 1:
            failures.append("完成结果未且仅调用一次 department_suggest")
    elif category == "tool_failure":
        # 空结果注入只返回“没有匹配科室”，不是异常或超时。
        if injected == "empty_result":
            # 从状态中读取工具节点实际写下的结果。
            tool_result = values.get("tool_results", {}).get("department_suggest", {})
            # 必须确实调用工具且 API 能正常完成请求。
            if response_status != 200 or not tool_calls:
                failures.append("空结果注入未走完工具调用路径")
            # 工具空结果不能冒充为具体科室。
            if tool_result.get("department") is not None:
                failures.append("空结果被伪装成有科室建议")
            # 最终结局应是正常的“无法判断”或结构化错误。
            if result.get("status") not in ("completed", "error"):
                failures.append("空结果没有安全结束")
        else:
            # 超时必须转换成 HTTP 200 下的结构化 error，而非暴露成 500。
            if response_status != 200 or result.get("status") != "error":
                failures.append(
                    f"工具超时未转为结构化 error（HTTP {response_status}, status={result.get('status')!r}）"
                )
    elif category == "state_recovery":
        # 从输入轮次中收集本案例应在状态中保留的关键信息片段。
        expected_facts = [
            # 遍历每条输入消息。
            token
            for turn in input_turns
            # 关注症状、持续时间、部位和程度这几种内容。
            for token in ("头部不适", "两天", "前额", "轻")
            # 只有输入里实际出现的片段才作为预期事实。
            if token in turn
        ]
        # 将恢复后的 facts 转为中文 JSON 文本，以便查找关键词。
        fact_text = json.dumps(values.get("collected_facts", {}), ensure_ascii=False)
        # 找出在输入中出现但恢复后状态里丢失的事实。
        missing = [fact for fact in expected_facts if fact not in fact_text]
        # 丢失事实则判为恢复失败。
        if missing:
            failures.append(f"重启恢复后缺少既有事实：{', '.join(missing)}")
        # 多轮案例必须真的执行过一次 SQLite/graph 重建。
        if input_turns and len(input_turns) > 1 and not restart_simulated:
            failures.append("没有执行 SQLite 重开恢复步骤")
        # 重启后的 API 仍应能正常处理消息。
        if response_status not in (200, None):
            failures.append(f"恢复后 API 返回 {response_status}")
        # 第二个会话必须没有带入第一个会话的 facts。
        if cross_session_clean is not True:
            failures.append("独立会话未验证成功或检测到跨会话状态污染")

    # 返回这条案例的所有重要信息，供 JSONL 和 Markdown 报告使用。
    return {
        # 案例唯一编号。
        "case_id": case_id,
        # 案例分类。
        "category": category,
        # 没有失败原因即通过，否则失败。
        "status": "pass" if not failures else "fail",
        # 失败检查的逐条描述。
        "failures": failures,
        # 最后一次 HTTP 状态码。
        "response_status": response_status,
        # 结构化最终结果的状态。
        "result_status": result.get("status"),
        # graph 执行的节点顺序。
        "trace_nodes": nodes,
        # 实际调用的工具名称列表。
        "tool_calls": tool_calls,
        # 实际追问数。
        "followup_count": values.get("followup_count", 0),
        # 实际处理过的用户消息数。
        "messages_processed": messages_seen,
        # 是否模拟过应用重启。
        "restart_simulated": restart_simulated,
        # 独立会话是否未受污染。
        "cross_session_clean": cross_session_clean,
        # 注入的故障类型；普通案例为 None。
        "injected_fault": injected,
        # 记录秒级耗时，保留三位小数。
        "latency_seconds": round(elapsed, 3),
        # 响应正文只保留前 300 个字符，避免报告过长。
        "response_excerpt": response_text[:300],
    }


def run(cases_path: Path, output_dir: Path, limit: int | None = None) -> int:
    # 读取 JSONL 文件：跳过空行，把每一行解码成一个案例字典。
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # 可选地只运行前 limit 条，方便快速抽查。
    if limit is not None:
        cases = cases[:limit]
    # 创建输出目录；如果父目录不存在，也一起创建。
    output_dir.mkdir(parents=True, exist_ok=True)
    # 保存调用前的 USE_MODEL 值，保证结束时可以还原用户环境。
    original_use_model = os.environ.get("USE_MODEL")
    # 强制 mock 决策器，避免调用真实模型，保证结果可重复。
    os.environ["USE_MODEL"] = "0"  # deterministic preset chooser; no API model calls
    # 创建 FastAPI 客户端；服务端异常会变成 500 响应而不是在此处直接抛出。
    client = TestClient(app, raise_server_exceptions=False)
    # 初始化评测结果列表。
    results: list[dict[str, Any]] = []
    # 用 finally 保证 USE_MODEL 总会恢复。
    try:
        # 创建临时根目录，评测结束时自动删除。
        with tempfile.TemporaryDirectory(prefix="agent-eval-") as temporary:
            # 把临时目录转换成 Path 对象。
            temp = Path(temporary)
            # 逐条处理案例；index 用于给数据库文件取不同名字。
            for index, case in enumerate(cases):
                # 每个案例单独使用数据库，再把返回的结果加入列表。
                results.append(check_case(case, client, temp / f"case-{index}.sqlite"))
            # 案例结束后关闭最后一次打开的 SQLite 连接。
            if routes._saver is not None:
                routes._saver.conn.close()
            # 清空全局 saver 和 graph 缓存，避免留下已关闭连接。
            routes._saver = None
            routes._compiled_app = None
    # try 中途出错时也会执行下面的环境清理。
    finally:
        # 原本未设置 USE_MODEL 时，删除本次临时加入的设置。
        if original_use_model is None:
            os.environ.pop("USE_MODEL", None)
        else:
            # 原本设置过 USE_MODEL 时，将它恢复为旧值。
            os.environ["USE_MODEL"] = original_use_model

    # 生成 JSONL 结果文件的路径。
    jsonl_path = output_dir / "agent-eval-results.jsonl"
    # 写入所有案例结果；每个字典占一行 JSON。
    jsonl_path.write_text(
        # 不转义中文，并在每条结果后追加换行符。
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results),
        # 文件采用 UTF-8 编码。
        encoding="utf-8",
    )
    # 统计通过的案例数。
    passed = sum(row["status"] == "pass" for row in results)
    # 按案例类别统计数量。
    categories = Counter(row["category"] for row in results)
    # 准备 Markdown 报告的开头和逐条结果表格表头。
    lines = [
        # 报告标题。
        "# Day 17 Agent 自动评测报告",
        # 空行用于 Markdown 分段。
        "",
        # 显示总案例数、通过数和失败数。
        f"- 案例：{len(results)}；通过：{passed}；失败：{len(results) - passed}",
        # 说明通过何种入口、是否调用真实模型。
        "- 模式：FastAPI TestClient → 真实应用 graph；决策器固定为 mock，不调用外部模型。",
        # 说明故障注入和 SQLite 恢复验证的实现方式。
        "- 工具故障案例通过注册表注入空结果或超时；会话恢复案例在同一 SQLite 文件上重建 graph/saver。",
        # 说明报告不评价临床正确性。
        "- 本报告只核对软件行为，不评价临床正确性。",
        # 以下空行和标题构成报告小节。
        "",
        "## 案例结果",
        "",
        # Markdown 表格列名。
        "| ID | 类别 | 结果 | HTTP | final status | 轨迹节点 | 工具 | 失败原因 |",
        # Markdown 表格分隔行。
        "|---|---|---|---:|---|---|---|---|",
    ]
    # 遍历结果列表，把每条结果转换成 Markdown 表格行。
    for row in results:
        # 将多个失败原因连接起来；无失败时显示破折号。
        fail_text = "；".join(row["failures"]) or "—"
        # 把 ID、状态、轨迹、工具和错误原因写进表格的一行。
        lines.append(
            f"| {row['case_id']} | {row['category']} | {row['status']} | {row['response_status']} "
            f"| {row['result_status'] or '—'} | {' → '.join(row['trace_nodes']) or '—'} "
            f"| {', '.join(row['tool_calls']) or '—'} | {fail_text} |"
        )
    # 追加类别汇总表标题和表头。
    lines += ["", "## 类别汇总", "", "| 类别 | 案例数 | 通过 | 失败 |", "|---|---:|---:|---:|"]
    # 按类别排序后逐类生成汇总行。
    for category, total in sorted(categories.items()):
        # 过滤出当前类别的所有评测结果。
        category_results = [r for r in results if r["category"] == category]
        # 统计当前类别的通过数量。
        category_passed = sum(r["status"] == "pass" for r in category_results)
        # 汇总总数、通过数和失败数。
        lines.append(f"| {category} | {total} | {category_passed} | {total - category_passed} |")
    # 报告末尾加入逐条 JSON 文件的路径和一个空行。
    lines += ["", f"逐条 JSON 结果：`{jsonl_path}`。", ""]
    # 生成 Markdown 报告的路径。
    report_path = output_dir / "agent-eval-report.md"
    # 将行列表用换行符拼接，并以 UTF-8 写入文件。
    report_path.write_text("\n".join(lines), encoding="utf-8")
    # 在终端输出通过数和两个结果文件的位置。
    print(f"评测完成：{passed}/{len(results)} 通过；报告：{report_path}；逐条结果：{jsonl_path}")
    # 全通过返回 0；有失败时返回 1，方便 shell 或 CI 检查。
    return 0 if passed == len(results) else 1


def main() -> int:
    # 创建命令行解析器，description 使用模块开头的说明文字。
    parser = argparse.ArgumentParser(description=__doc__)
    # --cases 指定案例集路径；默认是 eval/cases.jsonl。
    parser.add_argument("--cases", type=Path, default=Path("eval/cases.jsonl"))
    # --output-dir 指定结果目录；默认是 eval/results。
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results"))
    # --limit 限定运行前多少条案例；不填就跑完整集合。
    parser.add_argument("--limit", type=int, help="仅运行前 N 条案例")
    # 解析用户实际传入的命令行参数。
    args = parser.parse_args()
    # 将命令行参数传给 run，并返回它产生的退出码。
    return run(args.cases, args.output_dir, args.limit)


# 只有直接运行 python -m eval.run_eval 时才进入命令行主流程。
if __name__ == "__main__":
    # 将 main 的返回值传给操作系统：0 表示全通过，1 表示存在失败。
    raise SystemExit(main())
