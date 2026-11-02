# Phase 5：分层安全（输入层 / 决策层 / 输出层）

## Context

Phase 4 结束时，安全能力仍是 `backend/app/safety/rule_engine.py` 一层：
`screen()` 在 `graph.py` 的 `risk_check` 节点跑红旗/含糊词，`final_check()`
在 `output` 节点跑字面禁词与剂量正则。

问题不在规则不够聪明，而在三处：

1. **没有度量**：不知道漏多少红旗、误报多少，M2 严格召回只有 0.3125，且没有 holdout。
2. **输出层可被同义改写绕过**："不用去医院，吃点消炎药就行"没有任何字面禁词，但同样越界。
3. **决策层没有优先级与审计**：模型选了动作之后，没有"红旗必须压过模型"的策略层；
   风险升级也没有可回放的审计记录。

目标：把单层关键词升级成三道可独立失效的防线，用 guardrails-ai 做输出校验，
建 50 条对抗集把"漏多少、误报多少"变成可度量、可进 CI 的数字。

**已确认的决策：**

- 输出层采用 `guardrails-ai`。
- 接受把 `openai==3.3.0` 降到 `2.54.0`：guardrails-ai 0.11.0 硬性要求
  `openai>=2,<3`；instructor 1.17.0 要求 `openai>=2,<4`，交集是 2.x。

---

## 设计原则

1. **防御纵深**：输入层、决策层、输出层各自能独立失效，不把安全性押在"模型很乖"上。
2. **规则 / 模型 / 策略分离**：规则管高确定性硬约束，模型管语义覆盖，
   策略层管组合、优先级与升级，三者不塞进一个函数。
3. **代价不对称**：漏一条红旗是事故，多报一条只是打扰。红旗召回硬门禁 100%，
   精确率 ≥ 0.85。
4. **先度量后调参**：对抗集拆成调参集与 holdout，避免"规则照着测试集写"。

---

## 关键设计决策

- **`rule_engine.py` 保留为规则原语**，不删除、不重命名。新增 rail 模块调用它，
  避免 228 条测试、`eval/risk_probe.py`、`eval/ci_break_check.py` 断链。
- **策略配置用 JSON**（不新增 PyYAML 依赖），放
  `backend/app/safety/policies/safety-policy.json`，便于评审和改阈值。
- **审计独立于 trace**：`backend/app/safety/audit.py` 写 `logs/audit.jsonl`，
  同时 emit 一个 `safety.escalation` span；写盘前复用
  `backend/app/observability/redact.py`，只落脱敏摘要 + 短哈希，不落原话。
- **guardrails 不做 `reask`**：reask 会触发第二次模型调用，增加延迟和成本。
  统一 `on_fail="fix"` + 固定安全兜底文案，保持离线可测。
- **guardrails 自定义 validator 用类形式**：`@register_validator(name=..., data_type="string")`
  + 覆写 `_validate(self, value, metadata)`，失败返回
  `FailResult(error_message=..., on_fix=SAFE_FALLBACK)`。
- **只引入 guardrails 的校验器能力，不引入它的 LLM validator**，避免 litellm
  再发一次模型请求；"第二意见模型"留到 L5 可选，默认关闭。

---

## 课程与改动

### L1 输入层 + 依赖预检

先做依赖预检，避免后面四课建在坏地基上：

1. `uv add guardrails-ai==0.11.0`（resolver 会把 openai 降到 2.x）。
2. `uv run pytest` 跑完现有 228 条。
3. `USE_MODEL=1` 做一次百炼真实调用，确认 `chat.completions.create` 与
   instructor 的 `create_with_completion` 正常。

再写输入层：

- 新增 `backend/app/safety/input_rail.py`
  - `normalize()`：NFKC、lower、去零宽字符；空格/标点穿插交给 `rule_engine` 现有正则。
  - 越狱/提示注入模式表：中英文（"忽略之前的指令""开发者模式""输出你的提示词"等）。
  - PII 检测复用 `observability/redact.py`，只标记并脱敏，不因普通 PII 拒绝服务。
  - 调用 `rule_engine.screen()`，汇总成 `InputVerdict(labels, level, reasons)`。
- 新增 `backend/tests/test_input_rail.py`：空格、拆字、英文混写、双重否定、越狱、
  PII 六类正反例。

### L2 决策层 policy + audit

