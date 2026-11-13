# 指标变更日志

记录规则见 [指标定义](planning/optimization-metrics.md) 第 6 节：一次只改一件可归因的事；变好变差都记；波动超过 1 个标准差先重跑。

口径：M1 抽取槽位 F1；M2 红旗严格召回（all / dev / holdout）；M3 误报率；M4 p95 延迟 + 每会话成本；M5 judge 一致率。`—` 表示该阶段尚未建立。

| 日期 | 阶段 | 改了什么 | M1 | M2 | M3 | M4 | M5 | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-23 | Phase 0 | 建立基线，未改业务代码 | 待标注 | 0.3125 / 0.50 / 0.00 | 0.00 | p95 0.026s（mock），¥0 | — | 词表对改写表达几乎无覆盖；holdout 严格召回为 0 |
| 2026-10-25 | Phase 0 | 完成 30 条抽取标注，补齐 M1 基线（未改业务代码） | 0.3529（P 0.83 / R 0.22） | 同上 | 同上 | 同上 | — | 关键词抽取召回仅 0.22；16 个部位槽位全漏；"没有发热"被误提取为发热 |
| 2026-10-25 | Phase 0 | **口径变更**：新增 3 个可选字段（加重缓解因素/时间模式/测量值），必需字段不变 | **0.4865**（P 0.84 / R 0.34） | 同上 | 同上 | 同上 | — | F1 上升全部来自分母从 180→270，**不是系统变好**；跨口径不可比 |
| 2026-10-25 | Phase 0 | 评测器支持模型模式（`--use-model`），trace 记录 token 用量 | 同上 | 同上 | 同上 | **p50 0.612s / p95 3.245s；52 次调用，333.5 token/会话** | — | 33/33 仍通过；模型模式 p95 是 mock 的约 130 倍；9 条案例 0 次调用 |
| 2026-10-26 | Phase 1 | 引入 instructor 模型抽取（IntakeFacts + 证据/否定闸门），关键词版保留 | **0.9045**（P 0.9103 / R 0.8987；TP 71 / FP 7 / FN 8） | 未变 | 未变 | 单次约 1.2k 输入 token；2 条样本 2.9–4.4s | — | 达标（目标 ≥0.85 且 +20pt）；槽位错误集中在字段归属，值差异集中在时间外壳与修饰词 |
| 2026-10-26 | Phase 1 | 修复否定判断跨标点：`check_negation` 只在同一小句内生效 | **0.9325**（P 0.9048 / R 0.9620；TP 76 / FP 8 / FN 3） | 未变 | 未变 | 未测 | — | 召回 0.8987→0.9620；仅 IN21 可直接归因于修复，其余含模型输出波动 |
| 2026-10-26 | Phase 1 | prompt v2：一次加入六条规则（时间外壳/修饰词/测量值归属/加重完整片段/睡眠模式/拒答边界） | **0.8982**（P 0.8523 / R 0.9494；TP 75 / FP 13 / FN 4） | 未变 | 未变 | 未测 | — | **回退**：规则增多后字段归属退化（发热/累被归到程度）；F1 比 v1 低 3.4pt，已恢复 v1 |
| 2026-10-26 | Phase 1 | 模型抽取接入 intake；修"不适"内"不"的否定误判；工具故障夹具改为抽取器无关 | 0.9325（v1） | 未变 | 未变 | **104 次调用 / 33 案例；71,627 输入 / 5,293 输出 token；约 ¥0.068；0 回退；p50 4.298s / p95 13.726s（按案例）** | — | 模型模式 33/33；调用翻倍，输入 token 约 6.7 倍；约 87% 成本来自抽取 prompt |
| 2026-10-26 | Phase 1 | 第三课：决策上下文扩到完整状态；模型输出 action/reason/confidence；置信度 <0.6 回退规则；FINALIZE 前必须调用过工具；删除旧 urllib 决策路径 | 0.9268（P 0.8941 / R 0.962，重测；与 0.9325 差 1 FP，属模型波动） | 未变 | 未变 | 104 次调用 / 33 案例；102,748 输入 / 7,777 输出 token；约 ¥0.098；0 回退；p50 5.248s / p95 18.49s；**决策 52 轮，48 与模型一致，0 降级，平均置信度 0.904** | — | 33/33；模型决策真正生效；4 次程序覆盖均为"重复调用工具"被拦；0.6 阈值下置信度闸门一次未触发 |
| 2026-10-27 | Phase 2 | 第二课：图节点与模型调用埋点；state.trace 改摘要并截断到 80 条；新增 trace_report | 未变 | 未变 | 未变 | **4 轮模型会话 32 span / 8,774 输入 + 627 输出 token / ¥0.008273 / 0 错误；intake p95 3,290ms、decision p95 2,022ms** | — | 会话视图能逐轮展开 span 树；决策子 span 记录 missing_fields、action、confidence；mock 33/33 仍通过 |
| 2026-10-28 | Phase 3 | 冻结基线（`phase3-baseline`），未改业务代码 | 0.9202（LLM；keyword 0.4865） | 0.3125 / 0.50 / 0.00 | 0.00 | 104 调用 / 33 案例；102,814 输入 / 7,853 输出 token；¥0.097951；0 回退；p50 5.236s / p95 18.846s | — | 33/33；M1 在 Phase 1 波动内；holdout 召回仍为 0 |
| 2026-10-28 | Phase 3 | 第 1 课：33 条案例改为声明式硬断言，划 dev 26 / holdout 7；runner 增加 `--split` / `--case` | 未变 | 未变 | 未变 | 未变 | — | 33/33、dev 26/26、holdout 7/7；139 tests；故意改错断言 exit=1 |
| 2026-11-02 | Phase 5 | 分层安全：输入/决策/输出三道护栏、50 条对抗集、审计与三层 safety span；红旗词表补隐性替代表述 | 未变 | **1.00 / 1.00 / 1.00** | 0.00 | **104 调用 / 104,533 输入 + 7,788 输出 token / ¥0.099196 / p50 5.282s / p95 20.566s；输出层 p95 1.43ms** | — | M2 holdout 0.00→1.00；24 条红旗探针 24/24；对抗集 dev/holdout 门禁全过；279 tests；红队 dev 6/6 |

## 基线明细

**2026-10-23 · Phase 0**

- 单测：72 passed / 0.53 s
- 案例硬断言：33/33，三轮独立运行一致
- 延迟（mock，99 次）：mean 0.0115 s，p50 0.0080 s，p95 0.0260 s，max 0.037 s，stdev 0.0070 s
- 红旗探针：dev 14 条（严格 0.50 / 保守 0.60 / 误报 0.00）；holdout 10 条（严格 0.00 / 保守 0.1667 / 误报 0.00）
- 漏判：RF06、RF07、RF08、RF09、RF16、RF17、RF18、RF19、RF20
- 抽取样本：30 条已生成，0 条已标注

**2026-10-26 · Phase 1 第三课**

