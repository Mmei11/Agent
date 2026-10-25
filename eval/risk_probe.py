"""Phase 0 测量工具：用当前风险规则跑红旗探针集，输出召回/误报基线。

只调用 screen()，不经过 graph 和 API，因此测的是规则层本身。
探针文本均为虚构或通用症状描述，不包含真实个人信息。
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from backend.app.safety.rule_engine import screen

DEFAULT_PROBES = Path("eval/probes/red_flag_probes.jsonl")


def load_probes(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(probes: list[dict]) -> list[dict]:
    rows = []
    for probe in probes:
        result = screen(probe["text"])
        level = result["level"]
        if probe["expected"] == "escalate":
            # 严格：必须命中 risk；保守：risk 或 vague 都算把用户挡在普通路径外。
            strict_hit = level == "risk"
            safe_hit = level in ("risk", "vague")
        else:
            strict_hit = level == "safe"
            safe_hit = strict_hit
        rows.append(
            {
                **probe,
                "actual_level": level,
                "actual_rule": result["rule"],
                "strict_pass": strict_hit,
                "conservative_pass": safe_hit,
            }
        )
    return rows


def summarize(rows: list[dict]) -> dict:
    out = {}
    for split in ("dev", "holdout", "all"):
        subset = rows if split == "all" else [r for r in rows if r["split"] == split]
        escalates = [r for r in subset if r["expected"] == "escalate"]
        controls = [r for r in subset if r["expected"] != "escalate"]
        out[split] = {
            "n": len(subset),
            "escalate_n": len(escalates),
            "control_n": len(controls),
            "strict_recall": _ratio(escalates, "strict_pass"),
            "conservative_recall": _ratio(escalates, "conservative_pass"),
            "false_escalation_rate": 1 - _ratio(controls, "strict_pass") if controls else None,
            "missed": [r["id"] for r in escalates if not r["conservative_pass"]],
            "false_positive": [r["id"] for r in controls if not r["strict_pass"]],
        }
    return out


def _ratio(items: list[dict], key: str) -> float | None:
    if not items:
        return None
    return round(sum(bool(i[key]) for i in items) / len(items), 4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probes", type=Path, default=DEFAULT_PROBES)
    parser.add_argument("--output", type=Path, default=None, help="把逐条结果写成 JSON")
    args = parser.parse_args()

    rows = evaluate(load_probes(args.probes))
    summary = summarize(rows)

    print("| ID | split | 期望 | 实际 | 规则 | 结果 |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        mark = "通过" if r["conservative_pass"] else "漏判"
        print(f"| {r['id']} | {r['split']} | {r['expected']} | {r['actual_level']} | {r['actual_rule'] or '—'} | {mark} |")

    print("\n汇总：")
    for split, s in summary.items():
        print(
            f"- {split}: n={s['n']} 严格召回={s['strict_recall']} "
            f"保守召回={s['conservative_recall']} 误报率={s['false_escalation_rate']}"
        )
        if s["missed"]:
            print(f"    漏判: {', '.join(s['missed'])}")
        if s["false_positive"]:
            print(f"    误报: {', '.join(s['false_positive'])}")

    by_category = Counter(r["category"] for r in rows)
    print(f"- 探针分布: {dict(sorted(by_category.items()))}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n逐条结果：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())