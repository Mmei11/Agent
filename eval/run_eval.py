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
from backend.app.tools.contract import ToolResult

import backend.app.agent.graph as graph_module
# 声明式硬断言：每条案例自己声明要检查什么。
from eval.assertions import CaseObservation, run_hard_assertions

# 保证故障注入案例能走到工具节点的虚构完整输入。
# 故障案例的固定输入：六个必需字段都写成阳性描述，
# 保证关键词抽取和模型抽取都能走完追问、直接到达工具节点。
TOOL_FIXTURE = "头部不适两天，前额，程度轻，伴有恶心，无病史"


def reset_api(db_path: Path) -> None:
    """Close open savers and make the API open an isolated SQLite DB."""
    # 关闭进程内所有已打开的 saver；每个线程各自持有一份连接。
    routes.reset_app()
    # 设置本案例专用数据库路径，避免不同案例互相污染。
    os.environ["CONSULTATION_DB_PATH"] = str(db_path)


def state_for(session_id: str) -> dict[str, Any]:
    # 获取 API 当前使用的已编译 graph。
    graph = routes.get_app()
    # 用 thread_id（这里就是 session_id）从 checkpointer 读取状态快照。
    snapshot = graph.get_state({"configurable": {"thread_id": session_id}})
    # 返回快照里的 values；若为空则返回空字典。
    return dict(snapshot.values or {})


def check_case(
    case: dict[str, Any],
    client: TestClient,
    db_path: Path,
    *,
    with_state: bool = False,
) -> dict[str, Any]:
    # 取案例 ID，例如 E001。
    case_id = case["id"]
    # 取案例分类，决定采用哪组断言。
    category = case["category"]
    # 备份真实工具入口，后面可能临时替换，结束时需要还原。
    original_tool = graph_module.call_tool
    # 记录注入故障类型；普通案例没有故障，所以初始为 None。
    injected: str | None = None
    # 复制原始输入轮次，后面对故障案例会改成工具可达的输入。
    input_turns = list(case["input_turns"])

    # 故障案例需要经过真实 graph 到达工具节点，所以改用信息完整的虚构输入，
    # 然后在工具入口这一边界注入故障行为。
    if category == "tool_failure":
        # 让决策器不必先追问，直接到工具节点。
        input_turns = [TOOL_FIXTURE]
        # E030 注入“工具成功返回，但没有匹配结果”的情况。
        if case_id == "E030":
            # 标记注入的是空结果。
            injected = "empty_result"
            # 临时替换工具：明确返回 department=None，而不编造科室。
            graph_module.call_tool = lambda name, params: ToolResult(
                ok=True,
                name=name,
                data={"department": None, "reason": "injected_empty_result", "hits": []},
            )
        else:
            # E031 注入超时错误。
            injected = "timeout"
            # 直接返回超时回执，等价于契约层判定超时。
            graph_module.call_tool = lambda name, params: ToolResult(
                ok=False, name=name, error="执行超过 8.0s", error_kind="timeout"
            )

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
        # 恢复真实的工具入口，避免注入影响后续案例或程序。
        graph_module.call_tool = original_tool

    # 计算本案例的总耗时。
    elapsed = time.perf_counter() - started
    # 从状态中读取累积执行轨迹，没有轨迹时用空列表。
    trace = values.get("trace", [])
    # 提取每个轨迹事件中的节点名，保留其执行顺序。
    nodes = [entry.get("node") for entry in trace]
    # 决策轮的明细：模型选了什么、最终执行什么、置信度多少。
    decisions = [entry for entry in trace if entry.get("node") == "decision"]
    # 只从 tool 节点提取被调用的工具名。
    tool_calls = [entry.get("called") for entry in trace if entry.get("node") == "tool"]
    # decision 节点的用量记录在 model_usage；intake 节点的模型抽取记录在 usage。
    decision_usage = [
        entry.get("model_usage") or {}
        for entry in trace
        if entry.get("node") == "decision"
    ]
    intake_usage = [
        entry.get("usage") or {}
        for entry in trace
        if entry.get("node") == "intake" and entry.get("usage")
    ]
    usage_entries = decision_usage + intake_usage
    # 成功调用：决策器 mode=model 且未回退，加上真正走模型的 intake。
    model_calls = sum(
        1 for u in decision_usage if u.get("mode") == "model" and not u.get("fallback")
    )
    model_calls += sum(
        1
        for entry in trace
        if entry.get("node") == "intake" and entry.get("extractor") == "llm"
    )
    # 回退次数：决策回退 + intake 回退。
    fallback_calls = sum(1 for u in decision_usage if u.get("fallback"))
    fallback_calls += sum(
        1 for entry in trace if entry.get("node") == "intake" and entry.get("fallback")
    )
    # 整个会话（多轮累计）的 token 用量。
    input_tokens = sum(u.get("input_tokens") or 0 for u in usage_entries)
    output_tokens = sum(u.get("output_tokens") or 0 for u in usage_entries)
    # 只有配置了单价才有成本估算。
    known_costs = [u["cost_estimate"] for u in usage_entries if u.get("cost_estimate") is not None]
    cost_estimate = round(sum(known_costs), 6) if known_costs else None
    # 读取结构化最终结果；尚未结束的会话这里为空字典。
    result = values.get("final_result", {})
    # 断言层需要的全部观测值。
    observation = CaseObservation(
        case=case,
        response_status=response_status,
        nodes=nodes,
        tool_calls=tool_calls,
        result=result,
        values=values,
        trace=trace,
        messages_seen=messages_seen,
        restart_simulated=restart_simulated,
        cross_session_clean=cross_session_clean,
        injected=injected,
    )
    # 全局断言 + 案例声明断言；失败原因带断言类型，便于定位。
    failures = run_hard_assertions(case, observation)

    # 返回这条案例的所有重要信息，供 JSONL 和 Markdown 报告使用。
    row = {
        # 案例唯一编号。
        "case_id": case_id,
        # 分层：dev 用于日常调试，holdout 只在发版前跑。
        "split": case.get("split", "dev"),
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
        # 本会话的模型调用次数、token 与成本，用于 M4 指标。
        "model_calls": model_calls,
        "fallback_calls": fallback_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_estimate": cost_estimate,
        # 决策质量观测：轮次、与模型的最终一致数、低置信度降级数、置信度分布。
        "decision_rounds": len(decisions),
        "decision_same": sum(1 for d in decisions if d.get("same")),
        "decision_downgraded": sum(
            1 for d in decisions if d.get("downgraded_by_confidence")
        ),
        "decision_confidences": [
            d["confidence"] for d in decisions if d.get("confidence") is not None
        ],
        # 响应正文只保留前 300 个字符，避免报告过长。
        "response_excerpt": response_text[:300],
    }
    # 重复采样需要把完整状态交给 judge；默认不返回，避免结果文件膨胀。
    if with_state:
        row["_state"] = values
    return row