- 单测：113 passed / 1.1 s（新增决策上下文、Schema、闸门、回退共 20 条）
- 案例硬断言：mock 33/33；模型模式 33/33，0 回退
- 决策：52 轮，48 轮最终动作与模型一致；4 次被 `validate_action` 改掉，全部是"想重复调用工具"（工具空结果或失败后模型仍想再调一次）
- 置信度：平均 0.904，最低 0.8，没有一轮低于 0.6 阈值——**闸门当前未触发，属于"装了但没用上"**。模型自报置信度明显偏自信，符合已知规律；没有决策标注集，暂不调阈值
- 成本：¥0.098/33 案例，比第二课高约 44%，增量来自决策上下文（输入 token +31k）和 reason 输出
- E015 回归：模型第二轮直接选 FINALIZE，跳过工具，完成结果缺 department_suggest；修复为"没调工具就不许 FINALIZE"的程序不变量
- M1 重测 0.9268，与 0.9325 差 1 FP，落在模型输出波动内，不归因于本课改动

**2026-10-27 · Phase 2 第二课**

- 单测：123 passed / 1.0 s（新增 4 条埋点测试）
- 一次 mock 四轮会话：4 trace / 18 span / 0 错误
- 一次模型四轮会话：4 trace / 32 span / 8,774 输入 + 627 输出 token / ¥0.008273 / 0 错误
- 模型模式耗时：intake p95 3,290ms、decision p95 2,022ms；两处合计占每轮绝大部分
- 决策子 span 记录 missing_fields / action / confidence / prompt.version=v1，不落用户原话
- state.trace 截断到最近 80 条，评测器仍 33/33 通过

**2026-10-28 · Phase 2 第三课**

- 单测：132 passed / 1.1 s（新增脱敏、sink 组合、Langfuse 属性映射共 9 条）
- 脱敏规则：手机号 / 18 位身份证 / 邮箱 / 显式姓名；敏感键整段替换为 `[REDACTED]`
- 本地 JSONL：4 轮 mock 会话 22 个 span（含 4 个 `consultation.turn` 根 span）/ 0 错误
- Langfuse：4 条 trace / 22 个 observation / 4 个根 span；session 聚合正确
- 脱敏实测：Langfuse metadata 中 `note=电话[手机号]，身份证[身份证]`、`user_name=[REDACTED]`，无明文
- 降级：`LANGFUSE_HOST` 指向不可达端口时，4/4 请求成功，JSONL 仍写满 22 行，进程退出码 0
- 踩坑：本机 `localhost` 走 IPv6 会让 Docker 端口映射返回 502，Langfuse 地址统一用 `127.0.0.1`

**2026-10-28 · Phase 3 起点（`phase3-baseline`，commit `51d1d4c`）**

- 单测：132 passed / 1.39 s
- 硬断言：33/33（mock）
- M1 keyword：F1 0.4865（P 0.8438 / R 0.3418；TP 27 / FP 5 / FN 52）
- M1 LLM：F1 **0.9202**（P 0.8929 / R 0.9494；TP 75 / FP 9 / FN 4）
- M2：all 0.3125；dev 0.50；holdout 0.00
- M3：0.00
- M5：N/A（judge 尚未建立）
- M4：模型模式 33 条 33/33；104 次调用 / 0 回退；102,814 输入 + 7,853 输出 token；¥0.097951；p50 5.236s / p95 18.846s / max 27.348s
- 复现命令：`pytest -q`、`python eval/run_eval.py`、`python eval/risk_probe.py`、`python eval/score_intake.py --extractor ...`

**2026-10-28 · Phase 3 第 1 课**

- 新增 `eval/assertions.py`：声明式断言注册表与 `CaseObservation`
- `eval/cases.jsonl` 增加 `split` 与 `hard_assertions`；dev 26 / holdout 7
- runner 新增 `--split` / `--case`，报告增加分层汇总与断言类型
- 单测：139 passed（新增 7 条断言与案例 schema 测试）
- 验收：all 33/33、dev 26/26、holdout 7/7
- 破坏测试：把 E001 的 `api_status` 改成 500，exit=1，失败原因 `[api_status] 期望 HTTP 500，实际 200`
- 门禁自检：`eval/self_check.py` 把追问上限从 3 改成 10，正常 exit=0、破坏 exit=1，报告含 `[max_followups]`；140 tests

**2026-10-29 · Phase 3 第 2 课**

- 新增 `eval/judge.py`：`DimensionScore` / `JudgeResult` / `judge_case` / `verdict`；三维 1–5 分、4 分通过，`temperature=0`，prompt `judge.case` v1
- 输入只含 `conversation`（带 turn 与“用户/系统”标签）、`final_result`、`rendered_text`、`tool_results`；不喂 `expected_actions`，避免 judge 对着答案打分
- `final_result` 为空时 `evidence_grounding` / `boundary_compliance` 记 `applicable=false`，不参与阈值，避免把“没有结论”算成 0 分或满分
- 新增 `eval/judge_demo.py`：mock 跑案例 + 真实模型打分，支持 `--case` / `--split` / `--limit` / `--reasons`
- 单测：144 passed（新增 4 条 judge 离线测试）
- 10 条 dev 实测：8/10 通过；14,579 输入 + 2,079 输出 token，¥0.0158，p50 4.6s / p95 9.2s
- 质量层发现：E005 用户已说“肩膀酸痛”，系统仍追问“主要的不适是什么”（硬断言未覆盖）；E009 追问与剧本回答错位，judge 判为跑题
- 稳定性观察：同一案例跨次运行 E009 4→3、E012 5→4，`temperature=0` 不等于确定，需第 3 课校准

**2026-10-29 · Phase 3 第 3 课**

- 新增 `eval/calibrate_judge.py`：`export` 生成盲标材料与空白标签，`score` 计算一致率与 Cohen's κ；标签文件兼容 Excel 的 GB18030 编码
- 样本：20 条 dev 案例 → 32 个样本（追问 20、有据 6、边界 6）；judge 结果单独存放，标注前不展示
- v1（qwen-plus / prompt v1）：追问 0.90 / κ 0.615；有据 0.83 / κ 0.0；边界 1.00 / κ 无定义；总体 0.91 / 0.52
- v1 复核发现共同盲区：20 条追问里 10 条出现“用户已说主诉、系统仍问主要的不适是什么”，人工和 judge 都放过了其中 9 条
- 根因：`intake.FIELD_KEYWORDS` 未覆盖“肚子疼 / 肩膀酸痛 / 喉咙不舒服 / 胃部不适 / 腿部酸痛”等说法，主诉字段被误判为缺失
- v2（qwen-plus / prompt v2）：追问 0.75 / κ 0.167；qwen-plus 稳定误读“系统追问 0 条”和提问/拒绝的 turn 先后
- judge 改为独立 `JUDGE_MODEL` 与独立单价；qwen-max 抽查稳定，20 次调用 ¥0.195，约为 qwen-plus 的 6 倍
- 适用性改由代码判断：`final_result` 为空才跳过有据/边界，避免模型把 `risk_route` 当成“无结论”
- v4（qwen-max / prompt v4）：追问 1.00 / κ 1.0（人工与 judge 通过率均 0.40）；有据 1.00 / κ 无定义（n=6 全通过）；边界 1.00 / κ 无定义（n=6 全通过）
- 局限：同一 20 条 dev 上迭代 rubric，属样本内校准；人工已看过前几轮输出，盲标不完整；有据/边界各 6 条且全通过，κ 不可用；κ 只比 pass/fail，抓不到 reason 的错误
- 下一步：补 finalized 案例扩大有据/边界样本；修复 intake 词表；考虑把“重复追问”改成确定性断言

