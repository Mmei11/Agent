"""Phase 3 门禁自检：故意破坏关键规则，确认评测真的会变红。

用法：
    PYTHONPATH=. uv run python eval/self_check.py
"""

import io
from contextlib import redirect_stdout
from pathlib import Path

import backend.app.agent.graph as graph
from eval.run_eval import run

GUARD_CASES = Path("eval/guard_cases.jsonl")
OUTPUT_DIR = Path("/tmp/agent-gate-selfcheck")


def _run_with_limit(limit: int) -> int:
    original = graph.MAX_FOLLOWUPS
    graph.MAX_FOLLOWUPS = limit
    try:
        with redirect_stdout(io.StringIO()):
            return run(GUARD_CASES, OUTPUT_DIR, case_ids=["G001"])
    finally:
        graph.MAX_FOLLOWUPS = original


def main() -> int:
    normal = _run_with_limit(3)
    broken = _run_with_limit(10)

    report = (OUTPUT_DIR / "agent-eval-report.md").read_text(encoding="utf-8")
    caught = "[max_followups]" in report

    print(f"正常上限 3 -> exit {normal}（期望 0）")
    print(f"破坏上限 10 -> exit {broken}（期望 1）")
    print(f"报告包含 [max_followups] -> {caught}（期望 True）")

    if normal != 0 or broken != 1 or not caught:
        print("门禁自检失败：追问上限变化没有被拦住")
        return 1
    print("门禁自检通过：追问上限变化会被拦住")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())