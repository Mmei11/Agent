"""Phase B：长会话保持评测（>20 轮完整图）。

必须用真实模型：滚动摘要和记忆指代路由都只在 USE_MODEL=1 时生效。
脚本通过 FastAPI TestClient 走完整图，记录摘要更新次数、探针命中和回答来源。

运行（会产生模型费用）：
    uv run python -m eval.long_session --use-model
    uv run python -m eval.long_session --use-model --split holdout
    uv run python -m eval.long_session --use-model --case LS01
"""

from __future__ import annotations

import os

os.environ.setdefault("KNOWLEDGE_BACKEND", "stub")
os.environ["RATE_LIMIT_ENABLED"] = "0"
os.environ["AUTH_MODE"] = "off"

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app
from backend.app.config import load_env_file
from backend.app.context.budget import ANSWER_RECENT_ITEMS

DEFAULT_CASES = Path("eval/probes/long_session.jsonl")
DEFAULT_OUTPUT = Path("eval/results/long-session")


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def reset_api(db_path: Path) -> None:
    routes.reset_app()
    os.environ["CONSULTATION_DB_PATH"] = str(db_path)


def state_for(session_id: str) -> dict[str, Any]:
    graph = routes.get_app()
    snapshot = graph.get_state({"configurable": {"thread_id": session_id}})
    return dict(snapshot.values or {})


def answer_of(response) -> str:
    data = response.json()
    if data.get("rendered_text"):
        return data["rendered_text"]
    for item in reversed(data.get("conversation") or []):
        if item.get("role") == "agent":
            return item.get("text", "")
    return ""


def _hit(text: str, expected: list[str]) -> bool:
    return all(str(value) in text for value in expected)


def evaluate_probe(expected: list[str], answer: str, state: dict[str, Any]) -> dict[str, Any]:
    """检查探针回答，并记录期望值出现在哪一层。"""
    memory = state.get("session_memory") or {}
    summary = str(memory.get("summary") or "")
    details = json.dumps(memory.get("details") or [], ensure_ascii=False)
    facts = json.dumps(state.get("collected_facts") or {}, ensure_ascii=False)
    window = json.dumps(
        (state.get("conversation") or [])[-ANSWER_RECENT_ITEMS:], ensure_ascii=False
    )
    return {
        "answer_hit": _hit(answer, expected),
        "summary_hit": _hit(summary, expected),
        "details_hit": _hit(details, expected),
        "facts_hit": _hit(facts, expected),
        "window_hit": _hit(window, expected),
        "summary_chars": len(summary),
        "summary_text": summary,
        "details_count": len(memory.get("details") or []),
    }


def _probe_row(
    case: dict[str, Any], turn: int, check: dict[str, Any], answer: str, state: dict[str, Any]
) -> dict[str, Any]:
    return {
        "turn": turn,
        "label": check.get("label", ""),
        "expected": check["expected"],
        "answer": answer[:300],
        **evaluate_probe(check["expected"], answer, state),
    }


def _failed_row(
    case: dict[str, Any], error: str, started: float, probes: list[dict] | None = None
) -> dict[str, Any]:
    probes = probes or []
    return {
        "id": case["id"],
        "split": case.get("split", "dev"),
        "turns": len(case.get("turns") or []),
        "summary_updates": 0,
        "summary_chars": 0,
        "details_count": 0,
        "probes": probes,
        "probe_total": len(probes),
        "probe_hits": sum(1 for probe in probes if probe["answer_hit"]),
        "latency_seconds": round(time.perf_counter() - started, 2),
        "error": error,
    }


