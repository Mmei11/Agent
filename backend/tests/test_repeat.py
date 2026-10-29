"""Phase 3 第 4 课：多次采样的离线测试。"""

from eval.repeat import aggregate_case, aggregate_judge, mean_std

DIMS = ("followup_relevance", "evidence_grounding", "boundary_compliance")


def _run(status: str, failures: list[str], scores: dict | None = None) -> dict:
    row = {"status": status, "failures": failures}
    if scores is not None:
        row["judge"] = {
            "scores": scores,
            "applicable": {dimension: True for dimension in DIMS},
            "cost_estimate": 0.001,
        }
    return row


def test_mean_std():
    mean, std = mean_std([5, 5, 4, 5, 5])

    assert mean == 4.8
    assert std == 0.447


def test_aggregate_case_reports_pass_pow_and_failures():
    case = {"id": "T001", "category": "normal", "split": "dev"}
    runs = [
        _run("pass", []),
        _run("pass", []),
        _run("fail", ["[max_followups] 追问 4 次，超过上限 3"]),
    ]

    row = aggregate_case(case, runs, judged=False)

    assert row["pass_count"] == 2
    assert row["pass_pow"] is False
    assert row["failures"] == ["[max_followups] 追问 4 次，超过上限 3"]


def test_aggregate_judge_pools_scores_and_pass_rate():
    case = {"id": "T001", "category": "multi_turn", "split": "dev"}
    runs = [
        _run(
            "pass",
            [],
            {
                "followup_relevance": 5,
                "evidence_grounding": 5,
                "boundary_compliance": 5,
            },
        ),
        _run(
            "pass",
            [],
            {
                "followup_relevance": 4,
                "evidence_grounding": 5,
                "boundary_compliance": 5,
            },
        ),
        _run(
            "pass",
            [],
            {
                "followup_relevance": 3,
                "evidence_grounding": 5,
                "boundary_compliance": 5,
            },
        ),
    ]

    case_row = aggregate_case(case, runs, judged=True)
    summary = aggregate_judge([case_row])

    detail = summary["followup_relevance"]
    assert detail["n"] == 3
    assert detail["mean"] == 4.0
    assert detail["pass_rate"] == 0.667


def test_aggregate_case_skips_not_applicable_dimension():
    case = {"id": "T001", "category": "normal", "split": "dev"}
    run = _run(
        "pass",
        [],
        {
            "followup_relevance": 5,
            "evidence_grounding": 5,
            "boundary_compliance": 5,
        },
    )
    run["judge"]["applicable"]["evidence_grounding"] = False

    row = aggregate_case(case, [run], judged=True)

    assert row["judge"]["evidence_grounding"]["scores"] == []
    assert row["judge"]["evidence_grounding"]["mean"] is None