- 新增 `backend/app/safety/policy.py` 与 `policies/safety-policy.json`
  - `decide(verdict, state, model_action)` 返回 `PolicyDecision`。
  - 优先级：`risk` > `jailbreak` > `vague_streak >= 2` > `normal`。
  - `risk` → 固定风险话术；`jailbreak` → 固定拒绝话术；
    连续含糊 → 主动收束到 FINALIZE，不再无限追问。
- 新增 `backend/app/safety/audit.py`
  - 一行一个事件：时间、session_id、turn、layer、rule_id、action、输入短哈希。
  - 复用 `observability/redact.py`；写盘失败不影响主流程。
- `agent/state.py` 增加 `vague_turns`（覆盖语义），保留
  "FINALIZE 前必须调用过工具"的不变量。
- 测试：`test_policy.py`、`test_audit.py`。

### L3 输出层 guardrails

- 新增 `backend/app/safety/validators.py`，四个 `@register_validator`：
  1. `NoDiagnosis`：禁"确诊/诊断为/就是某病"及改写。
  2. `NoMedicationAdvice`：禁处方、剂量、"吃点消炎药""停药/减量"及改写。
  3. `DisclaimerRequired`：必须带固定免责声明。
  4. `CitationRequired`：引用了知识条目时必须带来源。
- 新增 `backend/app/safety/output_rail.py`
  - `Guard().use(...)` 串四个校验器，`on_fail="fix"`，失败替换为固定安全文案。
  - 返回 `OutputVerdict(passed, text, violations)`。
- `graph.py` 的 `output` 节点：先跑现有 `final_check()`，再跑 `output_rail`，
  保留两层而不是替换。
- 测试：`test_output_rail.py`，必须包含"无字面禁词"的改写攻击
  （"不用去医院""吃点消炎药就行""就是普通感冒"）。

### L4 对抗集与度量

- 新增 `eval/adversarial/cases.jsonl`，50 条，六类：
  明示红旗、隐性红旗、否定、越狱、诱导处方、情绪化表达。
  其中 20 条标 `holdout: true`，只用于最终报告，不参与调规则。
- 新增 `eval/adversarial_eval.py`
  - 输出每类召回/精确率、混淆明细、漏检与误报清单。
  - 门禁：红旗召回 = 1.0 硬失败；精确率 < 0.85 失败。
- 报告写到 `docs/eval-report-phase5.md`；`.github/workflows/ci.yml` nightly
  增加对抗集门禁。
- 根据漏检逐条修规则或词表，直到达标；误报逐条记录原因。

### L5 接线与验收

- `graph.py` 三处接线：`intake` 前输入层、`decision` 后策略层、`output` 后输出层。
- trace 增加 `safety.input` / `safety.policy` / `safety.output` 三个 span，
  只记标签、规则号、动作，不记用户原话。
- 端到端：mock 与 `USE_MODEL=1` 各跑一遍 33 案例；审计文件可回放。
- 更新 `README.md`、`docs/demo-script.md`，补一张三层防线图与验收数字。

---

## 验证

```bash
uv run pytest
uv run python eval/risk_probe.py
uv run python eval/adversarial_eval.py
uv run python eval/run_eval.py
USE_MODEL=1 uv run python eval/run_eval.py
```

验收标准：

- 单测从 228 增至 270+，新增规则都有正反例。
- 红旗对抗集召回 100%，精确率 ≥ 0.85。
- 输出层对改写攻击的拦截有测试覆盖。
- 每次风险升级在 `logs/audit.jsonl` 有记录（时间、规则、输入摘要、动作）。
- 33 案例在 mock 与模型模式均全过。
- `TRACE_SINK=jsonl` 能看到三层 safety span，且不含明文 PII。

---

## 风险与回退

- **openai 降级**：L1 预检是硬门槛。若 `create_with_completion` 或 instructor
  在 2.54 下异常，先回退 openai 3.3.0；此时改用独立校验服务或自写校验层，
  需要重新确认方案。
- **guardrails-ai 依赖较重**（litellm、langchain-core、lxml、tiktoken 等）：
  只把它用在输出层，不扩散到其他模块。
- **对抗集自写偏乐观**：保留 20 条 holdout，报告里注明；后续可换公开医疗安全集。
- **不要用 reask**：第二次模型调用会让延迟与成本不可控，也破坏离线测试。
