"""Phase 2 第一课：本地跑一条模拟 trace，观察 span 树与 JSONL 产物。

用法：
    PYTHONPATH=. TRACE_SINK=jsonl uv run python eval/trace_demo.py
"""

import os
import time

from backend.app.observability import span, start_trace


def main() -> None:
    os.environ.setdefault("TRACE_SINK", "jsonl")
    os.environ.setdefault("TRACE_PATH", "logs/traces-demo.jsonl")

    # 模拟一次就诊：intake 和 decision 各含一次模型调用。
    with start_trace("demo-session-1", 1) as trace:
        with span("intake", kind="node") as intake_span:
            with span(
                "llm.intake",
                kind="llm",
                attributes={"gen_ai.request.model": "qwen-plus"},
            ):
                time.sleep(0.02)
            intake_span.attributes["fact_count"] = 3

        with span("risk_check", kind="node", attributes={"risk_level": "safe"}):
            time.sleep(0.005)

        with span("decision", kind="node", attributes={"followup_count": 0}):
            with span(
                "llm.decision",
                kind="llm",
                attributes={"gen_ai.usage.input_tokens": 820},
            ):
                time.sleep(0.01)

    print(f"trace_id={trace.trace_id} spans={len(trace.spans)}")
    for record in trace.spans:
        parent = record.parent_span_id[-6:] if record.parent_span_id else "root"
        print(
            f"  {record.name:<14} kind={record.kind:<5} "
            f"parent={parent:<6} {record.duration_ms}ms status={record.status}"
        )
    print(f"\n落盘：{os.environ['TRACE_PATH']}")


if __name__ == "__main__":
    main()