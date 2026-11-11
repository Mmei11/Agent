"""Phase A · M6：上下文保持率。

要回答的问题不是“模型聪不聪明”，而是“跨了几轮之后，系统还记不记得”。

样本分两类，因为它们的失败方式不同：
- fact：进得了结构化字段的事实（部位、持续时间）。当前实现把 collected_facts
  全量传给回答节点，所以这类通常不会丢；它用来防“字段被覆盖/丢失”的回归。
- conversation：进不了结构化字段的对话细节（出差安排、偏好、交通方式）。
  这类只能靠最近对话窗口或记忆卡，是 Phase B 要补的核心场景。

每条样本测量四层：
- state：期望值是否还在 collected_facts 里
- window：期望值是否在最近 N 条对话里
- prompt：期望值是否出现在回答节点实际拿到的上下文里
- answer：期望值是否出现在最终回答里

运行：
    uv run python -m eval.context_retention
    uv run python -m eval.context_retention --use-model
    uv run python -m eval.context_retention --split holdout
"""

from __future__ import annotations

import os

# 必须在导入后端模块之前设置：graph.py 在导入时决定用真实检索还是 stub。
os.environ.setdefault("KNOWLEDGE_BACKEND", "stub")
os.environ["RATE_LIMIT_ENABLED"] = "0"
os.environ["AUTH_MODE"] = "off"

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.agent.answer import RECENT_CONVERSATION_ITEMS, build_context
from backend.app.agent.state import ConsultationState
from backend.app.api import routes
from backend.app.api.routes import app

DEFAULT_CASES = Path("eval/probes/context_retention.jsonl")
DEFAULT_OUTPUT = Path("eval/results/context-retention")

# 模型模式下，首轮可能还想追问。给它一次补充机会，确保样本进入“已出结果”状态。
SETUP_NUDGE = "没有其他不适，无病史，请直接整理结果。"


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reset_api(db_path: Path) -> None:
    """每个样本独立数据库，避免会话状态互相污染。"""
    routes.reset_app()
    os.environ["CONSULTATION_DB_PATH"] = str(db_path)


def state_for(session_id: str) -> dict[str, Any]:
    graph = routes.get_app()
    snapshot = graph.get_state({"configurable": {"thread_id": session_id}})
    return dict(snapshot.values or {})


def send(client: TestClient, session_id: str, message: str) -> tuple[int, str]:
    response = client.post(f"/sessions/{session_id}/messages", json={"message": message})
    return response.status_code, response.json().get("rendered_text", "") if response.status_code == 200 else ""


def contains_all(text: str, values: list[str]) -> bool:
    """精确子串匹配，不做同义改写判断。

    这样做的好处是可复现、零成本；代价是模型换个说法就判 0。
    样本里已经避开同义词，等指标稳定后再考虑加 judge。
    """
    return all(str(value) in text for value in values)


# 这些 source 来自 answer 节点，说明本轮真正使用了带上下文的回答器。
# general / router 是单路由直接回答，不经过 answer 的 build_context。
CONTEXT_SOURCES = {"model", "mock", "fallback"}

# 明确表示“不记得/看不到”的回答，即使澄清问句里碰巧出现了期望值，也算失败。
# 例：“你之前没有提到过，可以说上午、下午还是晚上吗？”——不能因为含“上午”就判命中。
NO_RECALL_MARKERS = (
    "没有提到",
    "没提到",
    "没有说过",
    "没说过",
    "无法查看",
    "不记得",
    "没有记录",
    "未记录",
)


def denies_recall(text: str) -> bool:
    return any(marker in text for marker in NO_RECALL_MARKERS)


def answer_path(values: dict[str, Any]) -> tuple[str, bool]:
    """从 state.trace 里取最后一次 answer 节点的来源，判断是否走了上下文路径。"""
    events = [entry for entry in values.get("trace", []) if entry.get("node") == "answer"]
    last = events[-1] if events else {}
    source = str(last.get("source") or last.get("general_answer_source") or "")
    return source, source in CONTEXT_SOURCES


