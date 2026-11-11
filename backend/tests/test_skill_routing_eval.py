"""Phase A：M9 技能路由计分逻辑。"""

from types import SimpleNamespace

from eval.skill_routing import score


def test_disabled_samples_are_skipped():
    cases = [
        {"id": "SR01", "message": "头痛", "expected_skill": "headache", "enabled": False}
    ]
    result = score(cases, lambda message: "headache")

    assert result["enabled"] == 0
    assert result["accuracy"] is None
    assert result["rows"] == []


def test_accuracy_counts_exact_skill_match():
    cases = [
        {"id": "SR01", "message": "头痛", "expected_skill": "headache", "enabled": True},
        {"id": "SR04", "message": "孩子发烧", "expected_skill": "pediatric_fever", "enabled": True},
    ]

    def fake_router(message):
        return "headache" if message == "头痛" else "headache"

    result = score(cases, fake_router)

    assert result["enabled"] == 2
    assert result["accuracy"] == 0.5
    assert [row["correct"] for row in result["rows"]] == [True, False]


def test_router_may_return_object_with_skill():
    cases = [
        {"id": "SR01", "message": "头痛", "expected_skill": "headache", "enabled": True}
    ]
    result = score(cases, lambda message: SimpleNamespace(skill="headache"))

    assert result["accuracy"] == 1.0
