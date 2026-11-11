"""general / medical 路由评测。

专门测预路由的边界：生活方式、饮食、寒暄、情绪应走 general；
症状、药品、检查、就医判断和红旗应走 medical。

运行（会调用模型）：
    uv run python -m eval.router_eval
    uv run python -m eval.router_eval --split holdout
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from backend.app.agent.router import route
from backend.app.llm.client import LLMError

DEFAULT_CASES = Path("eval/probes/router_cases.jsonl")
DEFAULT_OUTPUT = Path("eval/results/router-eval")


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score(cases: list[dict[str, Any]], router: Callable[[str], Any]) -> dict[str, Any]:
    rows = []
    for case in cases:
        predicted = ""
        error = ""
        try:
            result = router(case["message"])
            reply = result[0] if isinstance(result, tuple) else result
            predicted = str(getattr(reply, "intent", reply))
        except LLMError as exc:
            error = str(exc)
        rows.append(
            {
                "id": case["id"],
                "split": case.get("split", "dev"),
                "category": case["category"],
                "message": case["message"],
                "expected": case["expected_intent"],
                "predicted": predicted,
                "correct": predicted == case["expected_intent"],
                "error": error,
            }
        )

    correct = sum(1 for row in rows if row["correct"])
    return {
        "n": len(rows),
        "accuracy": round(correct / len(rows), 3) if rows else None,
        "rows": rows,
    }


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    for category in sorted({row["category"] for row in rows}):
        subset = [row for row in rows if row["category"] == category]
        correct = sum(1 for row in subset if row["correct"])
        summary.append(
            {
                "category": category,
                "n": len(subset),
                "correct": correct,
                "accuracy": round(correct / len(subset), 3) if subset else None,
            }
        )
    return summary


def write_report(result: dict[str, Any], output_dir: Path) -> Path:
    rows = result["rows"]
    lines = [
        "# general / medical 路由评测",
        "",
        f"- 样本：{result['n']}",
        f"- 准确率：{result['accuracy']}",
        "",
        "## 分类汇总",
        "",
        "| 类别 | 样本 | 正确 | 准确率 |",
        "|---|---:|---:|---:|",
    ]
    for item in summarize(rows):
        lines.append(
            f"| {item['category']} | {item['n']} | {item['correct']} | {item['accuracy']} |"
        )

    wrong = [row for row in rows if not row["correct"]]
    lines += ["", "## 错误样本", ""]
    if wrong:
        lines += ["| ID | 类别 | 消息 | 期望 | 实际 |", "|---|---|---|---|---|"]
        for row in wrong:
            lines.append(
                f"| {row['id']} | {row['category']} | {row['message'][:40]} "
                f"| {row['expected']} | {row['predicted'] or row['error']} |"
            )
    else:
        lines.append("无。")

    lines += [
        "",
        "## 怎么读",
        "",
        "- `general` 误判为 `medical` 会让饮食/寒暄问题进入字段收集，体验明显变差。",
        "- `medical` 误判为 `general` 风险更高；所以比例失衡时优先保医疗召回。",
        "- 本评测调用真实模型；单次结果有波动，阶段末至少重跑 3 次。",
    ]

    report_path = output_dir / "router-eval-report.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def run(cases_path: Path, output_dir: Path, *, split: str = "dev", limit: int | None = None) -> int:
    cases = load_cases(cases_path)
    if split != "all":
        cases = [case for case in cases if case.get("split", "dev") == split]
    if limit is not None:
        cases = cases[:limit]

    result = score(cases, route)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "router-eval-results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in result["rows"]),
        encoding="utf-8",
    )
    report_path = write_report(result, output_dir)

    print(f"路由评测完成：{result['n']} 条，准确率 {result['accuracy']}；报告：{report_path}")
    for item in summarize(result["rows"]):
        print(
            f"  {item['category']}: n={item['n']} correct={item['correct']} "
            f"accuracy={item['accuracy']}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, help="仅运行前 N 条样本")
    parser.add_argument(
        "--split",
        choices=["all", "dev", "holdout"],
        default="dev",
        help="默认只跑 dev；holdout 留到阶段末",
    )
    args = parser.parse_args()
    return run(args.cases, args.output_dir, split=args.split, limit=args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