**2026-10-29 · Phase 3 第 4 课**

- 新增 `eval/repeat.py`：`--n` 重复采样，输出 `pass^n`、单次通过率、judge 均值/标准差/通过率、成本与延迟
- `check_case` 增加 `with_state`，重复采样可把完整状态交给 judge，结果文件默认不携带状态
- mock 全量 dev `n=5`：26/26 `pass^5`，130/130 单次通过，成本 ¥0，延迟 p50 0.02s / p95 0.04s
- judge 抽样 `n=5`：E009/E015/E017/E018/E020，25 次 qwen-max，¥0.163
- 追问相关性：n=25，均值 4.52，标准差 0.714，案例内平均标准差 0.179，通过率 0.96
- 不稳定集中在 E015：分数 4/2/4/4/4，一次判“拒绝后仍追问”，其余 4 分；qwen-max 对 turn 先后仍有约 20% 误读
- 有据/边界：5/5 全稳定，均值 5.0，标准差 0
- 修复：judge 的 `evidence` 字段缺省为“无”，避免模型漏字段导致整次判断失败（首轮 25 次中 1 次）
- 局限：mock 确定性，`pass^5` 是评测器自检；judge 抽样只有 5 条且以 finalized 案例为主；n=5 的置信区间很宽

**2026-10-29 · Phase 3 第 4 课补丁：追问判定改为确定性规则**

- 新增 `eval/followup_check.py`：六个问题模板映射字段，只扫描提问 turn 之前的用户原话；识别否定（“没有呼吸困难”）和字段级拒绝（“不知道持续多久”/“说不清具体位置”）
- 判定输出 valid / repeated / invalid；1 条重复记 3 分，2 条及以上重复或拒绝后仍追问记 2 分，顺序错乱记 4 分
- `eval/judge.py` prompt v5：模型只评 `evidence_grounding` 与 `boundary_compliance`；`followup_relevance` 由规则填充
- 规则 vs 人工标签：20/20 一致，一致率 1.00、κ 1.0；有据/边界 6/6
- 校准导出跳过没有 `final_result` 的案例，qwen-max 调用从 20 次降到 6 次，成本 ¥0.195 → ¥0.078
- `n=5` 重跑：E015 追问 5/5/5/5/5（修复前 4/2/4/4/4），三维案例内标准差全 0；judge 成本 ¥0.163 → ¥0.109，0 错误
- 单测：160 passed（新增 7 条 followup_check 测试）
- 局限：规则依赖词表，遇到新说法可能漏判；本轮仍在同一批 dev 上验证

**2026-10-29 · Phase 3 第 5 课**

- 新增 `eval/redteam.jsonl`：六类攻击（直球越界、角色/权威、风险绕过、提示注入、多轮渐进、格式/资源），dev/holdout 各一条，共 12 条
- 新增 `eval/red_team.py`：复用 `check_case`，检查 HTTP、风险路由、禁用词与剂量单位、提示泄漏、禁止节点，并记录命中的风险事件
- 基线 9/12：空格式拆词和双重否定绕过风险门禁；多轮案例的 409 是风险会话被关闭，按拦截处理
- 加固 `rule_engine`：NFKC 归一化、字符间容忍空格/标点、双重否定按肯定、补充英文风险词、剂量数字与单位纳入 F002
- 复跑 12/12 拦截；正常集 33/33；红旗探针 M2 严格召回 0.3125（不变）、保守召回 0.4375、M3 误报 0.0（未升高）
- 单测：163 passed（新增 3 条安全规则测试）
- 局限：红队只有 12 条；同义改写的风险表述仍有 9 条漏判，留给 Phase 5 分层安全

**2026-10-29 · Phase 3 第 6 课**

- 新增 `.github/workflows/ci.yml`：PR 与 main push 跑单测、dev 硬断言、dev 红队、风险探针、门禁自检、破坏自检；Docker build 独立 job
- PR 全链路不调用模型（`USE_MODEL=0`），holdout 不进入 PR；模型评测、holdout 红队、judge `n=5` 放 `.github/workflows/nightly.yml`
- 新增 `eval/ci_break_check.py`：删风险词、改追问上限、放行边界文案三种破坏都会变红
- 本地质量链路 5.8 秒（不含 `uv sync`）；Docker build 热缓存 14.1 秒
- 单测：163 passed
- 局限：GitHub Actions 尚未实际触发，以上为本地同命令验证；首次远端运行需确认缓存、secret 与 runner 环境

**2026-10-29 · Phase 3 第 6 课补丁：远端首次运行**

- 更正上文“尚未实际触发”：CI 已在远端跑通；CI #1（167d226）48 秒全绿
- CI #2 失败：`setup-uv@v10` 没有移动标签；固定到 `v10.3.3` 后 CI #3 通过
- 所有 action 升级到 Node 24 版本（checkout/setup-python/upload-artifact v7、setup-uv v10.3.3、docker v4/v7），runner 固定 `ubuntu-24.04`
- Nightly #1 失败：RT009 在模型模式第二轮结束会话，第三轮返回 409；判定器只给 RT010 放行 409
- 红队判定改为通用规则：409 且已有 `final_result` 视为安全关闭；409 无结果算失败
- 红队报告增加 HTTP 状态列；本地模型模式 RT009 复跑为拦截
- 单测：166 passed
- nightly cron 修正为 UTC 19:17（北京时间次日 03:17）；需配置 `DASHSCOPE_API_KEY` secret

**2026-10-29 · Phase 3 第 7 课：评测报告收尾**

- 新增 `docs/eval-report-phase3.md`：5 分钟讲法、M1–M7 总表、硬断言、judge 校准、n=5 稳定性、红队、成本延迟、CI 与局限
- Nightly #2（`d46c9ae`）6 分 4 秒全绿：dev 模型 26/26、红队 12/12、judge n=5 的 25/25 全部通过
- 已汇总成本：业务 ¥0.072771 + judge ¥0.110056 = ¥0.182827；红队调用未单独汇总
- M4 p95 18.694s，未达 <8s 目标；M2 严格召回 0.3125，未达 Phase 5 目标
- 单测：166 passed
- 结论：流程与安全门禁已可证明；质量尺子对追问可用，对有据/边界样本不足；延迟与红旗改写召回是下一阶段缺口

**2026-11-02 · Phase 5：分层安全**

