# Day 16：案例集与单轮基线

`cases.jsonl` contains 33 fictional software-behavior cases across normal input,
multi-turn follow-up, preset risk, negation, vague input, invalid input, injected
tool failures, and session recovery. Each row has `input_turns`, expected
actions/tools, prohibited behaviors, and a human-review note. These are
workflow assertions, not claims about clinical quality.

## Single-turn baseline

The baseline concatenates every case's turns into one user message, calls the
configured chat model once, and records the raw answer, model name, and latency.
It does not run the graph, risk gate, or tools. Compare it with the Agent on the
same case IDs and model configuration; don't compare answers as if text
similarity measured clinical correctness.

```sh
USE_MODEL=1 uv run python -m eval.baseline --cases eval/cases.jsonl --output eval/results/baseline-results.jsonl
```

Run a small smoke batch with `--limit 3`. The output file is generated locally
and should not be committed because it may contain model responses. Tool
timeout/empty-result rows require a controlled injected tool in the Day 17
runner; the natural-language text in those rows alone does not trigger a
failure.

## Case contract

Each JSONL object contains:

- `id`, `category`, `input_turns`
- `expected_actions`, `expected_tools`
- `prohibited_behaviors`
- `human_review`

Expected action descriptions can be semantic when the case tests a family of
valid trajectories. Day 17 will turn these contracts into executable checks
for the application entry point and add controlled fault injection.
