"""Phase 4：检索召回评测。

对 30 条标注查询跑双路检索，统计 recall@k——期望条目有没有被捞进前 k 条。
只看"找没找到"，不看"答得好不好"；后者由 faithfulness 评测负责。

用法：
    uv run python -m eval.recall_eval
    uv run python -m eval.recall_eval --threshold 0.55 --verbose
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from backend.app.knowledge.retrieval import (
    DEFAULT_LAMBDA,
    DEFAULT_THRESHOLD,
    retrieve,
)

DEFAULT_CASES = Path("eval/retrieval_cases.jsonl")
TARGET_RECALL = 0.85


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检索召回评测")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--lam", type=float, default=DEFAULT_LAMBDA)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    per_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    failures: list[tuple[dict, list[str]]] = []
    passed = 0

    for case in cases:
        results = retrieve(
            case["query"],
            top_k=args.top_k,
            threshold=args.threshold,
            lam=args.lam,
            category=case["category"],
        )
        returned = [hit.id for hit in results]
        ok = any(expected in returned for expected in case["expected"])

        bucket = per_category[case["category"]]
        bucket[1] += 1
        if ok:
            passed += 1
            bucket[0] += 1
        else:
            failures.append((case, returned))

        if args.verbose:
            mark = "✓" if ok else "✗"
            print(f"{mark} {case['id']} {case['query']} -> {returned}")

    total = len(cases)
    recall = passed / total if total else 0.0

    print()
    print(f"recall@{args.top_k}: {passed}/{total} = {recall:.3f}"
          f"  (threshold={args.threshold}, lambda={args.lam})")
    for category, (hits, count) in sorted(per_category.items()):
        print(f"  {category:18s} {hits}/{count} = {hits / count:.3f}")

    if failures:
        print()
        print(f"未命中 {len(failures)} 条：")
        for case, returned in failures:
            print(f"  {case['id']} 期望 {case['expected']} 实际 {returned}  ← {case['query']}")

    return 0 if recall >= TARGET_RECALL else 1


if __name__ == "__main__":
    raise SystemExit(main())