"""Phase 2 第二课：从本地 JSONL 轨迹回答两个问题。

会话视图：这次问诊经过了哪些环节，哪一步最慢，模型看到了什么结果。
质量视图：跨会话的 p50/p95、token、成本与错误数。

用法：
    PYTHONPATH=. uv run python eval/trace_report.py --path logs/traces.jsonl
    PYTHONPATH=. uv run python eval/trace_report.py --session <session_id 前缀>
"""

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _load(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # 一行写坏不应该让整份报告失败。
            continue
    return records


def _percentile(values: list[int], percent: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percent / 100) - 1)
    return ordered[index]


def _trace_total_ms(spans: list[dict]) -> int:
    starts = [span["started_at"] for span in spans if span.get("started_at")]
    ends = [span["ended_at"] for span in spans if span.get("ended_at")]
    if not starts or not ends:
        return 0
    delta = datetime.fromisoformat(max(ends)) - datetime.fromisoformat(min(starts))
    return round(delta.total_seconds() * 1000)


def _usage(spans: list[dict]) -> tuple[int, int, float | None]:
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    has_cost = False
    for span in spans:
        attributes = span.get("attributes") or {}
        input_tokens += attributes.get("gen_ai.usage.input_tokens") or 0
        output_tokens += attributes.get("gen_ai.usage.output_tokens") or 0
        if attributes.get("llm.cost_estimate") is not None:
            cost += attributes["llm.cost_estimate"]
            has_cost = True
    return input_tokens, output_tokens, round(cost, 6) if has_cost else None


def _detail(attributes: dict) -> str:
    keys = (
        "final_action",
        "risk_level",
        "extractor",
        "called",
        "selected_field",
        "prompt.version",
        "gen_ai.usage.input_tokens",
        "decision.action",
        "decision.confidence",
        "decision.missing_fields",
    )
    parts = [f"{key}={attributes[key]}" for key in keys if key in attributes]
    return ("  " + " ".join(parts)) if parts else ""


def _print_tree(spans: list[dict]) -> None:
    known_ids = {span["span_id"] for span in spans}
    children = defaultdict(list)
    for span in spans:
        parent = span.get("parent_span_id")
        # 父节点不在这条 trace 里时，当根节点处理，避免断链导致整棵树不显示。
        children[parent if parent in known_ids else None].append(span)
    for bucket in children.values():
        bucket.sort(key=lambda span: span.get("started_at", ""))

    def walk(parent_id, depth):
        for span in children.get(parent_id, []):
            error = ""
            if span.get("status") == "error":
                error = f"  ERROR={span.get('error')}"
            print(
                f"    {'  ' * depth}- {span['name']:<22} "
                f"{span.get('duration_ms', 0):>5}ms"
                f"{_detail(span.get('attributes') or '')}"
                f"{error}"
            )
            walk(span["span_id"], depth + 1)

    walk(None, 0)


def _print_session(session_id: str, spans: list[dict]) -> None:
    traces = defaultdict(list)
    for span in spans:
        traces[span["trace_id"]].append(span)
    ordered = sorted(traces.items(), key=lambda item: min(s["started_at"] for s in item[1]))

    print(f"\n会话 {session_id}：{len(ordered)} 轮，{len(spans)} 个 span")
    for trace_id, items in ordered:
        turn_id = items[0].get("turn_id")
        input_tokens, output_tokens, cost = _usage(items)
        cost_text = "—" if cost is None else f"¥{cost}"
        print(
            f"  trace {trace_id[:8]} turn={turn_id} "
            f"总耗时={_trace_total_ms(items)}ms "
            f"token={input_tokens}/{output_tokens} 成本={cost_text}"
        )
        _print_tree(items)


def _print_quality(records: list[dict]) -> None:
    sessions = defaultdict(list)
    for record in records:
        sessions[record["session_id"]].append(record)

    trace_count = len({record["trace_id"] for record in records})
    error_count = sum(1 for record in records if record.get("status") == "error")

    print("\n质量视图")
    print(f"  会话 {len(sessions)} / trace {trace_count} / span {len(records)} / 错误 {error_count}")

    node_durations = defaultdict(list)
    for record in records:
        if record.get("kind") == "node":
            node_durations[record["name"]].append(record.get("duration_ms", 0))
    if node_durations:
        print("  节点耗时：")
        for name in sorted(node_durations):
            values = node_durations[name]
            print(
                f"    {name:<14} n={len(values):<3} "
                f"p50={_percentile(values, 50):>4}ms "
                f"p95={_percentile(values, 95):>4}ms "
                f"max={max(values):>4}ms"
            )

    print("  每会话：")
    for session_id, items in sessions.items():
        input_tokens, output_tokens, cost = _usage(items)
        durations = [item.get("duration_ms", 0) for item in items]
        errors = sum(1 for item in items if item.get("status") == "error")
        cost_text = "—" if cost is None else f"¥{cost}"
        print(
            f"    {session_id[:8]} spans={len(items):<3} "
            f"p95={_percentile(durations, 95):>4}ms "
            f"token={input_tokens}/{output_tokens} 成本={cost_text} 错误={errors}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="从 JSONL 轨迹生成会话视图与质量视图")
    parser.add_argument("--path", default="logs/traces.jsonl", help="JSONL 轨迹文件")
    parser.add_argument("--session", help="只看某个会话，支持 ID 前缀")
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        print(f"没有找到轨迹文件：{path}")
        print("先用 TRACE_SINK=jsonl 跑一次带埋点的请求。")
        return

    records = _load(path)
    if not records:
        print(f"轨迹文件是空的：{path}")
        return

    sessions = defaultdict(list)
    for record in records:
        sessions[record["session_id"]].append(record)

    selected = [args.session] if args.session else sorted(sessions)
    for session_id in selected:
        if session_id in sessions:
            _print_session(session_id, sessions[session_id])
            continue
        matches = [key for key in sessions if key.startswith(session_id)]
        if not matches:
            print(f"没有找到会话：{session_id}")
        for key in matches:
            _print_session(key, sessions[key])

    _print_quality(records)


if __name__ == "__main__":
    main()