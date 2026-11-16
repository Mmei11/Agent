"""E0 语义红队数据集的结构约束，防止后续编辑把样本改坏。"""

import json
import re
from collections import Counter
from pathlib import Path

CASES_PATH = Path(__file__).resolve().parents[2] / "eval" / "semantic_redteam.jsonl"


def _cases() -> list[dict]:
    return [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_semantic_redteam_dataset_shape():
    cases = _cases()

    assert len(cases) == 60
    assert len({case["id"] for case in cases}) == len(cases)

    counts = Counter(case["category"] for case in cases)
    assert counts == {
        "implicit_red_flag": 10,
        "indirect_medication": 8,
        "citation_fabrication": 6,
        "anxiety_pressure": 6,
        "authority_pressure": 6,
        "prompt_injection": 6,
        "multi_turn": 6,
        "benign_control": 12,
    }

    for case in cases:
        assert case["split"] in {"dev", "holdout"}
        assert case["input_turns"]
        assert all(isinstance(turn, str) and turn.strip() for turn in case["input_turns"])
        if case["category"] == "benign_control":
            assert case["expect"] == "answered"
        else:
            assert case.get("expect") != "answered"
            assert case.get("expected_result") in {"safe", "risk_route"}
            for pattern in case.get("forbidden_regex", []):
                re.compile(pattern)
