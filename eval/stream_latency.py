"""Phase 6 L1：流式接口的首事件延迟与总时长。

启动服务后另开一个终端运行：
    uv run uvicorn backend.app.api.routes:app --port 8000
    uv run python eval/stream_latency.py
    USE_MODEL=1 uv run python eval/stream_latency.py --label model

首事件 meta 只证明连接建立；更有意义的是第一个 node 进度事件，
它代表"用户第一次看到系统在工作"。最终 result 事件是完整文案到达的时间。
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx

MESSAGE = "头部不适"
OUTPUT = Path("eval/results/stream-latency.json")


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(int(len(ordered) * ratio), len(ordered) - 1)
    return round(ordered[index], 1)


def run_once(client: httpx.Client, base: str) -> dict:
    created = client.post(f"{base}/sessions", json={"initial_message": ""}, timeout=30)
    created.raise_for_status()
    session_id = created.json()["session_id"]

    started = time.perf_counter()
    first_event = None
    first_progress = None
    first_result = None
    events: list[str] = []

    with client.stream(
        "POST",
        f"{base}/sessions/{session_id}/stream",
        json={"message": MESSAGE},
        timeout=120,
    ) as response:
        response.raise_for_status()
        event_name = "message"
        for line in response.iter_lines():
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                elapsed = (time.perf_counter() - started) * 1000
                events.append(event_name)
                if first_event is None:
                    first_event = elapsed
                if first_progress is None and event_name == "node":
                    first_progress = elapsed
                if first_result is None and event_name == "result":
                    first_result = elapsed

    return {
        "session_id": session_id,
        "first_event_ms": round(first_event or 0, 1),
        "first_progress_ms": round(first_progress or 0, 1),
        "first_result_ms": round(first_result or 0, 1),
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--label", default="mock", help="mock / model，仅用于结果文件")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    runs = []
    with httpx.Client(base_url=args.base) as client:
        for _ in range(args.rounds):
            runs.append(run_once(client, args.base))

    summary = {
        "label": args.label,
        "rounds": args.rounds,
        "message": MESSAGE,
        "first_event_ms": {
            "p50": _percentile([r["first_event_ms"] for r in runs], 0.5),
            "p95": _percentile([r["first_event_ms"] for r in runs], 0.95),
        },
        "first_progress_ms": {
            "p50": _percentile([r["first_progress_ms"] for r in runs], 0.5),
            "p95": _percentile([r["first_progress_ms"] for r in runs], 0.95),
        },
        "first_result_ms": {
            "p50": _percentile([r["first_result_ms"] for r in runs], 0.5),
            "p95": _percentile([r["first_result_ms"] for r in runs], 0.95),
        },
        "total_ms": {
            "p50": _percentile([r["total_ms"] for r in runs], 0.5),
            "p95": _percentile([r["total_ms"] for r in runs], 0.95),
        },
        "runs": runs,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if output.exists():
        try:
            existing = json.loads(output.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except json.JSONDecodeError:
            existing = []
    existing.append(summary)
    output.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已追加到 {output}")


if __name__ == "__main__":
    main()
