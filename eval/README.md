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

- `id`, `split`, `category`, `input_turns`
- `expected_actions`, `expected_tools`
- `hard_assertions`: 声明式硬约束列表，runner 逐个执行
- `prohibited_behaviors`
- `human_review`

`split` 为 `dev` 或 `holdout`。dev 用于日常调试；holdout 只在发版前运行，避免看过答案再调规则。

Expected action descriptions can be semantic when the case tests a family of
valid trajectories. The Day 17 runner applies executable software-behavior
checks to the application entry point, with controlled tool fault injection.

## Day 17 Agent runner

Run the case set through FastAPI and the persisted graph with the deterministic
mock decision policy. The runner injects an empty tool result and a timeout for
the two tool-failure cases, and reopens SQLite during the recovery cases.

```sh
uv run python -m eval.run_eval
```

The runner writes `eval/results/agent-eval-results.jsonl` and
`eval/results/agent-eval-report.md`. It inspects the persisted graph trace to
check routing and tool use. Failures are recorded per case; the command exits
nonzero if any case fails.

### 分层与断言（Phase 3）

默认跑全部 33 条；日常只跑 dev，发版前再跑 holdout：

```sh
uv run python -m eval.run_eval --split dev
uv run python -m eval.run_eval --split holdout
```

单条调试：

```sh
uv run python -m eval.run_eval --case E001,E030
```

每条案例的 `hard_assertions` 支持：`api_status`、`node_present`、`node_absent`、
`node_count_max`、`result_status`、`risk_flags`、`trace_count_max`、
`asked_questions_unique`、`refusal_stops_followup`、`tool_calls_count`、
`tool_calls_present`、`tool_result_field`、`facts_preserved`、`restart_simulated`、
`cross_session_clean`。未知断言类型会直接判失败，不会静默跳过。

门禁自检：故意把追问上限从 3 改成 10，确认评测会失败：

```sh
uv run python -m eval.self_check
```

## Model-mode run (M4 baseline)

By default the runner forces the deterministic mock chooser. Pass `--use-model`
to let the real model decide; this calls the external API and costs money.

```sh
uv run python -m eval.run_eval --use-model --output-dir eval/results/phase0-model-baseline
```

Each row in the JSONL now carries `model_calls`, `fallback_calls`,
`input_tokens`, `output_tokens`, and `cost_estimate`; the report adds a usage
and latency section. `cost_estimate` stays `null` unless you provide prices:

```dotenv
MODEL_PRICE_INPUT_PER_1K=你的单价
MODEL_PRICE_OUTPUT_PER_1K=你的单价
```

Model mode is not deterministic, so a passing run is not evidence that the same
cases will pass next time. Record the model name and run date with the results.
Risk, vague, and invalid cases short-circuit before the decision node, so they
consume no tokens.
