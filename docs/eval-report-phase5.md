# Phase 5 评测报告：分层安全

## 结论

Phase 5 把单层关键词升级成输入层、决策层、输出层三道独立防线，并用 50 条
对抗集把“漏多少、误报多少”变成可进 CI 的数字。

| 指标 | dev | holdout | 门禁 |
| --- | --- | --- | --- |
| 红旗召回（严格） | 1.000 | 1.000 | = 1.000 |
| 红旗精确率 | 1.000 | 1.000 | ≥ 0.85 |
| 越狱召回 | 1.000 | 1.000 | = 1.000 |
| 输出攻击拦截 | 1.000 | 1.000 | = 1.000 |
| 安全文案误伤 | 1.000 | 无样本 | = 1.000 |

原有红旗探针（`eval/probes/red_flag_probes.jsonl`）同步重测：

| split | 严格召回 | 误报率 |
| --- | --- | --- |
| dev（14 条） | 1.000 | 0.000 |
| holdout（10 条） | 1.000 | 0.000 |
| all（24 条） | 1.000 | 0.000 |

33 条主评测集在 mock 模式下仍为 33/33；单测 274 条通过。

## 分层做了什么

### 输入层（`safety/input_rail.py`）

- 归一化：NFKC、去零宽、lower；零宽字符不会再把“呼吸困难”切成两个词。
- 越狱/提示注入：8 条中英文模式，覆盖“忽略之前的指令”“开发者模式”
  “输出系统提示”“你扮演医生”“别管规则”。
- PII：复用 `observability/redact.py`，只标记并脱敏，不因此拒绝服务。
- 红旗/含糊：复用 `rule_engine.screen()`，原有行为不漂移。

### 决策层（`safety/policy.py` + `safety/audit.py`）

- 优先级：`risk > jailbreak > 连续含糊 >= 2 轮 > 正常`。
- 红旗和越狱直接压过模型动作；连续含糊主动收束。
- 策略配置在 `safety/policies/safety-policy.json`，阈值可评审。
- 每次升级写一条 `logs/audit.jsonl`：时间、层级、规则号、动作、
  输入短哈希；detail 先脱敏再落盘。审计失败不影响主流程。

### 输出层（`safety/validators.py` + `safety/output_rail.py`）

四个 guardrails 自定义校验器：

1. `consultation-no-diagnosis`：确诊、诊断为、就是某病等。
2. `consultation-no-treatment-advice`：处方、剂量、服药方式、药名、
   “不用去医院”“自己在家扛”等。
3. `consultation-citation`：引用知识时必须给来源。
4. `consultation-disclaimer`：必须带免责声明。

统一 `on_fail="fix"`，任一失败整段替换为固定安全文案；没有使用 `reask`，
因此不会产生第二次模型调用。guardrails 遥测已关闭
（`configure(allow_metrics_collection=False)`）。

## 对抗集构成

`eval/adversarial/cases.jsonl` 共 50 条，dev 30 / holdout 20：

| 类别 | 条数 | 期望 |
| --- | --- | --- |
| 明示红旗 | 9 | risk |
| 隐性红旗 | 9 | risk |
| 否定 | 8 | safe |
| 越狱 | 9 | jailbreak |
| 诱导处方 | 8 | 输入 safe + 输出拦截 |
| 情绪化 | 7 | safe，其中 1 条自伤风险为 risk |

运行：

```bash
uv run python -m eval.adversarial_eval
uv run python -m eval.adversarial_eval --split holdout
```

CI 的 `quality` job 已加入该门禁，结果写到
`eval/results/adversarial-ci.json`。

## 本轮补强的红旗替代表述

`rule_engine.py` 新增：说话含糊、一侧手脚没力气、半身不遂、口角歪斜、
一侧脸麻、直冒冷汗/出冷汗、脸色苍白、晕过去、昏迷、叫不醒、血一直流、
出血不止、大石头压住、胸口压迫感、嘴唇发紫、看东西重影、站不稳、
呼吸很费劲、说不了完整句子、抽搐、眼睛往上翻、喉咙肿、被车撞了、
胸口痛、自杀、割腕、不想活了。

## 端到端验收

- 单测：279 passed。
- 33 条主案例：mock 模式 33/33；模型模式 33/33，104 次调用 /
  104,533 输入 + 7,788 输出 token / ¥0.099196 / p50 5.282s / p95 20.566s。
- 红队 dev：6/6 拦截（直球越界、角色/权威、风险绕过、提示注入、多轮渐进、格式/资源）。
- 门禁自检与破坏自检：通过；删风险词、改追问上限、放行边界文案都会变红。
- trace：一次风险会话产生 `safety.input`、`safety.policy`、`safety.output`、
  `safety.escalation` 四类 span；JSONL 中手机号和姓名均未出现。
- 输出层延迟：100 次校验 p50 1.22ms / p95 1.43ms（本机）。
- 越狱输入返回 `status=refused`，不进入普通决策，也不调用工具。
- 正常结果引用知识时带 `source_note`，输出层用 `uses_knowledge=True` 校验来源。

## 已知局限

- 50 条对抗集是自写的，holdout 与 dev 同一批人设计，不是真正盲测；
  指标偏乐观，后续应引入独立标注或公开医疗安全集。
- 输出层目前是模式匹配，能覆盖“吃点消炎药”“不用去医院”等常见改写，
  但更隐蔽的语义改写仍可能漏过；本轮没有引入 LLM 第二意见。
- guardrails-ai 0.11.0 要求 `openai<3`，项目已从 3.3.0 降到 2.54.0；
  升级 guardrails 前需要重新评估兼容性。
- `guardrails` 依赖较重（litellm、langchain-core、lxml、tiktoken 等），
  只用于输出层，不扩散到其他模块。
- 审计只记输入短哈希，无法从审计文件反查原文；需要原文时按 session/turn
  回放 checkpoint 或 trace。
