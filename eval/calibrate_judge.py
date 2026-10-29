"""Phase 3 第 3 课：judge 人工校准。

两步：
    export  跑案例、让 judge 打分，导出盲标模板（不展示 judge 结果）
    score   读人工标签，算一致率与 Cohen's κ

盲标是纪律不是功能：judge.jsonl 和模板放在同一目录，标注完成前别打开。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api.routes import app
from eval.judge import (
    DIMENSIONS,
    PASS_SCORE,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    DimensionScore,
    JudgeResult,
    build_payload,
    judge_case,
    verdict,
)
from eval.followup_check import analyze_followups, score_followups
from eval.judge_demo import run_case

DIMENSION_LABELS = {
    "followup_relevance": "追问相关性",
    "evidence_grounding": "结论有据",
    "boundary_compliance": "边界合规",
}

# 20 条 dev，覆盖 normal / multi_turn / risk / negation / vague。
# invalid 与 tool_failure / state_recovery 归硬断言，不进入质量校准。
DEFAULT_CASE_IDS = (
    "E001,E002,E003,E005,E006,E007,E008,"
    "E009,E011,E012,E013,E014,E015,E016,"
    "E017,E018,E020,E021,E024,E025"
)

PASS_WORDS = {"pass", "p", "1", "true", "通过"}
FAIL_WORDS = {"fail", "f", "0", "false", "不通过"}


def load_cases(path: Path, case_ids: list[str]) -> list[dict]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    wanted = set(case_ids)
    selected = [case for case in cases if case["id"] in wanted]
    missing = wanted - {case["id"] for case in selected}
    if missing:
        raise SystemExit(f"案例不存在：{', '.join(sorted(missing))}")
    return selected


def _material(values: dict) -> dict:
    """标注者看到的内容与 judge 完全一致，避免两边评的不是同一份材料。"""
    return build_payload(values)


def _render_material(sample: dict) -> str:
    material = sample["material"]
    lines = [
        f"## {sample['sample_id']} · {sample['case_id']} · "
        f"{sample['dimension']}（{DIMENSION_LABELS[sample['dimension']]}）",
        "",
        "**对话**",
        "",
    ]
    for entry in material["conversation"]:
        lines.append(f"{entry['turn']}. {entry['speaker']}：{entry['text']}")
    lines += ["", "**最终结果**", "", "```json"]
    lines.append(json.dumps(material["final_result"], ensure_ascii=False, indent=2))
    lines += ["```", "", "**渲染文案**", ""]
    lines.append(material["rendered_text"] or "（空）")
    lines += ["", "**工具结果**", "", "```json"]
    lines.append(json.dumps(material["tool_results"], ensure_ascii=False, indent=2))
    lines += [
        "```",
        "",
        f"→ 在 `labels.csv` 里给 {sample['sample_id']} 填 `pass` 或 `fail`。",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def export(
    cases_path: Path,
    output_dir: Path,
    case_ids: list[str],
) -> None:
    cases = load_cases(cases_path, case_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    samples: list[dict] = []
    models: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="calibrate-") as temporary:
        client = TestClient(app)
        for index, case in enumerate(cases):
            values = run_case(case, client, Path(temporary) / f"case-{index}.sqlite")
            if values.get("final_result"):
                result, usage = judge_case(values)
                models.add(usage.model)
            else:
                # 没有结论就没有有据/边界可评；追问是代码算的，不必调模型。
                followup = score_followups(
                    analyze_followups(values.get("conversation", []))
                )
                result = JudgeResult(
                    followup_relevance=DimensionScore(**followup),
                    evidence_grounding=DimensionScore(
                        score=5, reason="未产生最终结果，不适用", evidence="无"
                    ),
                    boundary_compliance=DimensionScore(
                        score=5, reason="未产生最终结果，不适用", evidence="无"
                    ),
                )
                usage = None
            summary = verdict(result, values)
            for dimension in DIMENSIONS:
                if not summary["applicable"][dimension]:
                    continue
                detail = getattr(result, dimension)
                samples.append(
                    {
                        "sample_id": f"S{len(samples) + 1:03d}",
                        "case_id": case["id"],
                        "dimension": dimension,
                        "material": _material(values),
                        "judge": {
                            "score": detail.score,
                            "pass": detail.score >= PASS_SCORE,
                            "reason": detail.reason,
                            "evidence": detail.evidence,
                        },
                        "judge_usage": {
                            "input_tokens": usage.input_tokens if usage else 0,
                            "output_tokens": usage.output_tokens if usage else 0,
                            "cost_estimate": usage.cost_estimate if usage else 0.0,
                        },
                    }
                )
            print(f"{case['id']} 已评分，累计样本 {len(samples)}")

    (output_dir / "judge.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "sample_id": sample["sample_id"],
                    "case_id": sample["case_id"],
                    "dimension": sample["dimension"],
                    **sample["judge"],
                },
                ensure_ascii=False,
            )
            + "\n"
            for sample in samples
        ),
        encoding="utf-8",
    )
    (output_dir / "blind.md").write_text(
        "# Judge 校准标注模板\n\n"
        f"共 {len(samples)} 个样本。每个样本只标一个维度：`pass` 或 `fail`。\n\n"
        f"判定标准：{PASS_SCORE} 分及以上 = `pass`，低于 {PASS_SCORE} 分 = `fail`。\n\n"
        f"评分细则见 `rubric.md`（prompt 版本 {PROMPT_VERSION}）。\n\n"
        + "\n".join(_render_material(sample) for sample in samples),
        encoding="utf-8",
    )
    with (output_dir / "labels.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["sample_id", "label", "note"])
        for sample in samples:
            writer.writerow([sample["sample_id"], "", ""])
    (output_dir / "rubric.md").write_text(
        f"# Judge 评分细则（{PROMPT_VERSION}）\n\n{SYSTEM_PROMPT}\n",
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "prompt_version": PROMPT_VERSION,
                "judge_model": sorted(models),
                "pass_score": PASS_SCORE,
                "case_ids": [case["id"] for case in cases],
                "samples": len(samples),
                "by_dimension": {
                    dimension: sum(
                        1 for sample in samples if sample["dimension"] == dimension
                    )
                    for dimension in DIMENSIONS
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    total_cost = sum(
        (sample["judge_usage"]["cost_estimate"] or 0) for sample in samples
    )
    print(
        f"导出完成：{len(samples)} 个样本，"
        f"judge 模型 {', '.join(sorted(models))}，"
        f"成本 ¥{round(total_cost, 6)}；目录 {output_dir}"
    )
    print("下一步：读 blind.md + rubric.md，在 labels.csv 里填 pass/fail。")


def _normalize_label(raw: str, sample_id: str) -> int:
    value = raw.strip().lower()
    if value in PASS_WORDS:
        return 1
    if value in FAIL_WORDS:
        return 0
    raise SystemExit(f"{sample_id} 的 label={raw!r} 无法识别，只能填 pass 或 fail")


def _read_text(path: Path) -> str:
    """兼容 Excel 在中文环境默认保存的 GBK/GB18030。"""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"{path} 不是 UTF-8 或 GB18030 编码，请另存为 UTF-8")


def cohen_kappa(pairs: list[tuple[int, int]]) -> float | None:
    """二分类 κ。两类都占满一侧时 pe=1，κ 无定义，返回 None。"""
    total = len(pairs)
    if total == 0:
        return None
    observed = sum(human == judge for human, judge in pairs) / total
    human_pass = sum(human for human, _ in pairs) / total
    judge_pass = sum(judge for _, judge in pairs) / total
    expected = human_pass * judge_pass + (1 - human_pass) * (1 - judge_pass)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 3)


def _interpret(kappa: float | None) -> str:
    if kappa is None:
        return "无定义（两类分布退化）"
    if kappa < 0:
        return "差于随机"
    if kappa <= 0.20:
        return "轻微"
    if kappa <= 0.40:
        return "一般"
    if kappa <= 0.60:
        return "中等"
    if kappa <= 0.80:
        return "较强"
    return "几乎完全一致"


def score(output_dir: Path, threshold: float) -> int:
    judge_rows = {
        row["sample_id"]: row
        for row in (
            json.loads(line)
            for line in (output_dir / "judge.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    label_path = output_dir / "labels.csv"
    label_rows = list(csv.DictReader(_read_text(label_path).splitlines()))

    pairs_by_dimension: dict[str, list[tuple[int, int]]] = {
        dimension: [] for dimension in DIMENSIONS
    }
    disagreements: list[str] = []
    missing: list[str] = []
    for row in label_rows:
        sample_id = row["sample_id"]
        if not row["label"].strip():
            missing.append(sample_id)
            continue
        human = _normalize_label(row["label"], sample_id)
        judge = judge_rows[sample_id]
        pairs_by_dimension[judge["dimension"]].append((human, 1 if judge["pass"] else 0))
        if human != (1 if judge["pass"] else 0):
            disagreements.append(
                f"- {sample_id}（{judge['case_id']} / {judge['dimension']}）："
                f"人工 {'pass' if human else 'fail'}，judge {'pass' if judge['pass'] else 'fail'}；"
                f"judge 理由：{judge['reason']}"
            )

    if missing:
        print(f"还有 {len(missing)} 个样本未标注：{', '.join(missing)}")

    manifest_path = output_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    lines = [
        "# Judge 人工校准报告",
        "",
        f"- 标注文件：`{label_path}`",
        f"- 标注文件 SHA-256：`{hashlib.sha256(label_path.read_bytes()).hexdigest()}`",
        f"- judge 模型：`{', '.join(manifest.get('judge_model', ['未知']))}`（有据 / 边界）",
        "- 追问相关性：确定性规则（`eval/followup_check.py`），不调用模型",
        f"- judge prompt：`judge.case` {PROMPT_VERSION}",
        f"- 通过阈值：{PASS_SCORE} 分",
        "",
        "| 维度 | n | 一致率 | Cohen's κ | 解读 | 人工通过率 | judge 通过率 |",
        "|---|---:|---:|---:|---|---:|---:|",
    ]
    all_pairs: list[tuple[int, int]] = []
    all_agree = True
    for dimension in DIMENSIONS:
        pairs = pairs_by_dimension[dimension]
        all_pairs.extend(pairs)
        if not pairs:
            lines.append(f"| {DIMENSION_LABELS[dimension]} | 0 | — | — | 无样本 | — | — |")
            continue
        agreement = sum(h == j for h, j in pairs) / len(pairs)
        kappa = cohen_kappa(pairs)
        human_rate = sum(h for h, _ in pairs) / len(pairs)
        judge_rate = sum(j for _, j in pairs) / len(pairs)
        if agreement < threshold:
            all_agree = False
        lines.append(
            f"| {DIMENSION_LABELS[dimension]} | {len(pairs)} | {agreement:.2f} | "
            f"{'—' if kappa is None else kappa} | {_interpret(kappa)} | "
            f"{human_rate:.2f} | {judge_rate:.2f} |"
        )

    if all_pairs:
        agreement = sum(h == j for h, j in all_pairs) / len(all_pairs)
        kappa = cohen_kappa(all_pairs)
        lines += [
            "",
            f"汇总：n={len(all_pairs)}，一致率 {agreement:.2f}，"
            f"κ {('—' if kappa is None else kappa)}（{_interpret(kappa)}）。",
        ]
    if disagreements:
        lines += ["", "## 不一致样本", "", *disagreements]
    lines += [
        "",
        "## 判定",
        "",
        f"- 逐维一致率均需 ≥ {threshold:.2f}。",
        "- κ 低于 0.60 时，即使一致率高也要检查样本是否过于集中在同一类（prevalence effect）。",
        "- 标注文件已冻结：改 rubric 需开新一轮目录，不能复用这份标签。",
        "",
    ]

    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"报告：{report_path}")
    return 0 if all_agree and not missing else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "score"])
    parser.add_argument("--dir", type=Path, default=Path("eval/results/calibration"))
    parser.add_argument("--cases", type=Path, default=Path("eval/cases.jsonl"))
    parser.add_argument("--case", default=DEFAULT_CASE_IDS, help="逗号分隔的案例 ID")
    parser.add_argument("--threshold", type=float, default=0.80)
    args = parser.parse_args()

    if args.command == "export":
        case_ids = [item.strip() for item in args.case.split(",") if item.strip()]
        export(args.cases, args.dir, case_ids)
        return 0
    return score(args.dir, args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())