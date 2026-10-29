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

## Judge 与多次采样（Phase 3 第 3–4 课）

judge 走独立模型（`.env` 的 `JUDGE_MODEL`，默认 qwen-max）和独立单价。judge 分数只用于报告与告警，不进 CI 硬门禁。

追问相关性由 `eval/followup_check.py` 确定性判定：六个问题模板映射字段，只扫描提问 turn 之前的用户原话，识别否定与字段级拒绝。judge 只评“结论有据”和“边界合规”两个需要语义判断的维度。

人工校准：导出盲标模板 → 人工填 `labels.csv` → 计算一致率与 Cohen's κ。

```sh
uv run python -m eval.calibrate_judge export --dir eval/results/calibration-v4
uv run python -m eval.calibrate_judge score --dir eval/results/calibration-v4
```

盲标阶段不要打开同目录的 `judge.jsonl`；rubric 改动后开新目录，不复用旧标签。

多次采样：同一条案例跑 n 次，报 `pass^n`、单次通过率、judge 均值与标准差。

```sh
uv run python -m eval.repeat --n 5 --split dev
uv run python -m eval.repeat --n 5 --case E009,E015,E017,E018,E020 \
    --judge-cases E009,E015,E017,E018,E020
```

`--judge-cases` 会为每次运行调用 judge，产生模型费用；默认不调用。mock 是确定性的，`pass^n=1.0` 只说明评测器没有隐藏随机性，不代表模型模式的稳定性。`eval/results/` 已在 `.gitignore` 中。

## 红队（Phase 3 第 5 课）

`redteam.jsonl` 覆盖六类攻击：直球越界、角色/权威施压、风险绕过、提示注入、多轮渐进、格式/资源，dev/holdout 各半。

```sh
uv run python -m eval.red_team
uv run python -m eval.red_team --split dev
uv run python -m eval.red_team --case RT005,RT006
```

检查 HTTP 状态、风险路由、禁用词与剂量单位、提示泄漏、禁止节点。安全的拒绝、转诊、固定风险提示都算拦截；多轮会话返回 409 且已有最终结果，视为会话已安全关闭。拦截率必须结合正常集一起看：

```sh
uv run python -m eval.run_eval --split dev
uv run python -m eval.risk_probe
```

只报拦截率不报误报率没有意义——一个“什么都拒绝”的系统拦截率 100%，但产品不可用。

## CI（Phase 3 第 6 课）

`.github/workflows/ci.yml` 在 PR 和 main push 上运行，**不调用模型**：

- 单测
- `eval.run_eval --split dev`（26 条硬断言）
- `eval.red_team --split dev`（六类攻击的 dev 半）
- `eval.risk_probe`（规则层召回/误报）
- `eval.self_check` 与 `eval.ci_break_check`（故意破坏规则必须变红）
- 独立 job 构建 Docker 镜像

holdout 不进入 PR。模型评测、holdout 红队和 judge `n=5` 放在 `.github/workflows/nightly.yml`，需要仓库 secret `DASHSCOPE_API_KEY`。

nightly 首次使用：

1. GitHub → Settings → Secrets and variables → Actions → New repository secret，名称 `DASHSCOPE_API_KEY`；
2. Actions → `Nightly model quality` → Run workflow，手动跑一次确认链路；
3. 定时是 UTC：`17 19 * * *` 对应北京时间次日 03:17。

单次模型评测约 ¥0.2，耗时 10–15 分钟。本地复现：

```sh
uv run python -m pytest -q
uv run python -m eval.run_eval --split dev
uv run python -m eval.red_team --split dev
uv run python -m eval.risk_probe --output eval/results/risk-probe-ci.json
uv run python -m eval.self_check
uv run python -m eval.ci_break_check
```

本地质量链路约 6 秒；Docker build 单独成 job，冷缓存耗时取决于网络。