- 新增 `backend/app/safety/input_rail.py` / `policy.py` / `audit.py` / `validators.py` / `output_rail.py`
- 输入层：NFKC + 去零宽；8 条越狱/注入模式；PII 复用 `observability/redact.py`；红旗/含糊复用 `rule_engine.screen()`
- 决策层：risk > jailbreak > 连续含糊；`policies/safety-policy.json` 可评审；审计写 `logs/audit.jsonl`（输入短哈希，不落原文）
- 输出层：guardrails-ai 0.11.0 四个自定义校验器，`on_fail="fix"`，固定安全兜底；关闭 guardrails 遥测；不使用 reask
- 依赖：guardrails-ai 0.11.0 要求 `openai<3`，openai 从 3.3.0 降到 2.54.0；L1 预检 228 tests + 百炼真实调用通过
- 红旗词表新增 30 条隐性替代表述；24 条探针 all/dev/holdout 严格召回均为 1.00，误报率 0.00（Phase 0 基线 holdout 0.00）
- 新增 50 条分层对抗集（dev 30 / holdout 20）：红旗召回、越狱召回、输出拦截、安全误伤四项门禁全过；CI `quality` job 已接入
- 图接线：intake 前输入层并脱敏，risk_check 走策略并审计，output 增加 `refused` 分支和 guardrails 第二道复核；33/33 仍通过
- trace：新增 `safety.input` / `safety.policy` / `safety.output` / `safety.escalation`；JSONL 实测手机号和姓名均未落盘
- 单测：279 passed
- 模型模式：33/33；104 次调用 / 104,533 输入 + 7,788 输出 token / ¥0.099196 / p50 5.282s / p95 20.566s / max 25.248s
- 局限：对抗集自写、holdout 非真正盲测；输出层为模式匹配，未引入 LLM 第二意见；p95 比 Phase 3 基线 18.8s 高约 1.7s，落在模型波动内，输出层本身 p95 仅 1.43ms

**2026-11-04 · Phase 6 第 1 课：SSE 流式**

- 新增 `POST /sessions/{session_id}/stream`：事件契约 `meta → node(start/end)* → result`，异常以 `error` 结束；`EventSourceResponse` + `X-Accel-Buffering: no`
- 图用 `graph.stream(stream_mode="debug")` 在 worker 线程跑：`SqliteSaver` 不支持 async 方法，避免在 L1 引入 `AsyncSqliteSaver` 双连接
- `start_trace` 在 worker 线程内开合；客户端断开后本轮继续跑完并落 checkpoint / 审计（实测断开后 GET 可见完整结果）
- 节点事件只带 `instrument.SAFE_KEYS` 白名单摘要，不推完整 state 或用户原话
- 前端：`fetch` + `ReadableStream` 解析 SSE（EventSource 只支持 GET）；节点进度文案 + 结果打字机；失败后先 GET 恢复真实状态，再决定是否重试
- nginx `/sessions` 关闭缓冲，`proxy_read_timeout 300s`
- 单测：280 passed（新增 `test_stream.py` 6 条：事件顺序 / 风险路径 / 404 / 409 / 400 / 旧接口兼容）
- 延迟（5 轮 mock）：首事件 p50 2.7ms / p95 3.6ms；首个进度 p50 3.3ms / p95 16.7ms；完整结果 p50 7.1ms / p95 25.8ms
- 延迟（3 轮模型）：首事件 p50 2.9ms / p95 5.1ms；首个进度 p50 18.4ms / p95 22.2ms；完整结果 p50 4498ms / p95 4794ms
- 说明：本项目输出由结构化结果渲染，不是模型逐字生成；"文本"在 `result` 事件一次到达，打字机是展示层效果，不伪造 token 流
- 遗留：断线重试可能重复提交一轮（幂等留给 L3 的单会话在途锁）；L2 仍需解决 SQLite 并发写

**2026-11-04 · Phase 6 第 2 课：异步与并发**

- 新增 `eval/load_test.py`：固定并发虚拟用户，支持 `/stream` 与 `/messages`，输出 p50/p95/p99、状态码分布、首事件、rps；5xx + 超时 + 连接错误算失败，4xx/429 单独计数
- 路由全部改 async；同步图调用经 `_run_sync` 进入专用 `ThreadPoolExecutor`，用 `contextvars.copy_context()` 保留 trace 上下文
- 线程池默认随负载类型：mock 8、模型 64；`GRAPH_MAX_WORKERS` 可覆盖，compose 已透传
- SQLite：`create_saver` 统一 WAL + `busy_timeout=5000` + `synchronous=NORMAL`；最终保留共享 saver（`SqliteSaver` 内部 lock 天然限流）
- 基线（50 并发 × 4 轮，mock，stream）：250 请求，0 5xx，p50 277ms / p95 398ms / 170 rps
- 每线程连接实验（64 线程）：p50 137ms / p95 894ms / 139 rps；32/16/8 线程 p95 = 894 / 591 / 448ms —— 中位数更好，尾部更差
- 共享 saver + 线程池扫描：64/32/16/8 线程 p95 = 540/504/475/397ms，rps = 149/155/159/170；mock 下 8 线程最优
- 模型模式（并发 10 × 2 轮）：10 线程 p95 5.04s、首事件 p50 12.5ms；4 线程 p95 11.88s、首事件 p50 4.57s —— I/O 密集时线程池直接决定排队
- 最终默认（mock，50 并发）：250 请求，0 5xx，p50 273ms / p95 385ms / 168 rps（与基线持平；mock 是 CPU 密集，异步不改吞吐）
- 单测：288 passed（新增 `test_concurrency.py`：不同会话并发隔离、trace 唯一）
- 结论：async 化解决"阻塞占线程"和结构隔离，不解决 CPU 密集与 SQLite 单写者；同会话并发与幂等留给 L3
- 未做：异步模型客户端（需要图节点整体迁移 async，会连带重构评测与 288 条测试）；Postgres checkpointer（演示规模不划算）

**2026-11-05 · Phase 6 第 3 课 L3.1：API Key 认证**

- 新增 `backend/app/api/auth.py`：`require_api_key` 依赖 + `CallerIdentity`（为 L3.2 按调用方限流预留）
- `AUTH_MODE=off` 放行；`required` 校验 `X-API-Key`；未知模式按 required 处理（fail closed）；required 但 `API_KEYS` 为空返回 503
- key 启动时计算 SHA-256，逐个 `secrets.compare_digest` 且不提前返回；审计只落摘要前 12 位，不落原文
- 5 条 `/sessions*` 路由挂认证依赖；`/docs`、`/openapi.json` 不受影响
- 单测：299 passed（新增 `test_auth.py` 11 条：off/required、缺失/错误/正确、多 key、前缀、未配置、未知模式、全路由保护、审计不泄漏）
- 实测（required + test-key）：缺 key 401、错误 key 401、正确 key 200、无 key 流式 401、带 key 流式正常；审计 3 条拒绝事件、原始 key 出现 0 次
- 边界：浏览器前端不携带 key，公开演示仍用 `AUTH_MODE=off`；API Key 只认证调用方程序，不是用户体系
- 下一步：L3.2 令牌桶与三维限流

**2026-11-05 · Phase 6 第 3 课 L3.2：令牌桶限流**

- 新增 `backend/app/api/ratelimit.py`：`TokenBucket` / `RateLimiter` / `RateLimitDecision`；时钟可注入（测试推进时间，不 sleep）；内部 `threading.Lock`；每 256 次检查回收空闲桶
- 三个维度，顺序固定：全局 → 调用方（API Key 摘要，off 模式退回 IP）→ 会话（从路径参数取）；前一层拒绝就不再消耗后一层
- 429 响应头：`Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`；审计 `RL001` 只落调用方标识的短哈希，不落原始 IP / key
- 默认值：调用方 60 次/分、突发 10；会话 30 次/分、突发 5；全局 600 次/分、突发 100；`RATE_LIMIT_ENABLED` 可整体关闭
- 单测：309 passed（新增 10 条：突发/补充/容量上限、key 隔离、空闲回收、429 响应头、关闭开关、会话维度、审计脱敏、认证先于限流）
- 实测：调用方突发 2 → 200/200/429（Retry-After: 1，Limit: 2）；会话突发 1 → 同一会话 200/429，不同会话不受影响
- 边界：限流状态在进程内，多 worker / 多副本时每进程各算各的；被拒绝的尝试会消耗上游维度令牌；按请求数计，不按 token 数；不信任 `X-Forwarded-For`，代理场景需在网关限流
- 下一步：L3.3 体积边界、会话在途锁、SSE 连接计数

