"""Phase 3 第 1 课：声明式硬断言的单元测试。"""

import json
from pathlib import Path

from eval.assertions import (
    CaseObservation,
    assertion_types,
    run_hard_assertions,
)


def _observation(**overrides) -> CaseObservation:
    base = dict(
        case={"id": "T001", "category": "normal", "input_turns": ["头部不适"]},
        response_status=200,
        nodes=["intake", "risk_check", "decision", "tool", "decision", "output"],
        tool_calls=["department_suggest"],
        result={
            "status": "completed",
            "known_facts": {},
            "unknowns": [],
            "tools_used": ["department_suggest"],
            "next_step": "建议就医",
            "risk_message": None,
            "limitations": "",
        },
        values={
            "followup_count": 0,
            "risk_flags": [],
            "asked_questions": [],
            "tool_results": {"department_suggest": {"department": "神经内科"}},
            "collected_facts": {"主要症状": ["头部不适"]},
        },
        trace=[{"node": "intake"}],
        messages_seen=1,
        restart_simulated=False,
        cross_session_clean=None,
    )
    base.update(overrides)
    return CaseObservation(**base)


def test_normal_case_assertions_pass():
    case = {
        "hard_assertions": [
            {"type": "api_status", "expected": 200},
            {"type": "risk_flags", "mode": "empty"},
            {"type": "trace_count_max", "node": "followup", "max": 1},
            {"type": "tool_calls_count", "tool": "department_suggest", "count": 1},
        ]
    }
    assert run_hard_assertions(case, _observation()) == []


def test_node_absent_failure_is_reported_with_type():
    case = {"hard_assertions": [{"type": "node_absent", "nodes": ["decision"]}]}
    failures = run_hard_assertions(case, _observation())
    assert any("[node_absent]" in item for item in failures)


def test_global_followup_limit_applies_to_every_case():
    failures = run_hard_assertions({}, _observation(values={"followup_count": 4}))
    assert any("[max_followups]" in item for item in failures)


def test_unknown_assertion_type_fails_instead_of_skipping():
    case = {"hard_assertions": [{"type": "typo_assertion"}]}
    failures = run_hard_assertions(case, _observation())
    assert any("[unknown_assertion]" in item for item in failures)


def test_facts_preserved_reports_missing_fragment():
    case = {
        "input_turns": ["头部不适两天"],
        "hard_assertions": [
            {"type": "facts_preserved", "tokens": ["头部不适", "两天"]}
        ],
    }
    failures = run_hard_assertions(
        case,
        _observation(case=case, values={"collected_facts": {"主要症状": ["头部不适"]}}),
    )
    assert any("两天" in item for item in failures)


def test_tool_result_field_checks_value():
    case = {
        "hard_assertions": [
            {
                "type": "tool_result_field",
                "tool": "department_suggest",
                "field": "department",
                "expected": None,
            }
        ]
    }
    failures = run_hard_assertions(case, _observation())
    assert any("[tool_result_field]" in item for item in failures)


def test_every_case_declares_known_assertions_and_split():
    cases = [
        json.loads(line)
        for line in Path("eval/cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    known = assertion_types()

    assert len(cases) == 33
    assert sum(case["split"] == "dev" for case in cases) == 26
    assert sum(case["split"] == "holdout" for case in cases) == 7

    for case in cases:
        assert case["hard_assertions"], f"{case['id']} 没有声明硬断言"
        for spec in case["hard_assertions"]:
            assert spec["type"] in known, f"{case['id']} 使用了未知断言 {spec['type']}"