def run_case(case: dict[str, Any], client: TestClient, db_path: Path) -> dict[str, Any]:
    reset_api(db_path)
    started = time.perf_counter()
    session_id: str | None = None
    state: dict[str, Any] = {}
    summary_updates = 0
    previous_index = 0
    probes: list[dict[str, Any]] = []
    checks = {int(item["turn"]): item for item in case.get("checks") or []}

    try:
        with client:
            turns = case["turns"]
            response = client.post("/sessions", json={"initial_message": turns[0]})
            if response.status_code != 200:
                return _failed_row(case, f"创建会话失败：HTTP {response.status_code}", started)

            session_id = response.json()["session_id"]
            state = state_for(session_id)
            previous_index = int((state.get("session_memory") or {}).get("summary_upto_index") or 0)
            if 1 in checks:
                probes.append(_probe_row(case, 1, checks[1], answer_of(response), state))

            for turn, message in enumerate(turns[1:], start=2):
                # 探针之前的快照：summary/details/facts/window 都用它，
                # 避免探针自己的回答把 window 点亮。
                before_state = state_for(session_id)
                response = client.post(
                    f"/sessions/{session_id}/messages", json={"message": message}
                )
                if response.status_code != 200:
                    return _failed_row(case, f"第 {turn} 轮失败：HTTP {response.status_code}", started, probes)

                state = state_for(session_id)
                current_index = int(
                    (state.get("session_memory") or {}).get("summary_upto_index") or 0
                )
                if current_index > previous_index:
                    summary_updates += 1
                    previous_index = current_index

                if turn in checks:
                    probes.append(
                        _probe_row(case, turn, checks[turn], answer_of(response), before_state)
                    )
    except Exception as exc:
        return _failed_row(case, f"{type(exc).__name__}: {exc}", started, probes)

    memory = state.get("session_memory") or {}
    return {
        "id": case["id"],
        "split": case.get("split", "dev"),
        "turns": len(case["turns"]),
        "summary_updates": summary_updates,
        "summary_chars": len(memory.get("summary") or ""),
        "details_count": len(memory.get("details") or []),
        "probes": probes,
        "probe_total": len(probes),
        "probe_hits": sum(1 for probe in probes if probe["answer_hit"]),
        "latency_seconds": round(time.perf_counter() - started, 2),
        "error": "",
    }