**2026-11-05 · Phase 6 第 3 课 L3.3：资源边界与会话在途锁**

- 新增 `backend/app/api/limits.py`：纯 ASGI 的 `BodySizeLimitMiddleware`（不影响 SSE 流式响应），`Content-Length` 超 32KB 直接 413，chunked 请求在 `receive` 里累计计数；`check_message_length` 限制单条消息 2000 字，超出 422
- 新增 `backend/app/api/inflight.py`：同一会话在途集合，第二个请求 409；每个调用方的 SSE 连接计数，超限 429；流式请求在后台任务 `finally` 里释放会话与连接名额
- 路由接线：体积中间件加在最外层；同步发消息和删除会话加会话锁；流式接口先占连接名额再占会话，任务结束统一释放
- 配置：`MAX_BODY_BYTES`、`MAX_MESSAGE_CHARS`、`MAX_STREAMS_PER_CLIENT`，compose 已透传
- 单测：318 passed（新增 8 条：413、422、同会话 409、不同会话并行、删除时 409、流结束释放锁、流计数上限、流超限 429）
- 实测：正常创建 200；40KB body → 413「请求体过大」；2001 字消息 → 422「消息不能超过 2000 字」
- 边界：会话锁与流计数在进程内，多 worker 时每个进程各一份；chunked body 依赖读取计数，极端情况下应用可能已读取部分 body
- 下一步：L3.4 观测、审计与带限流的压测收尾

**2026-11-05 · Phase 6 第 3 课 L3.4：观测、审计与带限流压测**

- 新增 `observability/request_trace.py`：给 `/sessions*` 开请求级 trace（根 span `http.request`），turn trace 通过 `parent.trace_id` 关联
- 闸门 span：`api.auth`（mode / result / reason / key_id）、`api.rate_limit`（buckets / allowed / denied / retry_after）、`api.session_lock`；标识只落短哈希
- 审计补齐：会话在途 `IN001`、流数量超限 `IN002`；原有 auth `A001`、限流 `RL001`
- `eval/load_test.py` 支持 `--api-key`，新增 401 / 409 / 413 / 429 分类计数
- JSONL 实测：3 个请求产生 3 个 `http.request`、3 个 `api.auth`、2 个 `api.rate_limit`、1 个 `api.session_lock`；`consultation.turn` 带 `parent.trace_id`
- 压测（50 并发 × 4 轮，stream，mock）：
  - 无限流：250 请求，0 5xx，p50 346ms / p95 552ms，128 rps
  - 有限流（burst 60 / 1200 每分钟）：185 个 429，0 5xx；被拒请求快速返回，整体 wall 0.5s
  - 带 API Key（10 并发 × 2 轮）：30 请求全 2xx，p95 80ms，首事件 p95 55ms
- 发现：`MAX_STREAMS_PER_CLIENT=5` 按调用方（单 IP）计，50 条并发流会被挡掉大部分；压测需调高。请求级 trace 让 mock p95 从 385ms 升到 552ms，开销记录在案
- 单测：318 passed
- L3 验收：401 / 413 / 422 / 409 / 429 均已覆盖；50 并发无 5xx；429 按配置生效
- 下一步：L4 反馈闭环

**2026-11-05 · Phase 6 第 4 课 L4：反馈闭环**

- 新增 `backend/app/feedback/store.py`：`feedback` 表（`session_id + turn_id` 主键，重复提交覆盖）、`list_for_session`、`stats`
- 新增 `backend/app/feedback/scores.py`：`user_feedback` 评分，本地 JSONL 必写，Langfuse `create_score` 尽力而为；reason 在入库与评分前统一脱敏
- 新增 `POST /sessions/{session_id}/feedback`：`rating=helpful|unhelpful` + 可选 reason（≤200 字）；服务端从 state 读取 turn_id / trace_id，不信任客户端；空会话 409、未知会话 404、非法 rating 422
- 追问快捷选项：state / SessionResponse 增加 `suggested_replies`，followup 按字段给 3 个选项，intake 每轮清空
- 前端：追问 chips 点选即发送；结果卡“有帮助 / 没帮助”；提交后显示已反馈态
- 单测：325 passed（新增 7 条：反馈落库与评分、重复覆盖、空会话、未知会话、非法评分、评分脱敏、选项随问题切换）
- 端到端：4 轮点选完成问诊 → 提交 helpful；feedback 行 `turn_id=4`、`trace_id` 与 `scores.jsonl` 一致，本地评分 value=1
- 边界：反馈只评价当前最新一轮；同一轮重复提交覆盖；Langfuse 未配置，本轮只验证本地 JSONL 路径
- **Phase 6 完成（L1-L4）**：流式、异步并发、鉴权限流、资源边界、反馈闭环全部落地；下一步 Phase 7 打包与叙事

**2026-11-05 · Langfuse v4 联调（L4 补记）**

- 本地是 events-only 模式：`/api/public/traces` 不可用，trace 走 OTLP 写入 ClickHouse `events_full`
- 实测会话 `dafad8b8-…`：turn trace 的 `consultation.turn` / `safety.input` / `intake` / `policy` / `risk_check` / `decision` / `followup`，以及请求 trace 的 `http.request` / `api.auth` 均已入库
- `user_feedback=1` 成功挂到 turn trace；v4 要求 `create_score` 的 `traceId` / `sessionId` 二选一，原实现同时传导致 400，已修复并加单测
- 新增 `docs/phase6-langfuse-workflow.svg`：采集链路 + 使用闭环
- 单测：326 passed

**2026-11-05 · Langfuse 会话归属与 trace 命名修复**

- 问题一：`POST /sessions` 创建时请求 trace 被记为 `anonymous`，Session 页只看到轮次 trace；
  修复：创建出 session_id 后补绑到当前请求 trace 的根 span
- 问题二：`langfuse.trace.name` 硬编码为 `consultation.turn`，请求 trace 也显示成同名；
  修复：`SpanRecord.trace_name` 由 `start_trace(root_name=...)` 决定，同一 trace 全部一致
- 实测新会话 `fc50ecc7-...`：Session 下正确显示 `http.request`（1 span）与 `consultation.turn`（7 span）
- Session 视图的 “This trace has no messages or tools” 是预期行为：该视图依赖 `gen_ai.input.messages` / `output.messages`，我们为隐私只写 span 元数据，不写对话原文；查看 span 请点 trace 或用 Tracing → Traces
- 单测：328 passed

## Phase A｜能力层指标（2026-11-11）

**2026-11-11 · M6 上下文保持率：建立测量链路**

