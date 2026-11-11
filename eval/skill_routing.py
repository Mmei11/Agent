"""Phase A · M9：技能路由准确率（占位）。

技能层要到 Phase D 才实现，所以这里先把“样本格式 + 计分逻辑 + N/A 报告”
搭好。启用方式：把样本的 `enabled` 改成 true，并实现
`backend.app.skills.router.route(message)`。

运行：
    uv run python -m eval.skill_routing
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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _skill_name(value: Any) -> str:
    """路由函数可以返回字符串，也可以返回带 .skill 的对象。"""
    return str(getattr(value, "skill", value))


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
                "message": case["message"],
                "expected_skill": case["expected_skill"],
                "predicted_skill": predicted,
                "correct": predicted == case["expected_skill"],
            }
        )
    correct = sum(1 for row in rows if row["correct"])
    return {
        "enabled": len(enabled),
        "accuracy": round(correct / len(enabled), 3) if enabled else None,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "skill-routing-report.md"

    cases = load_cases(args.cases)
    enabled = [case for case in cases if case.get("enabled")]

    if not cases:
        reason = f"样本文件不存在或为空：`{args.cases}`"
    elif not enabled:
        reason = "技能层尚未实现（Phase D），所有样本 `enabled=false`"
    else:
        try:
            from backend.app.skills.router import route
        except ImportError:
            reason = "找不到 `backend.app.skills.router.route`，技能层尚未实现"
        else:
            result = score(cases, route)
            (output_dir / "skill-routing-results.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in result["rows"]),
                encoding="utf-8",
            )
            lines = [
                "# M9 技能路由准确率",
                "",
                f"- 样本：{result['enabled']}",
                f"- 准确率：{result['accuracy']}",
                "",
                "| ID | 期望技能 | 实际技能 | 是否正确 |",
                "|---|---|---|---:|",
            ]
            for row in result["rows"]:
                lines.append(
                    f"| {row['id']} | {row['expected_skill']} | {row['predicted_skill']} "
                    f"| {'✓' if row['correct'] else '✗'} |"
                )
            report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"M9 完成：{result['enabled']} 条，准确率 {result['accuracy']}；报告：{report_path}")
            return 0

    report_path.write_text(
        "\n".join(
            [
                "# M9 技能路由准确率",
                "",
                "N/A",
                "",
                f"- 原因：{reason}",
                "- 启用方式：把 `eval/probes/skill_routing.jsonl` 中样本的 `enabled` 改为 true，",
                "  并实现 `backend.app.skills.router.route(message)`。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"M9 N/A：{reason}；报告：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
