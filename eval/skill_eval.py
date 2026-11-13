"""Phase D：按技能评测。

默认 mock 模式，不调用模型；每个技能读取 skills/<name>/eval.jsonl。
    uv run python -m eval.skill_eval
    USE_MODEL=1 uv run python -m eval.skill_eval --use-model
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# 环境必须在导入图之前设置好。
if "--use-model" in sys.argv:
    os.environ["USE_MODEL"] = "1"
else:
    os.environ.setdefault("USE_MODEL", "0")
os.environ.setdefault("KNOWLEDGE_BACKEND", "stub")
os.environ.setdefault("MCP_ENABLED", "0")
os.environ.setdefault("SKILLS_ENABLED", "1")
os.environ.setdefault("TRACE_SINK", "none")

DEFAULT_OUTPUT = Path("eval/results/skill-eval")


def load_cases(root: Path) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(root.glob("*/eval.jsonl")):
        skill = path.parent.name
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            case.setdefault("expected_skill", skill)
            cases.append(case)
    return cases


def _tools(state: dict) -> list[str]:
    return [
        entry.get("called")
        for entry in state.get("trace", [])
        if entry.get("node") == "tool" and entry.get("called")
    ]


def run_case(app, case: dict[str, Any]) -> dict[str, Any]:
    state = app.invoke({"latest_user_message": case["message"]})
    final = state.get("final_result") or {}
    question = (state.get("conversation") or [{}])[-1].get("text", "")
    tools = _tools(state)

    checks = {"skill": state.get("skill") == case["expected_skill"]}

    if case.get("expected_action"):
        checks["action"] = state.get("next_action") == case["expected_action"]
    if case.get("expected_status"):
        checks["status"] = final.get("status") == case["expected_status"]
    if case.get("expected_question_contains"):
        checks["question"] = case["expected_question_contains"] in question
    if case.get("expected_next_step_contains"):
        checks["next_step"] = (
            case["expected_next_step_contains"] in (final.get("next_step") or "")
        )
    forbidden = set(case.get("forbidden_tools") or [])
    checks["forbidden_tools"] = not (forbidden & set(tools))

    return {
        "id": case["id"],
        "expected_skill": case["expected_skill"],
        "actual_skill": state.get("skill"),
        "next_action": state.get("next_action"),
        "status": final.get("status"),
        "question": question,
        "tools": tools,
        "passed": all(checks.values()),
        "failed_checks": [name for name, ok in checks.items() if not ok],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=Path("skills"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-accuracy", type=float, default=0.9)
    args = parser.parse_args()

    from backend.app.agent.graph import build_graph

    cases = load_cases(args.cases_dir)
    if not cases:
        print(f"没有找到技能评测集：{args.cases_dir}/*/eval.jsonl")
        return 1

    app = build_graph()
    rows = [run_case(app, case) for case in cases]

    groups = []
    for skill in sorted({row["expected_skill"] for row in rows}):
        subset = [row for row in rows if row["expected_skill"] == skill]
        passed = sum(1 for row in subset if row["passed"])
        groups.append(
            {
                "skill": skill,
                "n": len(subset),
                "passed": passed,
                "accuracy": round(passed / len(subset), 3),
            }
        )

    overall = round(sum(1 for row in rows if row["passed"]) / len(rows), 3)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "skill-eval-results.jsonl"
    report_path = output_dir / "skill-eval-report.md"

    results_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    lines = [
        "# 技能评测",
        "",
        f"- 模式：{'model' if os.getenv('USE_MODEL') == '1' else 'mock'}",
        f"- 样本：{len(rows)}",
        f"- 整体通过率：{overall}",
        "",
        "| 技能 | n | passed | accuracy |",
        "|---|---:|---:|---:|",
    ]
    for group in groups:
        lines.append(
            f"| {group['skill']} | {group['n']} | {group['passed']} "
            f"| {group['accuracy']} |"
        )
    lines += ["", "| ID | 技能 | 实际技能 | 实际动作/状态 | 失败项 |", "|---|---|---|---|---|"]
    for row in rows:
        actual = row["status"] or row["next_action"]
        lines.append(
            f"| {row['id']} | {row['expected_skill']} | {row['actual_skill']} "
            f"| {actual} | {', '.join(row['failed_checks']) or '—'} |"
        )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        f"技能评测完成：{len(rows)} 条，整体 {overall}；报告：{report_path}"
    )

    failed_groups = [
        group for group in groups if group["accuracy"] < args.min_accuracy
    ]
    if failed_groups:
        print(
            "未达门禁："
            + ", ".join(
                f"{group['skill']}={group['accuracy']}" for group in failed_groups
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