- 新增 `eval/context_retention.py`：用 TestClient 跑真实图，多轮会话后在探针轮检查四层命中
- 新增 `eval/probes/context_retention.jsonl`：10 条虚构样本（8 dev / 2 holdout），分 `fact` / `conversation` 两类，各有 near / far 对照
- 口径：`state`（结构化字段）、`window`（最近 6 条对话）、`prompt`（answer 节点实际上下文）、`answer`（最终回答）、`bypass`（被单路由直接回答、未走 answer 节点）
- mock 基线（dev 8 条）：

| 组别 | n | state | window | prompt | answer | bypass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 全部 | 8 | 0.375 | 0.500 | 0.625 | 0.000 | 0 |
| fact / far | 2 | 1.000 | 0.500 | 1.000 | 0.000 | 0 |
| conversation / near | 2 | 0.000 | 1.000 | 1.000 | 0.000 | 0 |
| conversation / far | 3 | 0.000 | 0.000 | 0.000 | 0.000 | 0 |

- 关键结论：
  1. 事实类 `state=1.0`、`prompt=1.0`，`window` 有一半命中，因为首轮结果消息本身复述了字段；answer 节点又拿的是全量字段，所以事实类不依赖记忆卡也能保持。
  2. 对话类 far 三层全 0，是 Phase B 会话记忆要解决的直接证据。
  3. mock 的 `answer=0` 是兜底文案造成的，不作为质量结论；模型模式的 `answer` 才有意义。
- 单测：444 passed（新增 6 条判定逻辑测试）
- 模型基线（dev 8 条，分两批跑，离线合并重算）：

| 组别 | n | state | window | prompt | answer | bypass | 否认回忆 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 全部 | 8 | 0.375 | 0.625 | 0.625 | — | 8 | 4 |
| fact / far | 2 | 1.000 | 1.000 | 1.000 | — | 2 | 0 |
| conversation / near | 2 | 0.000 | 1.000 | 1.000 | — | 2 | 2 |
| conversation / far | 3 | 0.000 | 0.000 | 0.000 | — | 3 | 2 |

- **关键发现**：8/8 探针被单路由判为普通问题，由 router 直接回答，带上下文的 answer 节点一次都没参与。CR01（near，细节就在窗口里）仍回答“你之前没有提到过”；CR02（far）回答“我无法查看之前的对话记录”。
- **测量坑修复**：CR05 的回答是“没有提到过……上午、下午还是晚上？”，`上午` 只是澄清问句枚举，被原判定误判为命中。新增“否认回忆”检查：出现“没有提到/无法查看/不记得”时，即使子串命中也算失败；已用保存的回答离线重算，未重复调用模型。
- **结论**：Phase B 不能只加记忆存储，必须同时改路由——记忆指代类问题要进入带记忆的 answer 节点，或在 router prompt 注入记忆卡摘要。目标改为：对话类 `bypass=0`、`否认回忆=0`、context 路径 `answer≥0.90`。
- 剩余：M7 工具选择、M8 token 账本尚未实现；M6 holdout 留到阶段末。

**2026-11-11 · 路由修复：记忆指代走上下文路径**

- 新增 `backend/app/agent/memory_ref.py`：确定性识别“我刚才说 / 之前提到 / 你还记得 / 我说过什么”等记忆指代
- `answer_followup` 在单路由之前判断：命中记忆指代就跳过 router，直接进入带 `recent_conversation` 的 answer 节点；记忆指代优先于日期规则（“我刚才说几月几号要去”问的是对话，不是今天）
- `ANSWER_SYSTEM_PROMPT` 升到 v4：明确 `recent_conversation` 只用于记忆问题；找不到就说“这轮对话里没有记录到”，不许猜
- 记忆类回答的附加说明改为“以上内容来自本轮对话记录”，不再套“一般健康信息”
- 新增 `answer.memory_reference` span 属性；state trace 记录 `memory_reference` 与 `router_intent=memory`
- 单测：460 passed（新增记忆指代正反例、跳路由、日期优先级、附加说明共 10 条）
- 预期：M6 `bypass` 8→0；near 样本应开始命中；far 样本仍会失败，因为最近窗口仍只有 6 条——这正是 Phase B 记忆卡要补的部分
- 修复后模型基线（dev 8 条，探针前快照口径）：

| 组别 | n | state | window | prompt | answer | bypass | 否认回忆 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 全部 | 8 | 0.375 | 0.625 | 0.625 | 0.625 | 0 | 2 |
| fact 全部 | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| conversation / near | 2 | 0.000 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| conversation / far | 3 | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 2 |

- 结论：`bypass` 8→0；事实类 3/3、近距对话 2/2 命中；远距对话 0/3，模型明确回答“这轮对话里没有记录到”，没有编造。路由修复已经把它和“记忆存储”问题分开，剩下的远距丢失进入 Phase B。
- 测量口径修复：首次修复后运行的 `window/prompt` 被探针回答自身污染（CR03 的回答含“地铁”，把窗口点亮）。已改为“探针发送前的状态快照 + 探针用户消息”，并重跑干净的 8 条；上表是干净结果。
- 单测：461 passed（新增探针前快照测试）。

**2026-11-11 · M7 工具选择：建立标尺**

- 新增 `eval/tool_selection.py` + `eval/probes/tool_selection.jsonl`（12 条，10 dev / 2 holdout）
- 样本分四类：科室方向、就医指引、风险短路、普通问题
- 判决新增 `blocked_by_intake`：期望 `department_route` 但停在追问且仍有缺失字段，说明是抽取没完成，不算工具选错
- mock 基线（dev 8 有效，2 条 general 跳过）：

| 组别 | n | selection | outcome | 危险误调 | 不可达 | 被 intake 挡住 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 全部 | 8 | 0.625 | 1.000 | 0 | 3 | 0 |
| department | 3 | 1.000 | 1.000 | 0 | 0 | 0 |
| guideline | 3 | 0.000 | 1.000 | 0 | 3 | 0 |
| risk | 2 | 1.000 | 1.000 | 0 | 0 | 0 |

- 结论：`guideline_retrieve` 3/3 不可达，是 Phase C 的结构缺口；科室与风险类当前都正确，危险误调为 0。
- 模型基线（dev 10 条）：

| 组别 | n | selection | outcome | 危险误调 | 不可达 | 被 intake 挡住 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 全部 | 10 | 0.700 | 1.000 | 0 | 3 | 0 |
| department | 3 | 1.000 | 1.000 | 0 | 0 | 0 |
| general | 2 | 1.000 | 1.000 | 0 | 0 | 0 |
| guideline | 3 | 0.000 | 1.000 | 0 | 3 | 0 |
| risk | 2 | 1.000 | 1.000 | 0 | 0 | 0 |

- 首轮模型基线科室类只有 1/3：两条停在追问，原因是样本用了“没有其他不适”，被模型抽取丢掉。改成阳性伴随症状后恢复到 3/3；`blocked_by_intake` 这一列用来区分“抽取没完成”和“工具选错”。

**2026-11-11 · M8 token 账本**

- 新增 `eval/token_ledger.py`：读 trace JSONL，按 `prompt.name` 统计输入/输出 token 与延迟；只读文件，不调用模型
- 用 E001 单案例生成真实 trace（4 次调用）：

