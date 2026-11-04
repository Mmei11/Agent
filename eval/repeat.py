"""Phase 3 第 4 课：多次采样，衡量稳定性。

同一条案例跑 n 次，报 pass^n、单次通过率、judge 分数均值与标准差。
mock 全量验证评测器没有随机性；judge 只对 --judge-cases 指定的案例抽样。

运行：
    python -m eval.repeat --n 5 --split dev
    python -m eval.repeat --n 5 --case E009,E015,E017,E018,E020 \
        --judge-cases E009,E015,E017,E018,E020
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app
from backend.app.llm.client import LLMError
from eval.judge import DIMENSIONS, PASS_SCORE, judge_case, verdict
from eval.run_eval import check_case

DIMENSION_LABELS = {
    "followup_relevance": "追问相关性",
    "evidence_grounding": "结论有据",
    "boundary_compliance": "边界合规",
}


def load_cases(path: Path, split: str, case_ids: list[str]) -> list[dict]:
    cases = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if case_ids:
        wanted = set(case_ids)
        cases = [case for case in cases if case["id"] in wanted]
        missing = wanted - {case["id"] for case in cases}
        if missing:
            raise SystemExit(f"案例不存在：{', '.join(sorted(missing))}")
    elif split != "all":
        cases = [case for case in cases if case.get("split", "dev") == split]
    return cases


def mean_std(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    mean = statistics.fmean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    return round(mean, 3), round(std, 3)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 3)


def aggregate_case(
    case: dict,
    runs: list[dict],
    judged: bool,
) -> dict:
    passes = [run["status"] == "pass" for run in runs]
    row = {
        "case_id": case["id"],
        "category": case["category"],
        "split": case.get("split", "dev"),
        "runs": len(runs),
        "pass_count": sum(passes),
        "pass_pow": all(passes),
        "failures": sorted({failure for run in runs for failure in run["failures"]}),
    }
    if not judged:
        return row

    dimensions: dict[str, Any] = {}
    for dimension in DIMENSIONS:
        scores = [
            run["judge"]["scores"][dimension]
            for run in runs
            if (run.get("judge") or {}).get("scores")
            and run["judge"]["applicable"][dimension]
        ]
        mean, std = mean_std(scores)
        dimensions[dimension] = {
            "scores": scores,
            "mean": mean,
            "std": std,
            "pass_rate": (
                round(sum(score >= PASS_SCORE for score in scores) / len(scores), 3)
                if scores
                else None
            ),
            "applicable_runs": len(scores),
        }
    row["judge"] = dimensions
    return row


def aggregate_judge(case_rows: list[dict]) -> dict:
    summary: dict[str, Any] = {}
    for dimension in DIMENSIONS:
        pooled: list[float] = []
        within_std: list[float] = []
        for row in case_rows:
            detail = (row.get("judge") or {}).get(dimension)
            if not detail or not detail["scores"]:
                continue
            pooled.extend(detail["scores"])
            if detail["std"] is not None:
                within_std.append(detail["std"])
        mean, std = mean_std(pooled)
        summary[dimension] = {
            "n": len(pooled),
            "mean": mean,
            "std": std,
            "mean_within_case_std": (
                round(statistics.fmean(within_std), 3) if within_std else None
            ),
            "pass_rate": (
                round(sum(score >= PASS_SCORE for score in pooled) / len(pooled), 3)
                if pooled
                else None
            ),
        }
    return summary


def repeat(
    cases_path: Path,
    output_dir: Path,
    n: int,
    split: str,
    case_ids: list[str],
    judge_case_ids: set[str],
    use_model: bool,
) -> int:
    cases = load_cases(cases_path, split, case_ids)
    output_dir.mkdir(parents=True, exist_ok=True)

    original_use_model = os.environ.get("USE_MODEL")
    os.environ["USE_MODEL"] = "1" if use_model else "0"
    run_rows: list[dict] = []
    case_rows: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="repeat-") as temporary:
            client = TestClient(app, raise_server_exceptions=False)
            for case in cases:
                judged = case["id"] in judge_case_ids
                runs: list[dict] = []
                for index in range(n):
                    summary = check_case(
                        case,
                        client,
                        Path(temporary) / f"{case['id']}-{index}.sqlite",
                        with_state=judged,
                    )
                    state = summary.pop("_state", None)
                    run_row = {
                        "case_id": case["id"],
                        "run": index + 1,
                        **summary,
                    }
                    if judged and state is not None:
                        try:
                            result, usage = judge_case(state)
                            judge_summary = verdict(result, state)
                            run_row["judge"] = {
                                "scores": judge_summary["scores"],
                                "applicable": judge_summary["applicable"],
                                "passed": judge_summary["passed"],
                                "failed_dimensions": judge_summary["failed_dimensions"],
                                "details": {
                                    dimension: getattr(result, dimension).model_dump()
                                    for dimension in DIMENSIONS
                                },
                                "model": usage.model,
                                "cost_estimate": usage.cost_estimate,
                                "latency_ms": usage.latency_ms,
                            }
                        except LLMError as exc:
                            run_row["judge"] = {"error": str(exc)}
                    run_rows.append(run_row)
                    runs.append(run_row)
                case_row = aggregate_case(case, runs, judged)
                case_rows.append(case_row)
                mark = f"PASS^{n}" if case_row["pass_pow"] else f"{case_row['pass_count']}/{n}"
                print(
                    f"{case['id']}  {mark:10}  "
                    f"失败原因 {len(case_row['failures'])} 条"
                )
    finally:
        if original_use_model is None:
            os.environ.pop("USE_MODEL", None)
        else:
            os.environ["USE_MODEL"] = original_use_model
        routes.reset_app()

    case_runs_jsonl = output_dir / "repeat-runs.jsonl"
    case_runs_jsonl.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in run_rows),
        encoding="utf-8",
    )
    (output_dir / "repeat-cases.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in case_rows),
        encoding="utf-8",
    )

    total_cases = len(case_rows)
    stable = sum(row["pass_pow"] for row in case_rows)
    total_runs = total_cases * n
    passed_runs = sum(row["pass_count"] for row in case_rows)
    unstable = [row for row in case_rows if 0 < row["pass_count"] < n]
    always_fail = [row for row in case_rows if row["pass_count"] == 0]

    all_latencies = [row["latency_seconds"] for row in run_rows]
    case_cost = sum((row.get("cost_estimate") or 0) for row in run_rows)
    judge_cost = sum(
        ((row.get("judge") or {}).get("cost_estimate") or 0) for row in run_rows
    )
    judge_models = sorted(
        {
            row["judge"]["model"]
            for row in run_rows
            if (row.get("judge") or {}).get("model")
        }
    )
    judge_errors = sum(1 for row in run_rows if (row.get("judge") or {}).get("error"))

    judge_summary = aggregate_judge(case_rows)
    lines = [
        "# 多次采样报告",
        "",
        f"- 案例：{total_cases}；每条运行 {n} 次；模式："
        + ("真实模型（USE_MODEL=1）" if use_model else "确定性 mock"),
        f"- 硬断言 pass^{n}：{stable}/{total_cases} = {stable / total_cases:.2f}"
        if total_cases
        else "- 无案例",
        f"- 单次运行通过率：{passed_runs}/{total_runs} = "
        f"{passed_runs / total_runs:.2f}" if total_runs else "- 无运行",
        f"- 不稳定案例：{len(unstable)} 条；始终失败：{len(always_fail)} 条",
        f"- 成本：案例 ¥{round(case_cost, 6)}；judge ¥{round(judge_cost, 6)}",
        f"- 延迟 p50 / p95 / max（秒）："
        f"{_pct(all_latencies, 0.5)} / {_pct(all_latencies, 0.95)} / "
        f"{max(all_latencies) if all_latencies else 0}",
    ]
    if judge_models:
        lines.append(
            f"- judge：{', '.join(judge_models)}；{len(judge_case_ids)} 条案例 × "
            f"{n} 次；错误 {judge_errors} 次"
        )
    else:
        lines.append("- judge：未启用")

    lines += [
        "",
        "## 硬断言",
        "",
        f"| ID | 类别 | 通过/次数 | pass^{n} | 失败原因 |",
        "|---|---|---:|---|---|",
    ]
    for row in case_rows:
        fail_text = "；".join(row["failures"]) or "—"
        lines.append(
            f"| {row['case_id']} | {row['category']} | "
            f"{row['pass_count']}/{row['runs']} | "
            f"{'是' if row['pass_pow'] else '否'} | {fail_text} |"
        )

    if judge_models:
        lines += [
            "",
            "## judge 分数",
            "",
            "| 维度 | n | 均值 | 标准差 | 案例内平均标准差 | 通过率 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for dimension in DIMENSIONS:
            detail = judge_summary[dimension]
            lines.append(
                f"| {DIMENSION_LABELS[dimension]} | {detail['n']} | "
                f"{detail['mean'] if detail['mean'] is not None else '—'} | "
                f"{detail['std'] if detail['std'] is not None else '—'} | "
                f"{detail['mean_within_case_std'] if detail['mean_within_case_std'] is not None else '—'} | "
                f"{detail['pass_rate'] if detail['pass_rate'] is not None else '—'} |"
            )
        lines += [
            "",
            "### 逐条分数",
            "",
            "| ID | 维度 | 分数序列 | 均值 | 标准差 | 通过率 |",
            "|---|---|---|---:|---:|---:|",
        ]
        for row in case_rows:
            for dimension in DIMENSIONS:
                detail = (row.get("judge") or {}).get(dimension)
                if not detail or not detail["scores"]:
                    continue
                lines.append(
                    f"| {row['case_id']} | {DIMENSION_LABELS[dimension]} | "
                    f"{' / '.join(str(score) for score in detail['scores'])} | "
                    f"{detail['mean']} | {detail['std']} | {detail['pass_rate']} |"
                )

    if unstable:
        lines += ["", "## 不稳定案例", ""]
        for row in unstable:
            lines.append(
                f"- {row['case_id']}：{row['pass_count']}/{n} 次通过；"
                f"失败原因：{'；'.join(row['failures']) or '—'}"
            )
    if always_fail:
        lines += ["", "## 始终失败", ""]
        for row in always_fail:
            lines.append(
                f"- {row['case_id']}：0/{n}；失败原因：{'；'.join(row['failures']) or '—'}"
            )

    lines += [
        "",
        "## 局限",
        "",
        f"- n={n} 只能发现明显不稳定；5 次全过不等于永远通过，二项比例的置信区间很宽。",
        "- mock 是确定性的，pass^n=1.0 是评测器自检，不代表模型模式的稳定性。",
        "- judge 分数是离散的 1-5，标准差小不代表判断一定正确；理由错误不在 κ/标准差里体现。",
        "",
        f"逐条运行：`{case_runs_jsonl}`。",
        "",
    ]
    report_path = output_dir / "repeat-report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    print(
        f"\n完成：pass^{n} {stable}/{total_cases}；"
        f"成本 ¥{round(case_cost + judge_cost, 6)}；报告 {report_path}"
    )
    return 0 if stable == total_cases else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("eval/cases.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results/repeat"))
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--split", choices=["all", "dev", "holdout"], default="dev")
    parser.add_argument("--case", dest="case_ids", help="逗号分隔，只跑指定案例")
    parser.add_argument(
        "--judge-cases",
        default="",
        help="逗号分隔；这些案例的每次运行都调用 judge（会产生模型费用）",
    )
    parser.add_argument("--use-model", action="store_true")
    args = parser.parse_args()

    case_ids = [item.strip() for item in (args.case_ids or "").split(",") if item.strip()]
    judge_case_ids = {
        item.strip() for item in args.judge_cases.split(",") if item.strip()
    }
    return repeat(
        args.cases,
        args.output_dir,
        args.n,
        args.split,
        case_ids,
        judge_case_ids,
        args.use_model,
    )


if __name__ == "__main__":
    raise SystemExit(main())