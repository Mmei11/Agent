# Phase 3 执行计划：从“流程通过”到“质量可证明”

> 起始日期：2026-10-28
> 基线标签：`phase3-baseline`
> 目标：把现有 33/33 的流程断言，升级成一套分层、可校准、可重复、能拦截的评测系统。

---

## 一、完成标准

| 维度 | 标准 |
| --- | --- |
| 硬约束 | dev 集 33 条 100% 通过；风险路径不得进入 decision |
| Judge 可信度 | 人工 20 条校准，一致率 ≥ 0.8（M5） |
| 稳定性 | 硬断言 pass^5 = 1.0；judge 均分标准差可解释 |
| 红队 | 六类攻击拦截率 100%，正常集误报不升高 |
| CI | PR 5 分钟内跑完；故意删一条风险规则必须变红 |

---

## 二、三层断言

| 层 | 判定方式 | 例子 | 失败处理 |
| --- | --- | --- | --- |
| 硬约束 | 代码断言 | 风险输入不得进入 decision；追问 ≤3；工具异常转结构化 error | CI 阻断 |
| 质量 | judge + rubric | 追问相关性、结论有据、边界合规 | 阈值阻断或告警 |
| 效率 | 计量 | p50/p95、token、¥/会话 | 预算告警 |

三层分开报告，不揉成一个总分。

---

## 三、七课执行链路

### 第 0 课｜冻结基线（0.5 天）

- 测量：单测、33 条硬断言、红旗探针、抽取 F1（keyword + LLM）、模型模式成本与延迟。
- 产出：`docs/metrics-log.md` 的“Phase 3 起点”一节；标签 `phase3-baseline`。
- 验收：拿到标签能复现同一组数字。

### 第 1 课｜分层断言（2–3 天）

- 升级 `eval/cases.jsonl`：增加 `split`、`hard_assertions`、`judge_rubric`。
- 新增 `eval/assertions.py`：声明式断言注册表。
- 重构 `eval/run_eval.py`：抽出 `run_case()`，报告拆分为硬断言通过率与失败明细。
- 验收：33 条 dev 全过；把追问上限改成 10 必须失败。

### 第 2 课｜Judge 骨架（2–3 天）

- 新增 `eval/judge.py`：`DimensionScore`、`JudgeResult`、`judge_case()`。
- 三个维度：追问相关性、结论有据、边界合规；1–5 分，4 分通过。
- 复用 `chat_structured`，`temperature=0`，prompt 版本化。
- 验收：抽 10 条人工读 `reason/evidence`，确认可解释。

### 第 3 课｜人工校准（2 天 + 1–2 小时标注）

- 新增 `eval/calibrate_judge.py`：导出标注模板、计算一致率与 Cohen's κ。
- 人工标注 20 条 ×2 维；标签确认后冻结。
- 验收：一致率 ≥0.8；否则改 rubric 重跑并记录。

### 第 4 课｜多次采样（2 天）

- 新增 `eval/repeat.py`：`--n 5`，mock 全量、judge 抽样。
- 输出 pass^k、均值、标准差、失败案例、成本。
- 验收：硬断言 pass^5 = 1.0；结论注明 n=5 的局限。

### 第 5 课｜红队（2–3 天，可与 2–4 并行）

- 新增 `eval/redteam.jsonl`：六类攻击，dev/holdout 各半。
- 新增 `eval/red_team.py`：复用 `run_case()`，检查路由、禁止词、提示泄漏。
- 验收：拦截率 100%；正常集误报不升高；拦截可溯源。

### 第 6 课｜CI 门禁（2–3 天）

- 新增 `.github/workflows/ci.yml`：单测、mock 硬断言、安全探针、脱敏、Docker build。
- 可选 `.github/workflows/nightly.yml`：模型 judge、n=5 抽样。
- 破坏测试：删规则、改上限、放行越界文案，CI 必须变红。
- 验收：PR 5 分钟内完成；holdout 不进入 PR。

### 第 7 课｜报告与叙事（1–2 天）

- 新增 `docs/eval-report-phase3.md`：硬断言、judge、校准、红队、与基线差值。
- 更新 `docs/metrics-log.md` 与 README 的评测说明。
- 验收：5 分钟能讲清“测得什么、还缺什么”。

---

## 四、指标扩展

| 指标 | 含义 | 目标 |
| --- | --- | --- |
| M1 抽取 F1 | 槽位级 P/R/F1 | 维持 ≥0.85 |
| M2 红旗召回 | dev / holdout 严格召回 | dev 1.00 / holdout ≥0.90（Phase 5） |
| M3 误报率 | 对照集误报 | ≤0.10 |
| M4 成本与延迟 | p95、token、¥/会话 | p95 < 8s；成本可查 |
| M5 judge 一致率 | 人工 vs judge | ≥0.80 |
| M6 红队拦截率 | 六类攻击 | 100% |
| M7 稳定性 | pass^k、judge 标准差 | pass^5=1.0；std 可解释 |

---

## 五、数据与版本纪律

- `dev` 用于调试，`holdout` 只在发版前跑。
- 人工标签确认后冻结，不随规则调整而改。
- judge prompt 与 rubric 版本化；数据集变更必须记录。
- 调试时看过的 holdout 案例，不再算 holdout。

---

## 六、明确不做

- 不在 Phase 3 引入 RAG 或知识库（Phase 4）。
- 不把 judge 分数直接当硬门禁；judge 会波动。
- 不追求覆盖率数字，优先可解释和可复现。
- 不在 PR 流水线里调用真实模型。

---

## 七、起手式

从第 0 课和第 1 课开始：

1. 冻结当前基线并打标签。
2. 把 33 条案例转成声明式断言。
3. 跑 `run_eval --split dev`，确认 33/33。
4. 记一行 metrics-log，进入 judge。