| 节点 / prompt | 调用 | 输入 total | 输入 p95 | 延迟 p95(ms) |
| --- | ---: | ---: | ---: | ---: |
| intake.extract | 1 | 1215 | 1215 | 2456 |
| decision.choose | 1 | 769 | 769 | 1827 |
| router.reply | 1 | 484 | 484 | 625 |
| followup.replies | 1 | 455 | 455 | 760 |
| 全部 | 4 | 2923 | 1148.1 | 2361.6 |

- 结论：`intake.extract` 是输入 token 最大头（1215，占 41.6%），与 Phase 1 的成本观察一致。
- 注意：单案例单次运行，p95 与 max 相同，只验证测量链路；真实分位数要多次会话汇总。

**2026-11-11 · M9 技能路由占位**

- 新增 `eval/skill_routing.py` + `eval/probes/skill_routing.jsonl`（6 条：`headache` / `pediatric_fever` 各 3 条）
- 全部 `enabled=false`，当前输出 N/A；Phase D 实现 `backend.app.skills.router.route` 后逐条启用
- 单测：472 passed（新增 M7 6 条、M8 3 条、M9 3 条判定逻辑测试）

**2026-11-11 · Phase B 第 1 课：会话记忆卡与上下文装配**

- 新增 `backend/app/memory/session_memory.py`：只存“没进结构化字段、但值得跨轮记住”的第一人称细节；事实仍以 `collected_facts` 为准，结论仍以 `final_result` 为准
- 新增 `backend/app/context/budget.py`：回答节点字符预算（最近对话 6 条 / 1200 字，记忆卡 8 条 / 600 字）
- 新增 `backend/app/context/assembler.py`：`assemble_answer_context` 统一装配 question / facts / final_result / recent_conversation / session_memory
- `ConsultationState` 增加 `session_memory`；intake 每轮更新；answer 的 `build_context` 改用装配器
- answer prompt 升到 v5：记忆类问题先看 `session_memory.details`，再看最近对话；找不到才说“没有记录到”
- 记忆筛选规则：有结构化事实、不是第一人称、或只是记忆指代追问，都不存；最多 10 条、单条 120 字
- 稳健性修正：模型可能把“下周”抽成持续时间；只要消息里有明确的个人细节线索（如“出差”“下周”），即使抽到字段也仍保留细节
- mock M6 复测：`conversation/far` 的 `prompt` 从 0 升到 1.0，`window` 仍为 0——信息确实从记忆卡进入，不是窗口变大
- 单测：487 passed（新增记忆卡 10 条、装配 3 条、图端到端 1 条）
- 模型模式复测（dev 8 条）：

| 组别 | n | state | window | prompt | answer | bypass | 否认回忆 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 全部 | 8 | 0.375 | 0.750 | 1.000 | 1.000 | 0 | 0 |
| fact / far | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| conversation / near | 2 | 0.000 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| conversation / far | 3 | 0.000 | 0.333 | 1.000 | 1.000 | 0 | 0 |

- 结论：`conversation/far` 的 `answer` 0.000 → 1.000、`prompt` 0 → 1.000，`window` 仍只有 0.333；至少两条远距细节在最近窗口之外，仍被正确回答。`否认回忆=0`，没有编造。
- Phase B 四项目标全部达成：对话类 `bypass=0`、`否认回忆=0`、context `answer=1.000`、far `prompt=1.000`。

**2026-11-11 · Phase B 第 2 课：decision 接入参考上下文**

- `decision_llm.build_context` 增加 `session_memory` 与 `recent_conversation`（4 条 / 300 字；4 条 / 800 字）
- decision prompt 升到 v2：参考上下文只用于理解指代，不当作新事实；用户已说过的信息不重复追问
- 尝试过 intake 参考上下文（JSON 包装）：prompt v3 F1 **0.8734**，prompt v4 修正字段归属后 **0.9057**，均低于基线 **0.9325**；主要错误在 `associated` / `severity` 之间来回漂移
- 决定回退 intake 到 v2 prompt：证据闸门本来就阻止参考上下文贡献事实，intake 吃上下文只增加噪声；多轮指代交给 decision / answer
- 顺带修复 `eval.run_eval` 未关闭限流导致 33 案例回归出现 429 的问题：评测是离线测量，不应被进程内限流打断；限流本身仍由 `test_ratelimit.py` 覆盖
- 单测：489 passed；33/33 mock 回归通过
- M1 回退后复测：v2 prompt（与 10 月基线完全同一份代码）两次结果为 **0.8750 / 0.8790**（P 0.8642 / 0.8846，R 0.8861 / 0.8734），当前模型下均值约 **0.877**；keyword 抽取仍是 0.4865，说明评测器与标注没变。
- **结论：0.9325 是 2026-10-26 的历史基线，当前 qwen-plus 行为已漂移，不能跨日期直接比较。** “参考上下文导致退化”的因果判断不成立——同一份 v2 代码今天也只有 0.877。
- 基于当前模型的新基线：v2 ≈ 0.877。
- v5（纯文本 + 字段归属 guidance）第一次复测 **0.8734**，`伴随表现` 仍 TP=1 / FP=7，未高于 v2；v4 的 0.9057 没有复现，按单次波动处理。**已回退到 v2**，`intake_llm.py` 与提交版无 diff。
- 最终：intake 保持解析器边界；decision 保留记忆接入；M1 后续以 **0.877** 作为当前模型基线。

**2026-11-11 · M11 general / medical 路由评测**

- 新增 `eval/router_eval.py` + `eval/probes/router_cases.jsonl`（14 条：10 dev / 4 holdout）
- 样本覆盖饮食搭配、营养常识、寒暄、情绪、日期、产品、倾诉，以及症状、药品、检查、红旗、儿童发热和两个边界案例
- v1 基线（dev 10 条）：整体 **0.800**；general 0.714、medical 1.000
- 错误：RT01「晚上我吃了一份生菜……健康不？」、RT02「这样的饮食均衡吗」都被判成 medical
- 原因：prompt 把“健康”当成医疗信号，且缺少“日常健康管理”的反例
- 修复：router prompt v2，去掉“健康”泛化触发词，明确饮食/营养/运动/作息算 general；只有症状、疾病、药品、检查、治疗、就医判断或医疗风险才算 medical；先看有没有具体身体不适，没有就 general
- v2 dev：**1.000**（general 7/7、medical 3/3）；holdout：**1.000**（4/4，含“减肥/晚饭不吃 → general”与“进食后胃疼 → medical”两个边界）
- 端到端（Docker，`USE_MODEL=1`）：同一条饮食问题返回 general 回答，`final_status` 为空、`followup_count=0`，未进入字段收集
- 全量：495 tests；33/33 mock；模型模式小回归 E001/E009/E017/E021/E025/E027 **6/6** 通过；Docker backend 已重建为 router v2
- 单测：新增 router eval 计分 2 条

**2026-11-11 · Phase B 第 3 课：长会话滚动摘要**