def evaluate(
    case: dict[str, Any],
    values: dict[str, Any],
    answer_text: str,
    *,
    context_values: dict[str, Any] | None = None,
) -> dict[str, bool]:
    # window / prompt 必须用“探针回答之前”的状态。
    # 否则探针自己的回答会被算进最近窗口，出现自我污染。
    context_state = context_values or values
    expected = case["expected_values"]
    facts_text = json.dumps(values.get("collected_facts", {}), ensure_ascii=False)
    conversation = context_state.get("conversation", [])
    window_text = json.dumps(conversation[-RECENT_CONVERSATION_ITEMS:], ensure_ascii=False)

    try:
        context = build_context(ConsultationState(**context_state))
        prompt_text = json.dumps(context, ensure_ascii=False)
    except Exception:
        # 状态结构变化时不让整个评测崩掉；prompt 层记为未命中。
        prompt_text = ""

    state_hit = contains_all(facts_text, expected)
    recall_denied = denies_recall(answer_text)
    return {
        "state_hit": state_hit,
        "window_hit": contains_all(window_text, expected),
        "prompt_hit": contains_all(prompt_text, expected),
        # 明确否认回忆时，即使子串碰巧命中，也判失败。
        "answer_hit": contains_all(answer_text, expected) and not recall_denied,
        "recall_denied": recall_denied,
        # conversation 类样本本不该进结构化字段；命中说明抽取器“泄漏”了。
        "fact_leak": (not case["expect_in_facts"]) and state_hit,
    }


def run_case(case: dict[str, Any], client: TestClient, db_path: Path) -> dict[str, Any]:
    reset_api(db_path)
    started = time.perf_counter()
    session_id: str | None = None
    values: dict[str, Any] = {}
    probe_state: dict[str, Any] = {}
    answer_text = ""
    setup_finalized = False
    status_ok = True
    error = ""

    try:
        with client:
            setup_turns = list(case.get("setup_turns") or [""])
            response = client.post("/sessions", json={"initial_message": setup_turns[0]})
            if response.status_code != 200:
                return _failed_row(case, f"创建会话失败：HTTP {response.status_code}", started)

            session_id = response.json()["session_id"]
            values = state_for(session_id)

            for message in setup_turns[1:]:
                status, answer_text = send(client, session_id, message)
                if status != 200:
                    return _failed_row(case, f"初始轮失败：HTTP {status}", started)
                values = state_for(session_id)

            # 模型模式可能还缺字段；最多补两次，避免样本没出结果就进入细节轮。
            nudges = 0
            while not values.get("final_result") and nudges < 2:
                nudges += 1
                status, answer_text = send(client, session_id, SETUP_NUDGE)
                if status != 200:
                    return _failed_row(case, f"收束轮失败：HTTP {status}", started)
                values = state_for(session_id)

            setup_finalized = bool(values.get("final_result"))

            for message in case.get("turns", []):
                status, answer_text = send(client, session_id, message)
                if status != 200:
                    status_ok = False
                    error = f"细节轮失败：HTTP {status}"
                    break
                values = state_for(session_id)

            if status_ok:
                # 先拍快照，再发探针：answer 节点看到的是“历史 + 这条探针”，
                # 不包含探针自己的回答。
                probe_state = state_for(session_id)
                status, answer_text = send(client, session_id, case["probe_turn"])
                if status != 200:
                    status_ok = False
                    error = f"探针轮失败：HTTP {status}"
                else:
                    values = state_for(session_id)
    except Exception as exc:  # 评测器本身出错时保留样本 ID，方便定位
        return _failed_row(case, f"{type(exc).__name__}: {exc}", started)

    context_values = None
    if probe_state:
        context_values = dict(probe_state)
        context_values["conversation"] = list(probe_state.get("conversation", [])) + [
            {"role": "user", "text": case["probe_turn"]}
        ]
        context_values["latest_user_message"] = case["probe_turn"]

    hits = evaluate(case, values, answer_text, context_values=context_values)
    answer_source, context_used = answer_path(values)
    return {
        "id": case["id"],
        "split": case.get("split", "dev"),
        "kind": case["kind"],
        "distance": case["distance"],
        "expected": case["expected_values"],
        "valid": bool(setup_finalized and status_ok),
        "setup_finalized": setup_finalized,
        "answer_source": answer_source,
        "context_used": context_used,
        "probe_answer": answer_text[:300],
        "latency_seconds": round(time.perf_counter() - started, 3),
        "error": error,
        **hits,
    }