def write_report(rows: list[dict[str, Any]], output_dir: Path) -> Path:
    probes = [probe for row in rows for probe in row["probes"]]
    hits = sum(1 for probe in probes if probe["answer_hit"])
    total_updates = sum(row["summary_updates"] for row in rows)

    lines = [
        "# 长会话保持评测",
        "",
        "- 模式：真实模型（USE_MODEL=1，会产生费用）",
        f"- 脚本：{len(rows)}；探针：{len(probes)}；回答命中：{hits}",
        f"- 摘要更新总次数：{total_updates}",
        "",
        "## 脚本汇总",
        "",
        "| ID | 分层 | 轮次 | 摘要更新 | 摘要字符 | 细节条数 | 探针 | 命中 | 耗时(s) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['split']} | {row['turns']} | {row['summary_updates']} "
            f"| {row['summary_chars']} | {row['details_count']} | {row['probe_total']} "
            f"| {row['probe_hits']} | {row['latency_seconds']} |"
        )

    lines += [
        "",
        "## 逐探针",
        "",
        "| ID | 轮次 | 期望 | answer | summary | details | facts | window | 回答摘录 |",
        "|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        for probe in row["probes"]:
            answer = probe["answer"].replace("\n", " ")[:60]
            lines.append(
                f"| {row['id']} | {probe['turn']} | {'、'.join(probe['expected'])} "
                f"| {'✓' if probe['answer_hit'] else '✗'} "
                f"| {'✓' if probe['summary_hit'] else '✗'} "
                f"| {'✓' if probe['details_hit'] else '✗'} "
                f"| {'✓' if probe['facts_hit'] else '✗'} "
                f"| {'✓' if probe['window_hit'] else '✗'} | {answer} |"
            )

    lines += [
        "",
        "## 探针时的摘要",
        "",
        "| ID | 轮次 | 摘要 |",
        "|---|---:|---|",
    ]
    for row in rows:
        for probe in row["probes"]:
            summary = (probe.get("summary_text") or "").replace("\n", " ")[:160]
            lines.append(f"| {row['id']} | {probe['turn']} | {summary or '（空）'} |")

    lines += [
        "",
        "## 怎么读",
        "",
        "- `answer`：探针回答里是否逐字包含期望值。",
        "- `summary` / `details` / `facts` / `window`：期望值出现在哪一层。",
        "  `summary=✓` 且 `details=✗` 才说明回答必须依赖滚动摘要。",
        "- `摘要更新`：本轮 `summary_upto_index` 是否前进；24 轮脚本通常有 2–3 次。",
        "- 精确子串匹配会惩罚合理改写；模型换个说法但意思对时可能判 0。",
        "",
        "## 局限",
        "",
        "- 样本是虚构对话，脚本由项目编写，可能有措辞偏好。",
        "- 真实模型有随机性，单次结果不能当稳定结论；阶段末至少重跑 3 次。",
        "- 失败时先看探针的 `summary/details` 列，区分“摘要没保住”和“回答没用上摘要”。",
    ]

    report_path = output_dir / "long-session-report.md"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def run(
    cases_path: Path,
    output_dir: Path,
    *,
    use_model: bool = False,
    split: str = "dev",
    limit: int | None = None,
    case_ids: list[str] | None = None,
) -> int:
    if not use_model:
        print("长会话评测需要真实模型：请加 --use-model（会产生费用）")
        return 2

    load_env_file()
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("缺少 DASHSCOPE_API_KEY，无法运行长会话评测")
        return 2

    cases = load_cases(cases_path)
    if split != "all":
        cases = [case for case in cases if case.get("split", "dev") == split]
    if case_ids:
        wanted = set(case_ids)
        cases = [case for case in cases if case["id"] in wanted]
    if limit is not None:
        cases = cases[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)
    original_use_model = os.environ.get("USE_MODEL")
    original_summary = os.environ.get("SUMMARY_ENABLED")
    original_db = os.environ.get("CONSULTATION_DB_PATH")
    os.environ["USE_MODEL"] = "1"
    os.environ["SUMMARY_ENABLED"] = "1"
    client = TestClient(app, raise_server_exceptions=False)

    rows: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="long-session-") as temporary:
            temp = Path(temporary)
            for index, case in enumerate(cases):
                rows.append(run_case(case, client, temp / f"case-{index}.sqlite"))
            routes.reset_app()
    finally:
        for name, original in (
            ("USE_MODEL", original_use_model),
            ("SUMMARY_ENABLED", original_summary),
            ("CONSULTATION_DB_PATH", original_db),
        ):
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original

    (output_dir / "long-session-results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    report_path = write_report(rows, output_dir)

    total_probes = sum(row["probe_total"] for row in rows)
    total_hits = sum(row["probe_hits"] for row in rows)
    print(
        f"长会话评测完成：{len(rows)} 条脚本，{total_hits}/{total_probes} 探针命中；"
        f"报告：{report_path}"
    )
    for row in rows:
        print(
            f"  {row['id']}: turns={row['turns']} summary_updates={row['summary_updates']} "
            f"probes={row['probe_hits']}/{row['probe_total']} latency={row['latency_seconds']}s"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--use-model", action="store_true", help="必须加：本评测只跑真实模型")
    parser.add_argument("--limit", type=int, help="仅运行前 N 条脚本")
    parser.add_argument("--case", dest="case_ids", help="只跑指定脚本 ID，逗号分隔")
    parser.add_argument(
        "--split",
        choices=["all", "dev", "holdout"],
        default="dev",
        help="默认只跑 dev；holdout 留到阶段末",
    )
    args = parser.parse_args()
    case_ids = (
        [item.strip() for item in args.case_ids.split(",") if item.strip()]
        if args.case_ids
        else None
    )
    return run(
        args.cases,
        args.output_dir,
        use_model=args.use_model,
        split=args.split,
        limit=args.limit,
        case_ids=case_ids,
    )


if __name__ == "__main__":
    raise SystemExit(main())