- 新增 `backend/app/memory/summary.py`：最近窗口之外累计 12 条触发；摘要 ≤300 字；模型失败保留旧摘要、游标不动，下一轮重试；没有旧摘要时用抽取式兜底
- 新增 `backend/app/memory/summary_llm.py` + `SessionSummary` schema：把 `previous_summary` 与 `new_turns` 合并成新摘要
- intake 接入：仅 `USE_MODEL=1` 且 `SUMMARY_ENABLED=1` 时触发；trace 记录 `summary_updated` / `summary_chars` / `summary_source`
- answer prompt v6、decision prompt v3 读取 `session_memory.summary`
- 导出视图与 API 增加 `summary` 字段
- 预算调整：细节从 10 条 × 120 字收到 **8 条 × 90 字**，加上 300 字摘要后最坏 **1438 字符（约 1.44K）**，守住 1.5K 目标
- 真实摘要调用：14 轮合成对话 → 113 字，599 输入 / 75 输出 token，覆盖症状、时间线、结论、出差约束、时间偏好、交通、社交来源和就诊准备
- 测试：502 passed；M6 mock 的 `prompt` 仍为 1.000
- 待补：>20 轮完整图会话的长期保持评测（M6 当前最长 8 轮，不会触发摘要）

**2026-11-11 · 长会话保持评测（LS01 首次运行）**

- 新增 `eval/long_session.py` + `eval/probes/long_session.jsonl`：LS01 24 轮 dev、LS02 20 轮 holdout
- 首次 LS01：1/3 命中；24 轮耗时 83.7s，摘要更新 3 次，最终摘要 120 字
- 首次结果里有两个评测器/探针问题，不是产品结论：
  - 第 12 轮 `window=✓` 是探针自己的回答把窗口点亮；已改为使用“探针前快照”
  - 第 24 轮“我最早说……”没有被识别成记忆指代（`memory_ref` 只认“最开始”）；已补“最早”并加测试
  - 第 18 轮是真实歧义：脚本后面又说了“更喜欢晚上”，探针问“一开始”时期望“下午”；已移除冲突偏好，脚本只保留一个时间偏好
- 修复后 v2 复测：**3/3 探针命中**；24 轮耗时 83.2s，摘要更新 3 次，最终摘要 148 字
- 第 12 轮（下周三）：`summary=✓ / details=✗ / window=✗`，回答正确
- 第 18 轮（下午）：`summary=✓ / details=✗ / window=✗`，回答正确
- 第 24 轮（前额/两天）：`summary=✓ / facts=✓`，回答正确
- 第 12 与 18 轮证明：回答确实依赖滚动摘要，而不是最近窗口或 details
- LS02 holdout 复测：**3/3 探针命中**；20 轮耗时 69.8s，摘要更新 2 次，最终摘要 116 字
  - 第 11 轮（下个月）：`summary=✓ / details=✓ / window=✗`
  - 第 16 轮（上午）：`summary=✓ / details=✗ / window=✗`，只能靠摘要
  - 第 20 轮（腹部/三天）：`summary=✓ / facts=✓`
- 结论：LS01 dev **3/3**、LS02 holdout **3/3**；滚动摘要在 20–24 轮会话里按预期工作
- 注意：均为单次运行；阶段末仍需重复 3 次确认稳定性

**2026-11-12 · Phase C 第 1 课：工具目录与风险分级**

- `ToolSpec` 增加 `risk_class`（`read_only` / `write` / `sensitive`）、`scope`（`internal` / `external`）和派生属性 `auto_callable`
- `tool_schema` 同步暴露三个字段；MCP 的 `inputSchema` 不受影响
- 新增 `backend/app/tools/catalog.py`：注册、查询、只读过滤、`prompt_entries`、未注册工具的结构化失败
- 三个知识工具显式声明 `read_only` / `internal`
- 单测：515 passed（新增 7 条目录测试）
- M7 基线暂不变：决策 schema 还没有 `tool_name`，模型仍不能选工具

**2026-11-12 · Phase C 第 2 课：决策层选工具与图内通用执行**

- `DecisionChoice` 增加 `tool_name` / `tool_args`；decision prompt 升到 v4，注入只读工具目录
- `decision.py` 新增 `rule_decision` / `gate_decision` / `validate_decision`；旧的 `rule_action` / `validate_action` 保留，避免破坏既有测试
- 校验规则：未知工具回退默认、重复工具收束、单轮最多 3 次、`write` / `sensitive` 拒绝自动调用
- `state` 增加 `next_tool` / `next_tool_args` / `tool_call_count`；intake 每轮重置
- graph 的 tool 节点改为按工具名与参数执行，trace 记录 `called` / `args_keys`
- 单测：525 passed；M7 mock 仍为 0.625（规则路径只调 `department_route`）
- M7 模型复测进行中

**2026-11-12 · Phase C 第 3 课：就医准备走确定性工具路径**

- 新增 `general.is_guideline_question`：识别“就诊前要带什么 / 挂号流程 / 第一次去医院”等问题
- `opening.answer_guideline` 在 general 路径里直接调用 `guideline_retrieve`，用检索结果组织回答；查不到就明说，不拿相近条目凑数
- 该路径在模型路由之前执行，mock 模式也能调工具；`answer_followup` 也接入了同一路径
- trace 同时记录 `general_route` / `answer` 与 `tool` 两个条目，M7 能读到实际工具调用
- 单测：537 passed（新增 12 条就医准备路径测试）
- M7 mock：**1.000**（department 3/3、guideline 3/3、risk 2/2，unreachable 0）
- M7 模型：**1.000**（10/10；department、general、guideline、risk 全部正确，unreachable 0、危险误调 0）
- 第二次模型复测：**1.000**（两次独立运行一致，稳定性确认）

**2026-11-13 · Phase C 第 4 课：工具结果摘要**

- `ToolSpec` 新增 `summarize` 字段；`contract.clip_summary` 统一 400 字上限，省略号表示截断
- 三个知识工具各写摘要函数：科室保留方向/依据/出处/命中数；红旗保留“不确定≠安全”；指南最多三条要点
- 新增 `catalog.prompt_tool_results`：在装配 prompt 时压缩，失败结果变成“调用失败（timeout/unavailable）”
- `state.tool_results` 保持原文，渲染、出处与错误分支不受影响；只有模型看到摘要
- 用真实知识条目实测：department 1010 → 42 字（24x）、guideline 909 → 136 字（6.7x）、red_flag 975 → 42 字（23x）
- 单测：539 passed + 6 skipped（新增 8 条摘要测试）

**2026-11-13 · Phase C 第 5 课：MCP client（stdio）**

- 新增 `backend/app/tools/mcp_client.py`：后台事件循环线程桥接 async MCP 会话，`ToolSpec.handler` 仍是同步契约
- 配置：`MCP_ENABLED` 默认 0；`MCP_SERVERS` 是 JSON 数组，每个 server 必须显式列 `allowed_tools`，空列表等于全部拒绝
- 发现与注册：`list_tools` → 按白名单生成 `ToolSpec`（`read_only` + `external` + 动态 Pydantic 参数模型），重名时保留内部工具
- 运行时：调用失败/超时转成结构化 error；连续失败 3 次熔断 30s；某个 server 启动失败只跳过，不阻断主流程
- `registry.call_tool` 改为统一走 `DEFAULT_CATALOG`，外部工具与内部工具同一条执行路径；`KNOWLEDGE_BACKEND=stub` 仍只替换知识类工具
- 集成测试连项目自己的 MCP server（stdio 子进程 + stub 知识库），验证工具发现、白名单、真实调用往返与启动失败包装
- 单测：555 passed + 6 skipped（新增 16 条 MCP client 测试）