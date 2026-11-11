"""Phase A · M8：每轮输入 token 账本。

现有 `run_eval` 只统计“每个会话一共用了多少 token”，回答不了
“哪一步的 prompt 最大、p95 是多少”。这个脚本读 trace JSONL，
按 `prompt.name` 分组统计每次模型调用的输入/输出 token 与延迟。

只读本地 trace，不发起模型调用。生成 trace 的方式：

    TRACE_SINK=jsonl TRACE_PATH=logs/traces.jsonl \
        uv run python -m eval.run_eval --use-model --case E001

然后：

    uv run python -m eval.token_ledger --trace logs/traces.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_TRACE = Path("logs/traces.jsonl")
DEFAULT_OUTPUT = Path("eval/results/token-ledger")


def load_spans(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 1)


def _new_group(key: str) -> dict[str, Any]:
    return {
        "key": key,
        "calls": 0,
        "input": [],
        "output": [],
        "latency": [],
        "cost": 0.0,
        "models": set(),
        "versions": set(),
    }


def _add(group: dict[str, Any], span: dict[str, Any], attrs: dict[str, Any]) -> None:
    group["calls"] += 1
    input_tokens = _num(attrs.get("gen_ai.usage.input_tokens"))
    output_tokens = _num(attrs.get("gen_ai.usage.output_tokens"))
    latency = _num(span.get("duration_ms"))
    if input_tokens is not None:
        group["input"].append(input_tokens)
    if output_tokens is not None:
        group["output"].append(output_tokens)
    if latency is not None:
        group["latency"].append(latency)
    cost = _num(attrs.get("llm.cost_estimate"))
    if cost is not None:
        group["cost"] += cost
    model = attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
    if model:
        group["models"].add(str(model))
    version = attrs.get("prompt.version")
    if version:
        group["versions"].add(str(version))


def _row(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": group["key"],
        "calls": group["calls"],
        "input_total": int(sum(group["input"])),
        "input_p50": pct(group["input"], 0.5),
        "input_p95": pct(group["input"], 0.95),
        "input_max": max(group["input"]) if group["input"] else None,
        "output_total": int(sum(group["output"])),
        "output_p50": pct(group["output"], 0.5),
        "output_p95": pct(group["output"], 0.95),
        "latency_p50": pct(group["latency"], 0.5),
        "latency_p95": pct(group["latency"], 0.95),
        "cost": round(group["cost"], 6) if group["cost"] else None,
        "models": sorted(group["models"]),
        "versions": sorted(group["versions"]),
    }


def summarize(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    overall = _new_group("全部")

    for span in spans:
        attrs = span.get("attributes") or {}
        input_tokens = _num(attrs.get("gen_ai.usage.input_tokens"))
        output_tokens = _num(attrs.get("gen_ai.usage.output_tokens"))
        if input_tokens is None and output_tokens is None:
            continue
        key = str(attrs.get("prompt.name") or span.get("name") or "unknown")
        _add(groups.setdefault(key, _new_group(key)), span, attrs)
        _add(overall, span, attrs)

    rows = [_row(group) for group in groups.values()]
    rows.sort(key=lambda row: row["input_total"], reverse=True)
    if rows:
        rows.append(_row(overall))
    return rows


def _cell(value: Any) -> str:
    return "—" if value is None else str(value)


def write_report(rows: list[dict[str, Any]], output_dir: Path, trace_path: Path) -> Path:
    lines = [
        "# M8 每轮输入 token 账本",
        "",
        f"- trace：`{trace_path}`",
        f"- 含用量的模型调用：{rows[-1]['calls'] if rows else 0}",
        "- 按 `prompt.name` 分组；p50/p95 是单次调用的分位数，不是会话总量。",
        "",
    ]
    if not rows:
        lines += [
            "## 结果",
            "",
            "N/A：trace 文件不存在，或其中没有带 `gen_ai.usage.*` 的 span。",
            "",
            "生成方式：",
            "",
            "```sh",
            "TRACE_SINK=jsonl TRACE_PATH=logs/traces.jsonl \\",
            "    uv run python -m eval.run_eval --use-model --case E001",
            "```",
        ]
    else:
        lines += [
            "## 汇总",
            "",
            "| 节点 / prompt | 调用 | 输入 total | 输入 p50 | 输入 p95 | 输入 max | 输出 p50 | 延迟 p95(ms) | 成本 | 模型 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for row in rows:
            lines.append(
                f"| {row['key']} | {row['calls']} | {row['input_total']} "
                f"| {_cell(row['input_p50'])} | {_cell(row['input_p95'])} | {_cell(row['input_max'])} "
                f"| {_cell(row['output_p50'])} | {_cell(row['latency_p95'])} "
                f"| {_cell(row['cost'])} | {', '.join(row['models']) or '—'} |"
            )
        lines += [
            "",
            "## 怎么读",
            "",
            "- `输入 total` 用于找成本大头；`p95` 用于判断单次 prompt 是否失控。",
            "- 同一个 prompt 有多个版本时，`versions` 会出现在原始 JSON 里；改 prompt 后应分开比较。",
            "- 这里只统计模型调用，不包含工具与图节点耗时；总延迟看 `eval.stream_latency.py`。",
        ]

    report_path = output_dir / "token-ledger-report.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def run(trace_path: Path, output_dir: Path) -> int:
    spans = load_spans(trace_path)
    rows = summarize(spans)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "token-ledger.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_path = write_report(rows, output_dir, trace_path)

    print(f"M8 完成：{rows[-1]['calls'] if rows else 0} 次模型调用；报告：{report_path}")
    for row in rows:
        print(
            f"  {row['key']}: calls={row['calls']} input_total={row['input_total']} "
            f"input_p95={_cell(row['input_p95'])} latency_p95={_cell(row['latency_p95'])}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    return run(args.trace, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
