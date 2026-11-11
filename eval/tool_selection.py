"""Phase A · M7：工具选择正确率。

当前图的 tool 节点写死只调用 `department_route`，决策 schema 也没有
`tool_name` / `tool_args`。所以 M7 在 Phase C 之前主要回答两个问题：

1. 现有唯一可用的工具，在哪些场景被正确调用？
2. 另外两个已声明但未接入图的工具（guideline_retrieve / red_flag_lookup），
   有多少场景本来需要它们却不可达？

指标不把“红旗由规则层短路”算成错误：risk 类样本的期望工具是 null。

运行：
    uv run python -m eval.tool_selection
    uv run python -m eval.tool_selection --use-model
    uv run python -m eval.tool_selection --case TS04,TS05
"""

from __future__ import annotations

import os

os.environ.setdefault("KNOWLEDGE_BACKEND", "stub")
os.environ["RATE_LIMIT_ENABLED"] = "0"
os.environ["AUTH_MODE"] = "off"

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app

DEFAULT_CASES = Path("eval/probes/tool_selection.jsonl")
DEFAULT_OUTPUT = Path("eval/results/tool-selection")


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reset_api(db_path: Path) -> None:
    routes.reset_app()
    os.environ["CONSULTATION_DB_PATH"] = str(db_path)


def state_for(session_id: str) -> dict[str, Any]:
    graph = routes.get_app()
    snapshot = graph.get_state({"configurable": {"thread_id": session_id}})
    return dict(snapshot.values or {})


def outcome_of(values: dict[str, Any]) -> str:
    """把一轮结束后的状态归一成结局类型。"""
    status = (values.get("final_result") or {}).get("status")
    if status:
        return status
    # 普通问题在 general_route 节点直接结束，没有 final_result。
    recent = values.get("trace", [])[-4:]
    if any(
        entry.get("node") == "general_route"
        and entry.get("source") in {"general", "fallback"}
        for entry in recent
    ):
        return "general"
    if values.get("turn_kind") == "followup" or values.get("followup_count"):
        return "followup"
    return "unknown"


def evaluate_case(
    case: dict[str, Any],
    tools: list[str],
    outcome: str,
    missing_fields: list[str] | None = None,
) -> dict[str, Any]:
    """纯判定：给定实际工具与结局，算选择是否正确。"""
    expected = case.get("expected_tool")
    forbidden = set(case.get("forbidden_tools") or [])
    missing = list(missing_fields or [])

    if expected is None:
        selection_correct = not tools
    else:
        selection_correct = expected in tools

    expected_outcome = case.get("expected_outcome", "any")
    outcome_correct = expected_outcome == "any" or outcome == expected_outcome

    misuse = sorted(set(tools) & forbidden)
    unreachable = expected not in (None, "department_route") and expected not in tools
    # 期望调 department_route 但没调，且仍有缺失字段：多半是 intake 还没收完；
    # guideline_retrieve 不走字段收集，不能算“被 intake 挡住”。
    blocked_by_intake = bool(expected == "department_route" and not tools and missing)

    return {
        "selection_correct": selection_correct,
        "outcome_correct": outcome_correct,
        "dangerous_misuse": misuse,
        "unreachable": unreachable,
        "blocked_by_intake": blocked_by_intake,
    }


def run_case(
    case: dict[str, Any], client: TestClient, db_path: Path
) -> dict[str, Any]:
    reset_api(db_path)
    session_id: str | None = None
    values: dict[str, Any] = {}
    error = ""

    try:
        with client:
            turns = case["input_turns"]
            response = client.post("/sessions", json={"initial_message": turns[0]})
            if response.status_code != 200:
                return _failed_row(case, f"创建会话失败：HTTP {response.status_code}")
            session_id = response.json()["session_id"]
            values = state_for(session_id)

            for message in turns[1:]:
                response = client.post(
                    f"/sessions/{session_id}/messages", json={"message": message}
                )
                if response.status_code != 200:
                    return _failed_row(case, f"发送消息失败：HTTP {response.status_code}")
                values = state_for(session_id)
    except Exception as exc:
        return _failed_row(case, f"{type(exc).__name__}: {exc}")

    trace = values.get("trace", [])
    tools = [
        entry.get("called")
        for entry in trace
        if entry.get("node") == "tool" and entry.get("called")
    ]
    outcome = outcome_of(values)
    missing_fields = list(values.get("missing_fields") or [])
    verdict = evaluate_case(case, tools, outcome, missing_fields)

    return {
        "id": case["id"],
        "split": case.get("split", "dev"),
        "category": case["category"],
        "expected_tool": case.get("expected_tool"),
        "actual_tools": tools,
        "outcome": outcome,
        "expected_outcome": case.get("expected_outcome", "any"),
        "missing_fields": missing_fields,
        "error": error,
        **verdict,
    }


def _failed_row(case: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "id": case["id"],
        "split": case.get("split", "dev"),
        "category": case["category"],
        "expected_tool": case.get("expected_tool"),
        "actual_tools": [],
        "outcome": "error",
        "expected_outcome": case.get("expected_outcome", "any"),
        "missing_fields": [],
        "selection_correct": False,
        "outcome_correct": False,
        "dangerous_misuse": [],
        "unreachable": False,
        "blocked_by_intake": False,
        "error": error,
    }


