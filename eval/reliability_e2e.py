"""真实模型模式下的工具可靠性端到端回归。

只验证“重试/熔断在真实超时下是否按预期工作”，不评价医学质量。

流程：
1. 起一个可控故障 MCP server，暴露 `pubmed_search_articles`；
2. 健康模式问一次：确认正常回答不受影响；
3. 打开故障开关，连问三次：
   - 第 1 次：server 计数 +2，证明幂等工具真的重试了一次；
   - 第 2 次：再失败一次，熔断器打开；
   - 第 3 次：server 计数不变，证明调用被熔断短路；
4. 删除故障开关并等过冷却期：确认工具恢复，回答重新带上 PMID。

需要 .env 里有 DASHSCOPE_API_KEY。默认 USE_MODEL=1，会真实调用模型并产生费用；
调试链路时可用 `--mock`。

用法：
    PYTHONPATH=. uv run python -m eval.reliability_e2e
    PYTHONPATH=. uv run python -m eval.reliability_e2e --mock
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKDIR = Path(tempfile.mkdtemp(prefix="reliability-e2e-"))
MARKER = WORKDIR / "fault-on"
COUNTER = WORKDIR / "calls.log"
TRACE_PATH = WORKDIR / "traces.jsonl"
DB_PATH = WORKDIR / "consultation.sqlite"
SERVER_LOG = WORKDIR / "fault-server.log"

QUESTION = "PubMed 上有没有关于 hypertension 的研究？"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _configure_env(use_model: bool, port: int) -> None:
    os.environ["USE_MODEL"] = "1" if use_model else "0"
    os.environ["KNOWLEDGE_BACKEND"] = "stub"
    os.environ["CONSULTATION_DB_PATH"] = str(DB_PATH)
    os.environ["TRACE_SINK"] = "jsonl"
    os.environ["TRACE_PATH"] = str(TRACE_PATH)
    os.environ["MCP_ENABLED"] = "1"
    os.environ["MCP_SERVERS"] = json.dumps(
        [
            {
                "name": "fault-pubmed",
                "transport": "http",
                "url": f"http://127.0.0.1:{port}/mcp",
                "allowed_tools": ["pubmed_search_articles"],
                # 1 秒超时让真实超时在几秒内发生，而不是等默认的 8 秒。
                "timeout_s": 1.0,
                "start_timeout_s": 5.0,
            }
        ]
    )
    os.environ["TOOL_MAX_ATTEMPTS"] = "2"
    os.environ["TOOL_RETRY_BACKOFF_MS"] = "50"
    os.environ["TOOL_BREAKER_THRESHOLD"] = "3"
    os.environ["TOOL_BREAKER_COOLDOWN_S"] = "4"


def _start_server(port: int) -> tuple[subprocess.Popen, object]:
    env = dict(os.environ)
    env.update(
        {
            "FAULT_MARKER": str(MARKER),
            "FAULT_COUNTER": str(COUNTER),
            "FAULT_DELAY_S": "30",
        }
    )
    log = SERVER_LOG.open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "eval.fault_mcp_server",
            "--http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"故障 server 提前退出，日志：{SERVER_LOG.read_text()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return process, log
        except OSError:
            time.sleep(0.1)
    log.close()
    raise RuntimeError(f"故障 server 未在 15 秒内就绪，日志：{SERVER_LOG.read_text()}")


def _call_count() -> int:
    if not COUNTER.exists():
        return 0
    return len([line for line in COUNTER.read_text().splitlines() if line.strip()])


def _state_for(session_id: str) -> dict:
    from backend.app.api import routes

    snapshot = routes.get_app().get_state(
        {"configurable": {"thread_id": session_id}}
    )
    return dict(snapshot.values or {})


def _tool_outcome(state: dict) -> dict:
    return (state.get("tool_results") or {}).get("pubmed_search_articles") or {}


def _request(client, session_id: str | None, message: str) -> tuple[dict, float]:
    started = time.perf_counter()
    if session_id is None:
        response = client.post("/sessions", json={"initial_message": message})
    else:
        response = client.post(
            f"/sessions/{session_id}/messages", json={"message": message}
        )
    elapsed = time.perf_counter() - started
    if response.status_code != 200:
        raise AssertionError(
            f"HTTP {response.status_code}：{response.text[:500]}"
        )
    return response.json(), elapsed


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mock",
        action="store_true",
        help="用 mock 模型调试链路，不产生模型费用",
    )
    args = parser.parse_args()

    port = _free_port()
    _configure_env(use_model=not args.mock, port=port)

    from fastapi.testclient import TestClient

    from backend.app.api import routes
    from backend.app.tools.catalog import DEFAULT_CATALOG

    process, log = _start_server(port)
    try:
        routes.reset_app()
        routes.get_app()
        external = [
            name for name in DEFAULT_CATALOG.names() if name.startswith("pubmed_")
        ]
        with TestClient(routes.app) as client:
            print(f"模式：{'mock' if args.mock else '真实模型'}；故障 server：{port}")
            print("已注册外部工具：" + ("、".join(external) or "（无）"))

            # --- 1. 健康路径 ---
            before = _call_count()
            payload, elapsed = _request(client, None, QUESTION)
            session_id = payload["session_id"]
            text = payload.get("rendered_text", "")
            health_calls = _call_count() - before
            print(f"[健康] 回答：{text!r}")
            print(f"[健康] 工具结果：{_tool_outcome(_state_for(session_id))}")
            _check(health_calls == 1, f"健康调用次数应为 1，实际 {health_calls}")
            _check("PubMed" in text or "PMID" in text, f"健康回答缺少 PubMed 内容：{text!r}")
            print(f"[健康] {elapsed:.2f}s，server 调用 +{health_calls}，回答 {text[:60]!r}")

            # --- 2. 打开故障，第一轮应重试 ---
            MARKER.touch()
            before = _call_count()
            payload, elapsed = _request(client, session_id, QUESTION)
            first_calls = _call_count() - before
            first_outcome = _tool_outcome(_state_for(session_id))
            first_text = payload.get("rendered_text", "")
            _check(
                first_calls == 2,
                f"幂等工具应重试一次（server 计数 +2），实际 +{first_calls}",
            )
            _check(
                first_outcome.get("error_kind") == "timeout",
                f"首次故障应记录 timeout，实际 {first_outcome}",
            )
            _check(
                "不可用" in first_text or "暂无依据" in first_text,
                f"工具超时不能谎称“没有文献”：{first_text!r}",
            )
            print(
                f"[故障 1] {elapsed:.2f}s，server 调用 +{first_calls}，"
                f"error_kind={first_outcome.get('error_kind')}"
            )

            # --- 3. 第二轮再失败，熔断应打开 ---
            before = _call_count()
            payload, elapsed = _request(client, session_id, QUESTION)
            second_calls = _call_count() - before
            second_outcome = _tool_outcome(_state_for(session_id))
            _check(
                second_outcome.get("error_kind") in {"timeout", "unavailable"},
                f"第二次故障应仍是瞬时失败，实际 {second_outcome}",
            )
            print(
                f"[故障 2] {elapsed:.2f}s，server 调用 +{second_calls}，"
                f"error_kind={second_outcome.get('error_kind')}"
            )

            # --- 4. 第三轮应被熔断短路，不再打到 server ---
            before = _call_count()
            payload, elapsed = _request(client, session_id, QUESTION)
            third_calls = _call_count() - before
            third_outcome = _tool_outcome(_state_for(session_id))
            third_text = payload.get("rendered_text", "")
            _check(third_calls == 0, f"熔断期间不应再调用 server，实际 +{third_calls}")
            _check(
                "熔断" in str(third_outcome.get("error", "")),
                f"熔断结果应写明原因，实际 {third_outcome}",
            )
            _check(
                "不可用" in third_text or "暂无依据" in third_text,
                f"熔断期间不能谎称“没有文献”：{third_text!r}",
            )
            print(
                f"[熔断] {elapsed:.2f}s，server 调用 +{third_calls}，"
                f"error={third_outcome.get('error')!r}"
            )

            # --- 5. 恢复：删掉故障开关，等过冷却期，应重新拿到结果 ---
            MARKER.unlink()
            time.sleep(4.5)
            before = _call_count()
            payload, elapsed = _request(client, session_id, QUESTION)
            recovery_calls = _call_count() - before
            recovery_outcome = _tool_outcome(_state_for(session_id))
            recovery_text = payload.get("rendered_text", "")
            _check(
                "error_kind" not in recovery_outcome,
                f"恢复后不应再有工具错误，实际 {recovery_outcome}",
            )
            _check(
                "PMID" in recovery_text or "PubMed" in recovery_text,
                f"恢复后回答缺少 PubMed 内容：{recovery_text!r}",
            )
            print(
                f"[恢复] {elapsed:.2f}s，server 调用 +{recovery_calls}，"
                f"回答 {recovery_text[:60]!r}"
            )

        print(f"\n轨迹：{TRACE_PATH}")
        print(f"故障 server 日志：{SERVER_LOG}")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
