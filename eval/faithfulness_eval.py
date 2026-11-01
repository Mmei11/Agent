"""Phase 4：忠实度评测——结论是否被检索到的内容支撑。

这里测的是**结构化结论**：科室是不是来自命中的条目、每条命中是否带得出
出处、没有依据时有没有老实返回 None。全部可确定性判定，所以能当硬门禁。

自由文本的忠实度（渲染文案有没有超出材料）仍由 Phase 3 的 judge 负责，
那条是 advisory，不进 CI。

用法：
    uv run python -m eval.faithfulness_eval
"""

import argparse
import json
from pathlib import Path

from backend.app.tools.contract import ToolResult
from backend.app.tools.registry import call_tool

DEFAULT_CASES = Path("eval/retrieval_cases.jsonl")
TARGET = 0.9

# 没有对应知识的问题：正确行为是明确说"没依据"，而不是编一个。
NO_EVIDENCE_QUERIES = [
    "我想问问天气",
    "帮我写首诗",
    "今天吃什么好",
    "说不上来哪里不舒服",
    "明天要上班吗",
    "给我讲个笑话",
    "推荐一部电影",
    "股票会涨吗",
    "帮我写段代码",
    "你好",
]

TOOL_BY_CATEGORY = {
    "department_route": "department_route",
    "red_flag": "red_flag_lookup",
    "visit_prep": "guideline_retrieve",
}


def _load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _check_provenance(hits: list[dict]) -> list[str]:
    """每条命中都必须能说清出处，否则就是不可追溯的建议。"""
    problems = []
    for hit in hits:
        source = hit.get("source") or {}
        if not source.get("id") or not source.get("title"):
            problems.append(f"命中 {hit.get('id')} 缺少出处")
        if not hit.get("scope_text"):
            problems.append(f"命中 {hit.get('id')} 缺少适用范围")
        if not hit.get("version"):
            problems.append(f"命中 {hit.get('id')} 缺少版本")
    return problems


def _check_department(data: dict) -> list[str]:
    hits = data.get("hits") or []
    department = data.get("department")

    if department is None:
        # 没有依据时必须什么都没给，不能一边说 None 一边塞一堆命中
        return [] if not hits else ["返回 department=None 却带着命中条目"]

    supported = {hit.get("department") for hit in hits}
    if department not in supported:
        return [f"科室 {department} 不在命中条目的科室集合里"]
    return []


def _check_red_flag(data: dict) -> list[str]:
    hits = data.get("hits") or []
    flag_ids = data.get("flag_ids") or []
    problems = []
    if data.get("uncertain") != (not hits):
        problems.append("uncertain 与命中情况不一致")
    returned = {hit.get("id") for hit in hits}
    if not set(flag_ids) <= returned:
        problems.append("flag_ids 里出现了未命中的条目")
    return problems


def _check_guideline(data: dict) -> list[str]:
    hits = data.get("hits") or []
    if data.get("no_match") != (not hits):
        return ["no_match 与命中情况不一致"]
    return []


def _check_no_evidence(category: str, data: dict) -> list[str]:
    """无对应知识的问题：三个工具都该明确说"没有"，而不是硬给一条。"""
    hits = data.get("hits") or []
    if hits:
        return [f"无依据问题却返回 {len(hits)} 条命中"]
    if category == "department_route" and data.get("department") is not None:
        return ["无依据问题却给出了科室"]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="结构化忠实度评测")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    cases = _load_cases(args.cases)
    checks: list[tuple[str, bool, str]] = []

    for case in cases:
        tool = TOOL_BY_CATEGORY[case["category"]]
        result: ToolResult = call_tool(tool, {"query": case["query"]})
        if not result.ok:
            checks.append((case["id"], False, f"工具失败：{result.error_kind}"))
            continue

        data = result.data or {}
        problems = _check_provenance(data.get("hits") or [])
        if case["category"] == "department_route":
            problems += _check_department(data)
        elif case["category"] == "red_flag":
            problems += _check_red_flag(data)
        else:
            problems += _check_guideline(data)

        checks.append((case["id"], not problems, "；".join(problems)))

    for index, query in enumerate(NO_EVIDENCE_QUERIES, 1):
        for category, tool in TOOL_BY_CATEGORY.items():
            result = call_tool(tool, {"query": query})
            label = f"N{index:02d}/{category}"
            if not result.ok:
                checks.append((label, False, f"工具失败：{result.error_kind}"))
                continue
            problems = _check_no_evidence(category, result.data or {})
            checks.append((label, not problems, "；".join(problems)))

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    score = passed / total if total else 0.0

    print(f"faithfulness(structured): {passed}/{total} = {score:.3f}  (目标 ≥ {TARGET})")

    failures = [(label, detail) for label, ok, detail in checks if not ok]
    if failures:
        print(f"\n未通过 {len(failures)} 条：")
        for label, detail in failures[:20]:
            print(f"  {label}: {detail}")

    if args.verbose:
        for label, ok, detail in checks:
            print(f"{'✓' if ok else '✗'} {label} {detail}")

    return 0 if score >= TARGET else 1


if __name__ == "__main__":
    raise SystemExit(main())