"""general / medical 路由评测的计分逻辑。"""

from types import SimpleNamespace

from backend.app.llm.client import LLMError
from eval.router_eval import score


def fake_router(message):
    intent = "medical" if ("疼" in message or "健康" in message) else "general"
    return SimpleNamespace(intent=intent), None


def test_score_counts_intent_accuracy():
    cases = [
        {"id": "R1", "message": "你好", "expected_intent": "general", "category": "general"},
        {"id": "R2", "message": "头疼", "expected_intent": "medical", "category": "medical"},
        {"id": "R3", "message": "健康不", "expected_intent": "general", "category": "general"},
    ]

    result = score(cases, fake_router)

    assert result["n"] == 3
    assert result["accuracy"] == round(2 / 3, 3)
    assert [row["correct"] for row in result["rows"]] == [True, True, False]


def test_score_records_llm_error_as_incorrect():
    cases = [
        {"id": "R1", "message": "你好", "expected_intent": "general", "category": "general"}
    ]

    def broken_router(message):
        raise LLMError("model down", retryable=True)

    result = score(cases, broken_router)

    assert result["accuracy"] == 0.0
    assert result["rows"][0]["correct"] is False
    assert result["rows"][0]["error"] == "model down"
