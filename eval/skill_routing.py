"""M9：技能路由准确率。

支持三种路由模式：
    uv run python -m eval.skill_routing                 # hybrid（默认）
    uv run python -m eval.skill_routing --mode rule
    USE_MODEL=1 uv run python -m eval.skill_routing --mode model

报告按技能和 split 分组，便于确认 dev/holdout 是否一致。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

DEFAULT_CASES = Path("eval/probes/skill_routing.jsonl")
DEFAULT_OUTPUT = Path("eval/results/skill-routing")


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _skill_name(value: Any) -> str:
    """路由函数可以返回字符串，也可以返回带 .skill 的对象。"""
    return str(getattr(value, "skill", value))


def _rate(rows: list[dict], key: str) -> float | None:
    if not rows:
        return None
    return round(sum(1 for row in rows if row[key]) / len(rows), 3)


def score(
    cases: list[dict[str, Any]], router: Callable[[str], Any]
) -> dict[str, Any]:
    enabled = [case for case in cases if case.get("enabled")]
    rows = []
    for case in enabled:
        predicted = _skill_name(router(case["message"]))
        rows.append(
            {
                "id": case["id"],
                "split": case.get("split", "dev"),
                "message": case["message"],
                "expected_skill": case["expected_skill"],
                "predicted_skill": predicted,
                "correct": predicted == case["expected_skill"],
            }
        )

    groups = []
    for skill in sorted({row["expected_skill"] for row in rows}):
        subset = [row for row in rows if row["expected_skill"] == skill]
        groups.append(
            {
                "skill": skill,
                "n": len(subset),
                "accuracy": _rate(subset, "correct"),
                "dev": _rate([row for row in subset if row["split"] == "dev"], "correct"),
                "holdout": _rate(
                    [row for row in subset if row["split"] != "dev"], "correct"
                ),
            }
        )

    return {
        "enabled": len(enabled),
        "accuracy": _rate(rows, "correct"),
        "rows": rows,
        "groups": groups,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--mode", choices=["rule", "model", "hybrid"], default="hybrid"
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.mode == "hybrid" else f"-{args.mode}"
    report_path = output_dir / f"skill-routing-report{suffix}.md"
    results_path = output_dir / f"skill-routing-results{suffix}.jsonl"

    cases = load_cases(args.cases)
    enabled = [case for case in cases if case.get("enabled")]

    if not cases:
        reason = f"样本文件不存在或为空：`{args.cases}`"
    elif not enabled:
        reason = "没有启用的样本"
    else:
        try:
            from backend.app.skills import router as skill_router

            router = {
                "rule": skill_router.route_rule,
                "model": skill_router.route_model,
                "hybrid": skill_router.route,
            }[args.mode]
        except ImportError:
            reason = "找不到 `backend.app.skills.router`"
        else:
            result = score(cases, router)
            results_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False) + "\n"
                    for row in result["rows"]
                ),
                encoding="utf-8",
            )
            lines = [
                "# M9 技能路由准确率",
                "",
                f"- 模式：{args.mode}",
                f"- 样本：{result['enabled']}",
                f"- 准确率：{result['accuracy']}",
                "",
                "| 技能 | n | accuracy | dev | holdout |",
                "|---|---:|---:|---:|---:|",
            ]
            for group in result["groups"]:
                lines.append(
                    f"| {group['skill']} | {group['n']} | {group['accuracy']} "
                    f"| {group['dev']} | {group['holdout']} |"
                )
            lines += [
                "",
                "| ID | split | 期望技能 | 实际技能 | 是否正确 |",
                "|---|---|---|---|---:|",
            ]
            for row in result["rows"]:
                lines.append(
                    f"| {row['id']} | {row['split']} | {row['expected_skill']} "
                    f"| {row['predicted_skill']} | {'✓' if row['correct'] else '✗'} |"
                )
            report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(
                f"M9 [{args.mode}] 完成：{result['enabled']} 条，"
                f"准确率 {result['accuracy']}；报告：{report_path}"
            )
            return 0

    report_path.write_text(
        "\n".join(
            [
                "# M9 技能路由准确率",
                "",
                "N/A",
                "",
                f"- 原因：{reason}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"M9 N/A：{reason}；报告：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
