"""Phase 3 第 2 课：跑真实案例并观察 judge 打分。

案例本身用确定性 mock 执行（不花钱、可复现），只有 judge 调真实模型。

运行：
    python -m eval.judge_demo --case E009
    python -m eval.judge_demo --split dev --limit 10 --reasons
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.api import routes
from backend.app.api.routes import app
from backend.app.llm.client import LLMError
from eval.judge import DIMENSIONS, judge_case, verdict
from eval.run_eval import TOOL_FIXTURE

LABELS = {
    "followup_relevance": "追问",
    "evidence_grounding": "有据",
    "boundary_compliance": "边界",
}


def run_case(case: dict, client: TestClient, db_path: Path) -> dict:
    """用 API 跑完一条案例，返回 graph 里的完整状态。"""
    os.environ["CONSULTATION_DB_PATH"] = str(db_path)
    routes._saver = None
    routes._compiled_app = None
    # 与 run_eval 保持一致：故障案例用信息完整的固定输入，才能走到工具节点。
    input_turns = list(case["input_turns"])
    if case["category"] == "tool_failure":
        input_turns = [TOOL_FIXTURE]
    with client:
        response = client.post(
            "/sessions", json={"initial_message": input_turns[0]}
        )
        session_id = response.json()["session_id"]
        for message in input_turns[1:]:
            client.post(f"/sessions/{session_id}/messages", json={"message": message})
    graph = routes.get_app()
    snapshot = graph.get_state({"configurable": {"thread_id": session_id}})
    return dict(snapshot.values or {})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="只跑指定案例，逗号分隔，例如 E009,E017")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="dev")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--cases", type=Path, default=Path("eval/cases.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("eval/results/judge-smoke.jsonl"))
    parser.add_argument("--reasons", action="store_true", help="打印每一维的 reason/evidence")
    args = parser.parse_args()

    cases = [
        json.loads(line)
        for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if args.case:
        wanted = {item.strip() for item in args.case.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted]
    else:
        if args.split != "all":
            cases = [case for case in cases if case.get("split", "dev") == args.split]
        cases = cases[: args.limit]

    original = os.environ.get("USE_MODEL")
    os.environ["USE_MODEL"] = "0"
    rows: list[dict] = []
    try:
        with tempfile.TemporaryDirectory(prefix="judge-demo-") as temporary:
            client = TestClient(app)
            for index, case in enumerate(cases):
                values = run_case(case, client, Path(temporary) / f"case-{index}.sqlite")
                started = time.perf_counter()
                try:
                    result, usage = judge_case(values)
                except LLMError as exc:
                    print(f"{case['id']}  judge 失败：{exc}")
                    rows.append({"case_id": case["id"], "error": str(exc)})
                    continue
                elapsed = round((time.perf_counter() - started) * 1000)
                summary = verdict(result, values)
                row = {
                    "case_id": case["id"],
                    **summary,
                    "details": {
                        name: getattr(result, name).model_dump() for name in DIMENSIONS
                    },
                    "cost_estimate": usage.cost_estimate,
                    "judge_latency_ms": elapsed,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                }
                rows.append(row)
                scores = " / ".join(
                    f"{LABELS[name]} "
                    f"{summary['scores'][name] if summary['applicable'][name] else 'N/A'}"
                    for name in DIMENSIONS
                )
                mark = "PASS" if summary["passed"] else "FAIL"
                print(
                    f"{case['id']}  {mark:4}  {scores}  "
                    f"{elapsed / 1000:.1f}s  ¥{usage.cost_estimate or 0:.5f}"
                )
                if args.reasons:
                    for name in DIMENSIONS:
                        detail = row["details"][name]
                        print(f"    {name}: {detail['score']} | {detail['reason']}")
                        print(f"    evidence: {detail['evidence']}")
    finally:
        if original is None:
            os.environ.pop("USE_MODEL", None)
        else:
            os.environ["USE_MODEL"] = original
        if routes._saver is not None:
            routes._saver.conn.close()
        routes._saver = None
        routes._compiled_app = None

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    scored = [row for row in rows if "scores" in row]
    passed = sum(row["passed"] for row in scored)
    total_cost = round(sum(row["cost_estimate"] or 0 for row in scored), 6)
    print(f"\n{passed}/{len(scored)} 通过；累计成本 ¥{total_cost}；结果：{args.output}")
    return 0 if passed == len(scored) else 1


if __name__ == "__main__":
    raise SystemExit(main())