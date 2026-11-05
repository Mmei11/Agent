"""Phase 6 L2：并发压测。

先跑基线，再改一个变量复测。指标口径：
- 5xx、超时、连接错误算失败；
- 429 是限流按预期生效，单独计数；
- 4xx 是业务校验（空消息、会话已结束），不算服务端错误。

用法：
    uv run uvicorn backend.app.api.routes:app --port 8765
    uv run python eval/load_test.py --base http://127.0.0.1:8765 \
        --concurrency 50 --turns 4 --endpoint stream --label baseline-mock
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

OUTPUT = Path("eval/results/load-test.json")

# 四轮对话覆盖：追问 → 补充 → 补充 → 收束。会话提前结束时收到 409 就停。
MESSAGES = [
    "头部不适",
    "前两天开始的",
    "前额，程度轻",
    "暂无其他不适，无病史",
]


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * ratio), len(ordered) - 1)
    return round(ordered[index], 1)


def _record(records: list[dict], user: int, kind: str, status: int, started: float, first_event=None):
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    records.append(
        {
            "user": user,
            "kind": kind,
            "status": status,
            "ms": elapsed,
            "first_event_ms": round(first_event, 1) if first_event else None,
        }
    )

async def _run_user(client, base: str, user: int, endpoint: str, turns: int, records: list, failures: list):
    """一个虚拟用户：建会话，然后连续发 turns 轮消息。"""
    try:
        started = time.perf_counter()
        response = await client.post(f"{base}/sessions", json={"initial_message": ""})
        _record(records, user, "create", response.status_code, started)
        if response.status_code != 200:
            return
        session_id = response.json()["session_id"]

        for turn in range(turns):
            message = MESSAGES[turn % len(MESSAGES)]
            started = time.perf_counter()
            first_event = None

            if endpoint == "stream":
                async with client.stream(
                    "POST",
                    f"{base}/sessions/{session_id}/stream",
                    json={"message": message},
                ) as stream:
                    status = stream.status_code
                    if status // 100 == 2:
                        first_line = True
                        async for line in stream.aiter_lines():
                            if first_line and line.startswith("data:"):
                                first_event = (time.perf_counter() - started) * 1000
                                first_line = False
                    else:
                        await stream.aread()
                _record(records, user, "stream", status, started, first_event)
            else:
                response = await client.post(
                    f"{base}/sessions/{session_id}/messages",
                    json={"message": message},
                )
                status = response.status_code
                _record(records, user, "message", status, started)

            # 会话已在上一轮结束，后续请求没有意义。
            if status in (404, 409):
                break
    except Exception as exc:  # 连接错误、超时等
        failures.append({"user": user, "error": type(exc).__name__, "detail": str(exc)[:200]})


async def run(args) -> dict:
    wall_started = time.perf_counter()
    records: list[dict] = []
    failures: list[dict] = []
    limits = httpx.Limits(max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency)
    headers = {"X-API-Key": args.api_key} if args.api_key else {}

    async with httpx.AsyncClient(
        limits=limits,
        timeout=httpx.Timeout(args.timeout),
        headers=headers,
    ) as client:
        if args.warmup:
            await _run_user(client, args.base, -1, args.endpoint, 1, [], [])

        await asyncio.gather(
            *[
                _run_user(client, args.base, user, args.endpoint, args.turns, records, failures)
                for user in range(args.concurrency)
            ]
        )

    elapsed = time.perf_counter() - wall_started
    latencies = [record["ms"] for record in records]
    status_counts = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0}
    for record in records:
        bucket = f"{record['status'] // 100}xx"
        status_counts[bucket] = status_counts.get(bucket, 0) + 1

    failures_count = status_counts["5xx"] + len(failures)
    total = len(records) + len(failures)
    first_events = [record["first_event_ms"] for record in records if record["first_event_ms"]]

    rejection_codes = (401, 409, 413, 429)
    rejections = {
        str(code): sum(1 for record in records if record["status"] == code)
        for code in rejection_codes
    }
    rejections = {code: count for code, count in rejections.items() if count}

    return {
        "label": args.label,
        "base": args.base,
        "endpoint": args.endpoint,
        "concurrency": args.concurrency,
        "turns": args.turns,
        "wall_time_s": round(elapsed, 2),
        "total_requests": total,
        "status_counts": status_counts,
        "rejections": rejections,
        "network_errors": len(failures),
        "error_rate": round(failures_count / total, 4) if total else 0.0,
        "throughput_rps": round(total / elapsed, 2) if elapsed else 0.0,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": round(max(latencies), 1) if latencies else 0.0,
        },
        "first_event_ms": {
            "p50": _percentile(first_events, 0.50),
            "p95": _percentile(first_events, 0.95),
            "p99": _percentile(first_events, 0.99),
        },
        "failures": failures[:20],
        "requests": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--endpoint", choices=["stream", "messages"], default="stream")
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--api-key", default="", help="AUTH_MODE=required 时携带的 X-API-Key")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--warmup", type=int, default=1, help="压测前先跑一个用户热身")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    result = asyncio.run(run(args))
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "requests"},
            ensure_ascii=False,
            indent=2,
        )
    )

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
    existing.append(result)
    output.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n已追加到 {output}")


if __name__ == "__main__":
    main()
