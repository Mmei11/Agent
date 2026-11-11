"""Phase A：M8 token 账本聚合逻辑。"""

from eval.token_ledger import pct, summarize


def make_span(name, prompt, input_tokens, output_tokens, duration_ms, cost=0.0):
    return {
        "name": name,
        "duration_ms": duration_ms,
        "attributes": {
            "gen_ai.usage.input_tokens": input_tokens,
            "gen_ai.usage.output_tokens": output_tokens,
            "prompt.name": prompt,
            "gen_ai.response.model": "qwen-plus",
            "llm.cost_estimate": cost,
        },
    }


def test_summarize_groups_by_prompt_and_adds_overall():
    rows = summarize(
        [
            make_span("llm.chat_structured", "intake.extract", 1200, 60, 2000, 0.001),
            make_span("llm.chat_structured", "intake.extract", 1800, 40, 3000, 0.002),
            make_span("llm.chat_structured", "decision.choose", 300, 20, 1500, 0.0005),
        ]
    )
    by_key = {row["key"]: row for row in rows}

    assert by_key["intake.extract"]["calls"] == 2
    assert by_key["intake.extract"]["input_total"] == 3000
    assert by_key["intake.extract"]["input_p50"] == 1500.0
    assert by_key["全部"]["calls"] == 3
    assert by_key["全部"]["input_total"] == 3300


def test_summarize_ignores_spans_without_usage():
    assert summarize([{"name": "tool", "attributes": {"x": 1}}]) == []


def test_pct_interpolates():
    assert pct([1, 2, 3], 0.5) == 2.0
    assert pct([10, 20], 0.95) == 19.5
    assert pct([], 0.5) is None