def run(
    cases_path: Path,
    output_dir: Path,
    limit: int | None = None,
    use_model: bool = False,
    split: str = "all",
    case_ids: list[str] | None = None,
) -> int:
    # 读取 JSONL 文件：跳过空行，把每一行解码成一个案例字典。
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # 只跑指定案例 ID，便于单条调试和破坏测试。
    if case_ids:
        wanted = set(case_ids)
        cases = [case for case in cases if case["id"] in wanted]
    # 分层过滤：dev 日常跑，holdout 只在发版前跑。
    if split != "all":
        cases = [case for case in cases if case.get("split", "dev") == split]
    # 可选地只运行前 limit 条，方便快速抽查。
    if limit is not None:
        cases = cases[:limit]
    # 创建输出目录；如果父目录不存在，也一起创建。
    output_dir.mkdir(parents=True, exist_ok=True)
    # 保存调用前的 USE_MODEL 值，保证结束时可以还原用户环境。
    original_use_model = os.environ.get("USE_MODEL")
    # 评测是离线测量，不应被进程内限流打断；限流本身由 test_ratelimit.py 覆盖。
    original_rate_limit = os.environ.get("RATE_LIMIT_ENABLED")
    os.environ["RATE_LIMIT_ENABLED"] = "0"
    # 默认强制 mock 决策器；传 --use-model 时才调用真实模型。
    os.environ["USE_MODEL"] = "1" if use_model else "0"
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
            # 案例结束后关闭所有线程打开的 SQLite 连接。
            routes.reset_app()
    # try 中途出错时也会执行下面的环境清理。
    finally:
        # 原本未设置 USE_MODEL 时，删除本次临时加入的设置。
        if original_use_model is None:
            os.environ.pop("USE_MODEL", None)
        else:
            # 原本设置过 USE_MODEL 时，将它恢复为旧值。
            os.environ["USE_MODEL"] = original_use_model
        if original_rate_limit is None:
            os.environ.pop("RATE_LIMIT_ENABLED", None)
        else:
            os.environ["RATE_LIMIT_ENABLED"] = original_rate_limit

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
        "# Agent 自动评测报告",
        # 空行用于 Markdown 分段。
        "",
        # 显示总案例数、通过数和失败数。
        f"- 案例：{len(results)}；通过：{passed}；失败：{len(results) - passed}",
        # 说明本次跑的是哪一层。
        f"- 分层：{split}",
        # 说明通过何种入口、用的是模型还是预设决策器。
        "- 模式：FastAPI TestClient → 真实应用 graph；决策器为"
        + ("真实模型（USE_MODEL=1），会调用外部 API。" if use_model else "确定性 mock，不调用外部模型。"),
        # 说明故障注入和 SQLite 恢复验证的实现方式。
        "- 工具故障案例通过注册表注入空结果或超时；会话恢复案例在同一 SQLite 文件上重建 graph/saver。",
        # 说明报告不评价临床正确性。
        "- 本报告只核对软件行为，不评价临床正确性。",
        # 以下空行和标题构成报告小节。
        "",
        "## 案例结果",
        "",
        # Markdown 表格列名。
        "| ID | 分层 | 类别 | 结果 | HTTP | final status | 轨迹节点 | 工具 | 失败原因 |",
        # Markdown 表格分隔行。
        "|---|---|---|---|---:|---|---|---|---|",
    ]
    # 遍历结果列表，把每条结果转换成 Markdown 表格行。
    for row in results:
        # 将多个失败原因连接起来；无失败时显示破折号。
        fail_text = "；".join(row["failures"]) or "—"
        # 把 ID、分层、状态、轨迹、工具和错误原因写进表格的一行。
        lines.append(
            f"| {row['case_id']} | {row['split']} | {row['category']} | {row['status']} | {row['response_status']} "
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
    # 追加分层汇总表：dev 与 holdout 分开看。
    splits = Counter(row["split"] for row in results)
    lines += ["", "## 分层汇总", "", "| 分层 | 案例数 | 通过 | 失败 |", "|---|---:|---:|---:|"]
    for split_name, total in sorted(splits.items()):
        split_results = [r for r in results if r["split"] == split_name]
        split_passed = sum(r["status"] == "pass" for r in split_results)
        lines.append(f"| {split_name} | {total} | {split_passed} | {total - split_passed} |")
    # 汇总模型用量（M4 指标）。
    total_calls = sum(r["model_calls"] for r in results)
    total_fallbacks = sum(r["fallback_calls"] for r in results)
    total_input = sum(r["input_tokens"] for r in results)
    total_output = sum(r["output_tokens"] for r in results)
    known_costs_all = [r["cost_estimate"] for r in results if r["cost_estimate"] is not None]
    total_cost = round(sum(known_costs_all), 6) if known_costs_all else None
    latencies = sorted(r["latency_seconds"] for r in results)
    # 决策质量汇总：一致率看模型动作有没有被程序改掉，降级数看置信度闸门触发几次。
    total_decisions = sum(r["decision_rounds"] for r in results)
    same_decisions = sum(r["decision_same"] for r in results)
    downgraded_decisions = sum(r["decision_downgraded"] for r in results)
    all_confidences = [c for r in results for c in r["decision_confidences"]]
    avg_confidence = (
        round(sum(all_confidences) / len(all_confidences), 3) if all_confidences else None
    )

    def _pct(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        k = (len(values) - 1) * q
        lo = int(k)
        hi = min(lo + 1, len(values) - 1)
        return round(values[lo] + (values[hi] - values[lo]) * (k - lo), 3)

    lines += [
        "",
        "## 用量与延迟",
        "",
        "| 指标 | 值 |",
        "|---|---:|",
        f"| 决策器 | {'model' if use_model else 'mock'} |",
        f"| 模型调用次数 | {total_calls} |",
        f"| 回退预设次数 | {total_fallbacks} |",
        f"| input tokens | {total_input} |",
        f"| output tokens | {total_output} |",
        f"| 成本估算 | {('¥' + str(total_cost)) if total_cost is not None else '未配置单价'} |",
        f"| 延迟 p50 / p95 / max（秒） | {_pct(latencies, 0.5)} / {_pct(latencies, 0.95)} / {max(latencies) if latencies else 0} |",
        f"| 决策轮次 / 最终动作与模型一致 | {total_decisions} / {same_decisions} |",
        f"| 低置信度降级次数 | {downgraded_decisions} |",
        f"| 平均置信度 | {avg_confidence if avg_confidence is not None else '—'} |",
    ]

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
    parser.add_argument(
        "--use-model",
        action="store_true",
        help="用真实模型做决策（会调用外部 API 并产生费用）；不加则用确定性 mock",
    )
    parser.add_argument(
        "--split",
        choices=["all", "dev", "holdout"],
        default="all",
        help="只跑某一层：dev 日常用，holdout 只在发版前跑",
    )
    parser.add_argument(
        "--case",
        dest="case_ids",
        help="只跑指定案例 ID，逗号分隔，例如 E001,E030",
    )
    # 解析用户实际传入的命令行参数。
    args = parser.parse_args()
    case_ids = (
        [item.strip() for item in args.case_ids.split(",") if item.strip()]
        if args.case_ids
        else None
    )
    # 将命令行参数传给 run，并返回它产生的退出码。
    return run(
        args.cases,
        args.output_dir,
        args.limit,
        args.use_model,
        split=args.split,
        case_ids=case_ids,
    )


# 只有直接运行 python -m eval.run_eval 时才进入命令行主流程。
if __name__ == "__main__":
    # 将 main 的返回值传给操作系统：0 表示全通过，1 表示存在失败。
    raise SystemExit(main())