def rate(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return round(sum(1 for row in rows if row[key]) / len(rows), 3)


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[tuple[str, list[dict[str, Any]]]] = [("全部", rows)]
    for category in sorted({row["category"] for row in rows}):
        groups.append((category, [row for row in rows if row["category"] == category]))

    summary = []
    for name, subset in groups:
        summary.append(
            {
                "group": name,
                "n": len(subset),
                "selection": rate(subset, "selection_correct"),
                "outcome": rate(subset, "outcome_correct"),
                "misuse": sum(1 for row in subset if row["dangerous_misuse"]),
                "unreachable": sum(1 for row in subset if row["unreachable"]),
                "blocked": sum(1 for row in subset if row["blocked_by_intake"]),
            }
        )
    return summary


def _cell(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def write_report(
    rows: list[dict[str, Any]],
    summary: list[dict[str, Any]],
    output_dir: Path,
    *,
    use_model: bool,
    skipped: int,
) -> Path:
    lines = [
        "# M7 工具选择正确率报告",
        "",
        f"- 模式：{'真实模型（USE_MODEL=1，会产生费用）' if use_model else '确定性 mock，不调用外部模型'}",
        f"- 样本：{len(rows)} 有效；跳过：{skipped}（requires_model 且当前为 mock）",
        "- 选择正确的定义：期望工具被调用；期望为 null 时，没有调用任何工具。",
        "- 不可达：期望的是 department_route 之外的工具，但实际没调用。",
        "",
        "## 汇总",
        "",
        "| 组别 | 样本 | 选择正确 | 结局正确 | 危险误调 | 不可达 | 被 intake 挡住 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        lines.append(
            f"| {row['group']} | {row['n']} | {_cell(row['selection'])} "
            f"| {_cell(row['outcome'])} | {row['misuse']} | {row['unreachable']} | {row['blocked']} |"
        )

    lines += [
        "",
        "## 逐条结果",
        "",
        "| ID | 类别 | 期望工具 | 实际工具 | 结局 | 缺字段 | 选择 | 结局 | 备注 |",
        "|---|---|---|---|---|---|---:|---:|---|",
    ]
    for row in rows:
        tools = ", ".join(row["actual_tools"]) or "—"
        expected = row["expected_tool"] or "—"
        missing = ", ".join(row["missing_fields"]) or "—"
        if row["error"]:
            note = row["error"]
        elif row["unreachable"]:
            note = "不可达"
        elif row["blocked_by_intake"]:
            note = "被 intake 挡住"
        else:
            note = ""
        lines.append(
            f"| {row['id']} | {row['category']} | {expected} | {tools} | {row['outcome']} "
            f"| {missing} | {'✓' if row['selection_correct'] else '✗'} "
            f"| {'✓' if row['outcome_correct'] else '✗'} | {note} |"
        )

    lines += [
        "",
        "## 怎么读",
        "",
        "- `guideline` 类在 Phase C 之前会全部不可达：图只会调 `department_route`。",
        "  这不是模型选错，而是决策 schema 还没有 `tool_name`。",
        "- `risk` 类期望工具为 null：红旗由规则层在 decision 之前短路，不应进入工具节点。",
        "- `general` 类需要模型路由，mock 模式跳过。",
        "- 参数正确性（`tool_args`）留到 Phase C；当前参数由代码拼接，不是模型生成。",
        "",
        "## 局限",
        "",
        "- 样本是虚构场景，覆盖的是“该不该调哪个工具”，不是临床正确性。",
        "- 当前只有 1/3 的工具可达，选择正确率的上限由结构决定，不能当作模型能力结论。",
    ]

    report_path = output_dir / "tool-selection-report.md"
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

    runnable = []
    skipped = 0
    for case in cases:
        if case.get("requires_model") and not use_model:
            skipped += 1
            continue
        runnable.append(case)

    output_dir.mkdir(parents=True, exist_ok=True)
    original_use_model = os.environ.get("USE_MODEL")
    original_db = os.environ.get("CONSULTATION_DB_PATH")
    os.environ["USE_MODEL"] = "1" if use_model else "0"
    client = TestClient(app, raise_server_exceptions=False)

    rows: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="tool-selection-") as temporary:
            temp = Path(temporary)
            for index, case in enumerate(runnable):
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

    results_path = output_dir / "tool-selection-results.jsonl"
    results_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    summary = summarize(rows)
    report_path = write_report(
        rows, summary, output_dir, use_model=use_model, skipped=skipped
    )

    print(f"M7 完成：{len(rows)} 条样本，跳过 {skipped} 条；报告：{report_path}")
    for row in summary:
        print(
            f"  {row['group']}: n={row['n']} selection={_cell(row['selection'])} "
            f"outcome={_cell(row['outcome'])} misuse={row['misuse']} "
            f"unreachable={row['unreachable']} blocked={row['blocked']}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, help="仅运行前 N 条样本")
    parser.add_argument("--case", dest="case_ids", help="只跑指定样本 ID，逗号分隔")
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