def _failed_row(case: dict[str, Any], error: str, started: float) -> dict[str, Any]:
    return {
        "id": case["id"],
        "split": case.get("split", "dev"),
        "kind": case["kind"],
        "distance": case["distance"],
        "expected": case["expected_values"],
        "valid": False,
        "setup_finalized": False,
        "answer_source": "",
        "context_used": False,
        "probe_answer": "",
        "latency_seconds": round(time.perf_counter() - started, 3),
        "state_hit": False,
        "window_hit": False,
        "prompt_hit": False,
        "answer_hit": False,
        "recall_denied": False,
        "fact_leak": False,
        "error": error,
    }


def rate(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return round(sum(1 for row in rows if row[key]) / len(rows), 3)


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = [row for row in rows if row["valid"]]
    groups: list[tuple[str, list[dict[str, Any]]]] = [("全部", valid)]
    for kind in ("fact", "conversation"):
        groups.append((f"{kind} 全部", [r for r in valid if r["kind"] == kind]))
        for distance in ("near", "far"):
            groups.append(
                (
                    f"{kind} / {distance}",
                    [r for r in valid if r["kind"] == kind and r["distance"] == distance],
                )
            )

    summary = []
    for name, subset in groups:
        # answer 只统计真正经过 answer 节点的样本；被 router 直接回答的另记 bypass。
        context_rows = [row for row in subset if row["context_used"]]
        summary.append(
            {
                "group": name,
                "n": len(subset),
                "state": rate(subset, "state_hit"),
                "window": rate(subset, "window_hit"),
                "prompt": rate(subset, "prompt_hit"),
                "answer": rate(context_rows, "answer_hit"),
                "bypass": len(subset) - len(context_rows),
                "denied": sum(1 for row in subset if row.get("recall_denied")),
                "leak": sum(1 for row in subset if row["fact_leak"]),
            }
        )
    return summary


def _cell(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def write_report(
    rows: list[dict[str, Any]], summary: list[dict[str, Any]], output_dir: Path, *, use_model: bool
) -> Path:
    valid = sum(1 for row in rows if row["valid"])
    lines = [
        "# M6 上下文保持率报告",
        "",
        f"- 模式：{'真实模型（USE_MODEL=1，会产生费用）' if use_model else '确定性 mock，不调用外部模型'}",
        f"- 样本：{len(rows)}；有效：{valid}；无效：{len(rows) - valid}",
        f"- 最近对话窗口：{RECENT_CONVERSATION_ITEMS} 条（用户/助手各算一条）",
        "- 匹配方式：期望值必须在文本中逐字出现；不做同义判定。",
        "",
        "## 汇总",
        "",
        "| 组别 | 样本 | state | window | prompt | answer | bypass | 否认回忆 | fact 泄漏 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['group']} | {row['n']} | {_cell(row['state'])} | {_cell(row['window'])} "
            f"| {_cell(row['prompt'])} | {_cell(row['answer'])} | {row['bypass']} "
            f"| {row['denied']} | {row['leak']} |"
        )

    lines += [
        "",
        "## 逐条结果",
        "",
        "| ID | 类型 | 距离 | 路径 | state | window | prompt | answer | 答案摘录 |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        excerpt = (row["probe_answer"] or "").replace("\n", " ")[:60]
        path = row["answer_source"] or "—"
        lines.append(
            f"| {row['id']} | {row['kind']} | {row['distance']} | {path} | "
            f"{'✓' if row['state_hit'] else '✗'} | {'✓' if row['window_hit'] else '✗'} | "
            f"{'✓' if row['prompt_hit'] else '✗'} | {'✓' if row['answer_hit'] else '✗'} | {excerpt} |"
        )

    lines += [
        "",
        "## 怎么读",
        "",
        "- `state`：事实类看“有没有保留”；对话类看“有没有意外泄漏进结构化字段”。",
        "- `window` / `prompt`：回答节点在探针轮实际能看到什么。",
        "- `answer`：只在真正经过 answer 节点的样本上统计；mock 模式下这一列不作为质量结论。",
        "- `bypass`：探针被单路由直接回答、没走 answer 节点的数量。被 bypass 的样本无法证明“记不住”，",
        "  只能说明“没走记忆路径”，所以不进入 answer 分母。",
        "- `否认回忆`：回答里出现“没有提到/无法查看/不记得”等表述的次数。这类回答即使澄清问句里",
        "  碰巧出现期望值，也判为失败。",
        "- `fact / far` 低说明事实合并或检查点恢复有问题；`conversation / far` 低是预期结果，",
        "  也是 Phase B 会话记忆要解决的直接证据。",
        "",
        "## 局限",
        "",
        "- 精确子串匹配会惩罚合理改写；样本已避开同义词，但模型模式仍可能有格式差异。",
        "- 样本是虚构对话，不代表真实用户分布；它衡量相对变化，不衡量临床能力。",
        "- 模型模式有随机性，单次结果不能当作稳定结论；阶段末至少重跑 3 次。",
    ]

    report_path = output_dir / "context-retention-report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def run(
    cases_path: Path,
    output_dir: Path,
    *,
    limit: int | None = None,
    use_model: bool = False,
    split: str = "dev",
    case_ids: list[str] | None = None,
) -> int:
    cases = load_cases(cases_path)
    if split != "all":
        cases = [case for case in cases if case.get("split", "dev") == split]
    if case_ids:
        wanted = set(case_ids)
        cases = [case for case in cases if case["id"] in wanted]
    if limit is not None:
        cases = cases[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)
    original_use_model = os.environ.get("USE_MODEL")
    original_db = os.environ.get("CONSULTATION_DB_PATH")
    os.environ["USE_MODEL"] = "1" if use_model else "0"
    client = TestClient(app, raise_server_exceptions=False)

    rows: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="context-retention-") as temporary:
            temp = Path(temporary)
            for index, case in enumerate(cases):
                rows.append(run_case(case, client, temp / f"case-{index}.sqlite"))
            routes.reset_app()
    finally:
        if original_use_model is None:
            os.environ.pop("USE_MODEL", None)
        else:
            os.environ["USE_MODEL"] = original_use_model
        if original_db is None:
            os.environ.pop("CONSULTATION_DB_PATH", None)
        else:
            os.environ["CONSULTATION_DB_PATH"] = original_db

    results_path = output_dir / "context-retention-results.jsonl"
    results_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    summary = summarize(rows)
    report_path = write_report(rows, summary, output_dir, use_model=use_model)

    print(f"M6 完成：{len(rows)} 条样本；报告：{report_path}；逐条结果：{results_path}")
    for row in summary:
        print(
            f"  {row['group']}: n={row['n']} "
            f"state={_cell(row['state'])} window={_cell(row['window'])} "
            f"prompt={_cell(row['prompt'])} answer={_cell(row['answer'])} "
            f"bypass={row['bypass']} denied={row['denied']} leak={row['leak']}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, help="仅运行前 N 条样本")
    parser.add_argument("--case", dest="case_ids", help="只跑指定样本 ID，逗号分隔，例如 CR01,CR06")
    parser.add_argument("--use-model", action="store_true", help="用真实模型；不加则用 mock")
    parser.add_argument(
        "--split",
        choices=["all", "dev", "holdout"],
        default="dev",
        help="默认只跑 dev；holdout 留到阶段末",
    )
    args = parser.parse_args()
    case_ids = (
        [item.strip() for item in args.case_ids.split(",") if item.strip()]
        if args.case_ids
        else None
    )
    return run(
        args.cases,
        args.output_dir,
        limit=args.limit,
        use_model=args.use_model,
        split=args.split,
        case_ids=case_ids,
    )


if __name__ == "__main__":
    raise SystemExit(main())
