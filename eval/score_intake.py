"""Phase 0 测量工具：计算字段抽取的槽位级 F1。

对照 eval/probes/intake_samples.jsonl 中的人工标注，分别评估：
  - 关键词抽取器（backend/app/agent/intake.py 的 extract_facts）
  - 任意可注入的抽取函数（Phase 1 接模型后用）

未标注样本（labeled=false）自动跳过并提示数量。
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path

from backend.app.agent.intake import ALL_FIELDS, extract_facts

DEFAULT_SAMPLES = Path("eval/probes/intake_samples.jsonl")


def load_samples(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_labels(samples: list[dict]) -> tuple[list[str], int]:
    """检查标注格式：字段名合法、值是列表、值必须是原文片段。"""
    issues: list[str] = []
    labeled = 0
    for sample in samples:
        sid = sample.get("id", "?")
        expected = sample.get("expected")
        if not isinstance(expected, dict):
            issues.append(f"{sid}: expected 必须是对象 {{}}")
            continue
        unknown = [k for k in expected if k not in ALL_FIELDS]
        if unknown:
            issues.append(f"{sid}: 未知字段 {unknown}，只允许 {ALL_FIELDS}")
        for field, values in expected.items():
            if not isinstance(values, list):
                issues.append(f"{sid}.{field}: 值必须是列表，例如 [\"头痛\"]")
                continue
            for value in values:
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"{sid}.{field}: 存在空值或非字符串")
                elif value.strip() not in sample["text"]:
                    issues.append(f"{sid}.{field}: {value!r} 不是原文片段")
        if sample.get("labeled"):
            labeled += 1
    return issues, labeled


def extract_with_keywords(text: str) -> dict:
    return extract_facts(text)


def extract_with_module(spec: str):
    """spec 形如 'module:function'，函数接收 text 返回 {字段: [值]}。"""
    module_name, _, func_name = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def score(samples: list[dict], extractor) -> dict:
    per_field = {f: {"tp": 0, "fp": 0, "fn": 0} for f in ALL_FIELDS}
    rows = []
    for sample in samples:
        if not sample.get("labeled"):
            continue
        expected = sample["expected"]
        actual = extractor(sample["text"])
        for field in ALL_FIELDS:
            exp_vals = [v.strip() for v in expected.get(field, [])]
            act_vals = [v.strip() for v in actual.get(field, [])]
            has_exp, has_act = bool(exp_vals), bool(act_vals)
            if has_exp and has_act:
                per_field[field]["tp"] += 1
            elif has_act:
                per_field[field]["fp"] += 1
            elif has_exp:
                per_field[field]["fn"] += 1
        rows.append({"id": sample["id"], "text": sample["text"], "expected": expected, "actual": actual})
    return {"per_field": per_field, "rows": rows}


def summarize(per_field: dict) -> dict:
    tp = sum(v["tp"] for v in per_field.values())
    fp = sum(v["fp"] for v in per_field.values())
    fn = sum(v["fn"] for v in per_field.values())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--extractor",
        default="keyword",
        help="keyword 或 module:function（函数接收 text，返回字段字典）",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--validate", action="store_true", help="只检查标注格式，不计算分数")
    args = parser.parse_args()

    samples = load_samples(args.samples)

    if args.validate:
        issues, labeled = validate_labels(samples)
        print(f"已标注：{labeled} / {len(samples)}")
        if issues:
            print(f"发现问题 {len(issues)} 处：")
            for issue in issues[:40]:
                print(f"- {issue}")
            if len(issues) > 40:
                print(f"- …另有 {len(issues) - 40} 处")
            return 1
        print("格式检查通过。")
        return 0
    labeled = [s for s in samples if s.get("labeled")]
    if not labeled:
        print(f"没有已标注样本：{args.samples} 共 {len(samples)} 条，全部 labeled=false。")
        print("请先按 docs/planning/optimization-metrics.md 的标注规则填写 expected 并置 labeled=true。")
        return 1

    extractor = extract_with_keywords if args.extractor == "keyword" else extract_with_module(args.extractor)
    result = score(samples, extractor)
    summary = summarize(result["per_field"])

    print(f"样本：已标注 {len(labeled)} / 共 {len(samples)}；抽取器：{args.extractor}")
    print(f"槽位级  precision={summary['precision']}  recall={summary['recall']}  f1={summary['f1']}")
    print(f"计数：TP={summary['tp']} FP={summary['fp']} FN={summary['fn']}")
    print("\n| 字段 | TP | FP | FN |")
    print("|---|---:|---:|---:|")
    for field, v in result["per_field"].items():
        print(f"| {field} | {v['tp']} | {v['fp']} | {v['fn']} |")

    missed = [r for r in result["rows"] if r["expected"] and r["actual"] != r["expected"]]
    if missed:
        print(f"\n不一致样本 {len(missed)} 条（前 10）：")
        for r in missed[:10]:
            print(f"- {r['id']}: 期望 {r['expected']} / 实际 {r['actual']}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"summary": summary, **result}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n逐条结果：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())