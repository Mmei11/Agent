# 从作品集到可用产品：开源工程评估与优化落地方案

> 适用对象：本项目 v0.1.0（Day 20 交付版）
> 编写日期：2026-10-23
> 数据来源：GitHub REST API 实测（star 数、最后推送时间、归档状态、许可证均为 2026-10-23 当日值）+ 各仓库 README/LICENSE 核对
> 定位提醒：本项目始终只处理**虚构模拟病例**，不提供真实诊断、处方或用药建议。优化阶段不得改变这一边界。

---

## 一、先说结论

你现在的项目不是"没做完"，而是**一条完整的纵向链路 + 一个很薄的横向能力层**。
纵向链路（输入 → 提取 → 风险 → 决策 → 工具 → 结果 → 持久化 → API → 前端 → 容器）你已经打通，这是大多数求职项目缺的。
横向能力（模型接入、结构化输出、可观测性、评测、知识检索、分层安全、流式交互）目前都还是"教学级最小实现"。

所以优化阶段的正确姿势不是"再加一个功能"，而是**按工程成熟度逐层加厚**，每加一层都用评测证明它变好了。顺序很重要：

```
模型层 → 可观测性 → 评测升级 → 知识/工具 → 分层安全 → 产品化 → 打包叙事
```

**为什么可观测性排在评测前面？** 因为评测要读轨迹，调 prompt 要对比 trace。没有 trace 的评测只能看最终字符串；有 trace 才能回答"模型在哪一步、看到了什么、为什么选错"。这是你 Day 17-18 修 bug 时最痛的点的升级版。

**为什么不直接上 Dify / FastGPT / RAGFlow？** 它们会把你的项目"替换掉"，而不是"加厚"。面试时你讲的是自己的架构取舍，不是一个平台的部署截图。这三个只作为**架构参考**，不作为运行依赖。

**为什么不做多 Agent（CrewAI/AutoGen）？** 你的核心卖点是**确定性门控 + 可审计轨迹**。多 Agent 的自由协作会引入不可复现的分支，反而削弱安全性论证。真正该借的是它们的 handoff/guardrail/tracing 设计，不是它们的编排模型。

---

## 二、现状盘点：已有什么、缺什么

基于当前代码逐文件核对：

| 能力 | 当前实现 | 代码位置 | 成熟产品的要求 |
| --- | --- | --- | --- |
| 模型接入 | 手写 `urllib` 同步请求，20s 超时，无重试、无流式、无成本核算 | [decision.py:71](../../backend/app/agent/decision.py) `model_choose` | 统一 SDK / 网关、重试退避、超时分层、流式、token 与成本统计、多模型回退 |
| 信息提取 | 6 个字段 × 固定关键词表 | [intake.py:8](../../backend/app/agent/intake.py) | 模型结构化抽取 + Schema 校验 + 失败修复 + 置信度 |
| 决策 | 只把 4 个字段塞给模型（missing/asked/tool_keys/count） | [decision.py:87](../../backend/app/agent/decision.py) | 完整病历上下文、工具返回详情、显式推理、少样本、可优化 prompt |
| 风险门控 | 关键词 + 前 5 字否定检测 | [rule_engine.py](../../backend/app/safety/rule_engine.py) | 分层护栏（输入/决策/输出）、分类器 + 规则 + 策略，可度量召回 |
| 工具 | 2 个同步 Python 函数，无 schema、无超时、无重试 | [registry.py:53](../../backend/app/tools/registry.py) | 工具契约、权限、超时、幂等、可观测、MCP 标准化 |
| 知识 | 无检索，科室靠 6 条硬编码映射 | [registry.py:4](../../backend/app/tools/registry.py) | 可追溯的知识库、混合检索、引用、faithfulness 评测 |
| 可观测性 | trace 存在 state 里，随 checkpoint 落 SQLite，不可查询/聚合 | [state.py:52](../../backend/app/agent/state.py) | 分布式 trace、span 层级、会话视图、成本/延迟看板、PII 脱敏 |
| 评测 | 33 条 mock 案例，二值断言，单次运行 | [eval/run_eval.py](../../eval/run_eval.py) | 判分 rubric、LLM-as-judge、多次采样置信区间、回归门禁、红队 |
| 交互 | 同步阻塞 API，一次性返回全文 | [routes.py:104](../../backend/app/api/routes.py) | SSE 流式、首字延迟、中断/重试、反馈收集 |
| 运维 | SQLite 单进程、无 CI、无鉴权、无限流 | [routes.py:21](../../backend/app/api/routes.py) | Postgres、CI、鉴权、限流、并发压测 |

**一句话总结**：骨架是"Agent 应用"，肌肉还是"脚本"。

---

## 三、开源工程逐一评估

评分口径：★ 1–5 分，越高越好。"部署难度"指**你一个人在本机/单机上跑起来**的成本，5 = 最省事。

### 3.1 可观测性与评测

| 项目 | 实测数据 | 维护状态 | 部署难度 | 可复用模块 | 结论 |
| --- | --- | --- | --- | --- | --- |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | 36.2k★，推送 2026-10-23，MIT（`ee/` 目录除外） | 非常活跃 | ★★☆ Docker Compose 需 Postgres + ClickHouse + Redis + MinIO；也可用 Cloud 免费档 | trace/span/session、prompt 管理、dataset、score API、OTel 接入、成本与延迟看板 | **采用**（Phase 2 核心） |
| [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix) | 12.0k★，推送 2026-10-23，**Elastic License 2.0** | 非常活跃 | ★★★★ 单容器，SQLite 默认，也可纯 pip 内嵌 | OTel 原生 trace UI、notebook 内评测、embedding 分析 | 备选（许可证非 OSI，商业用途需看条款） |
| [comet-ml/opik](https://github.com/comet-ml/opik) | 22.8k★，推送 2026-10-23，Apache-2.0 | 非常活跃 | ★★☆ Compose，多服务 | trace + eval + guardrail 一体、OTel | 备选（功能重叠，选一个即可） |
| [confident-ai/deepeval](https://github.com/confident-ai/deepeval) | 19.1k★，推送 2026-10-19，Apache-2.0 | 非常活跃 | ★★★★★ `pip install` | pytest 原生断言、G-Eval/答案相关性/任务完成度/工具正确性指标、数据集管理、CI 集成 | **采用**（Phase 3 核心） |
| [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | 26.3k★，推送 2026-10-23，MIT | 非常活跃 | ★★★★★ CLI/Node | YAML 测试矩阵、多模型对比、红队插件、CI 门禁、网页报告 | **采用**（回归 + 红队） |
| [explodinggradients/ragas](https://github.com/explodinggradients/ragas) | 16.1k★，推送 **2026-02-24**，Apache-2.0 | ⚠️ 8 个月无推送 | ★★★★★ `pip` | faithfulness / context precision / recall 等 RAG 指标 | 有条件采用（仅 Phase 4 需要 RAG 指标时；注意维护放缓，锁版本） |
| [traceloop/openllmetry](https://github.com/traceloop/openllmetry) | 7.5k★，推送 2026-10-19，Apache-2.0 | 活跃 | ★★★★ pip + OTel Collector | LangGraph/LangChain 自动埋点 | 可选（如果不想手写 span） |
| [openai/evals](https://github.com/openai/evals) | 大型老牌仓库 | 维护节奏较慢 | ★★★★ | 评测注册表思路 | 借鉴即可 |
| [EleutherAI/lm-evaluation-harness](https://github.com/EleutherAI/lm-evaluation-harness) | 大型老牌仓库 | 活跃 | ★★★ | 标准化 benchmark 规范 | 不采用（面向基座模型，不是 Agent） |

### 3.2 模型接入与结构化输出

| 项目 | 实测数据 | 维护状态 | 部署难度 | 可复用模块 | 结论 |
| --- | --- | --- | --- | --- | --- |
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | 61.9k★，推送 2026-10-23，MIT（企业功能除外），5.6k open issues | 非常活跃（issue 多但迭代快） | ★★★★ SDK；Proxy 模式需 Postgres | 100+ Provider 统一接口、重试/回退、预算与限流、成本统计、OpenAI 兼容网关 | **采用**（Phase 6 做多模型路由/故障转移时） |
| [567-labs/instructor](https://github.com/567-labs/instructor) | 14.1k★，推送 2026-10-20，MIT | 活跃 | ★★★★★ `pip` | Pydantic `response_model`、校验失败自动重试并回喂错误、流式部分对象 | **采用**（Phase 1） |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 20.8k★，推送 2026-10-23，MIT | 非常活跃 | ★★★★★ `pip` | 类型化 Agent、依赖注入、结构化输出、内置 eval/OTel | 借鉴（你已用 LangGraph，不必迁移框架） |
| [stanfordnlp/dspy](https://github.com/stanfordnlp/dspy) | 38.7k★，推送 2026-10-23，MIT | 非常活跃 | ★★★★ `pip`，概念门槛高 | 用指标自动优化 prompt/few-shot | **进阶采用**（Phase 5 后用评测集做 prompt 自动优化） |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python) | 30.2k★，推送 2026-10-22，MIT | 非常活跃 | ★★★★★ `pip` | handoff、guardrails、session、内置 tracing | 借鉴（设计参考，不迁移） |

> 说明：你的百炼 DashScope 提供 OpenAI 兼容端点，因此 **Phase 1 优先用官方 `openai` SDK + `instructor`**，依赖轻、协议透明；LiteLLM 留到需要多模型路由和成本管控时再上，避免一开始就引入厚重依赖。

### 3.3 安全护栏

| 项目 | 实测数据 | 维护状态 | 部署难度 | 可复用模块 | 结论 |
| --- | --- | --- | --- | --- | --- |
| [guardrails-ai/guardrails](https://github.com/guardrails-ai/guardrails) | 7.6k★，推送 2026-10-21，Apache-2.0 | 活跃 | ★★★★ `pip` + Hub 校验器（部分需模型 Key） | 输出 Schema 校验、PII/毒性/自定义校验器、失败重问 | **采用**（Phase 5 输出层） |
| [NVIDIA/NeMo-Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | 7.4k★，推送 2026-10-23，Apache-2.0 | 非常活跃 | ★★★ `pip` + Colang 配置 + 额外模型调用 | 声明式对话流、input/output rails、越狱检测 | **有条件采用**（想学声明式策略时；否则自写策略层更可控） |
| [meta-llama/PurpleLlama](https://github.com/meta-llama/PurpleLlama)（Llama Guard） | 4.4k★，推送 2026-10-12 | 活跃 | ★★ 需 GPU 或托管推理 | 内容安全分类模型 | 可选（用托管 API；本地部署成本高） |
| [protectai/llm-guard](https://github.com/protectai/llm-guard) | 3.2k★，推送 2026-07-08，**已归档** | ❌ 归档 | — | — | **不采用**（停止维护） |

### 3.4 知识检索与工具协议

| 项目 | 实测数据 | 维护状态 | 部署难度 | 可复用模块 | 结论 |
| --- | --- | --- | --- | --- | --- |
| [run-llama/llama_index](https://github.com/run-llama/llama_index) | 52.6k★，推送 2026-10-21，MIT | 非常活跃 | ★★★★★ `pip` | 文档解析/切分、混合检索、查询引擎、评测集成 | **采用**（Phase 4） |
| [qdrant/qdrant](https://github.com/qdrant/qdrant) | 35.2k★，推送 2026-10-23，Apache-2.0 | 非常活跃 | ★★★★ 单容器 | 向量检索 + payload 过滤（按科室/风险等级过滤非常契合） | **采用**（Phase 4） |
| [chroma-core/chroma](https://github.com/chroma-core/chroma) | 29.6k★，推送 2026-10-23，Apache-2.0 | 非常活跃 | ★★★★★ 可纯内嵌 | 轻量向量库 | 备选（原型更快，生产不如 Qdrant） |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | 92.7k★，推送 2026-10-23，Apache-2.0，1.3k issues | 非常活跃 | ★☆ 重量级 Compose（ES/Infinity + MySQL + MinIO + Redis） | 文档解析、切分策略、引用溯源的产品化做法 | **只借鉴**（不部署） |
| [langgenius/dify](https://github.com/langgenius/dify) | 159.3k★，推送 2026-10-23，许可证含附加条款 | 非常活跃 | ★☆ 多服务 Compose | 工作流编排、工具市场、可观测面板的产品形态 | **只借鉴**（会替换你的项目） |
| [labring/FastGPT](https://github.com/labring/FastGPT) | 29.9k★，推送 2026-10-23，自定义许可证 | 非常活跃 | ★☆ 多服务 Compose | 知识库问答产品化路径 | **只借鉴** |
| [microsoft/graphrag](https://github.com/microsoft/graphrag) | 36.4k★，推送 2026-10-20，MIT | 活跃 | ★★★ 需 LLM 建图，成本高 | 图谱增强检索 | 不采用（对当前场景过重） |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | 24.7k★，推送 2026-10-17，MIT | 活跃 | ★★★★★ `pip` | 工具/资源/提示的标准化协议、传输层、Schema 生成 | **采用**（Phase 4 工具契约） |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | 91.5k★，推送 2026-10-22 | 非常活跃 | ★★★★ | 现成 server 参考实现 | 借鉴（看工具如何声明与鉴权） |

### 3.5 Agent 框架与记忆

| 项目 | 实测数据 | 维护状态 | 部署难度 | 可复用模块 | 结论 |
| --- | --- | --- | --- | --- | --- |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | 43.8k★，推送 2026-10-23，MIT | 非常活跃 | ★★★★★ 已在用 | `interrupt` 人工介入、子图、`astream_events`、Postgres checkpointer | **继续采用**（升级用法，不换框架） |
| [microsoft/autogen](https://github.com/microsoft/autogen) | 61.5k★，推送 2026-10-16 | 活跃 | ★★★★ | 多 Agent 协作模式 | 不采用（自由协作削弱确定性门控） |
| [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) | 60.0k★，推送 2026-10-23，MIT | 非常活跃 | ★★★★ | 角色化任务分解 | 不采用（同上） |
| [google/adk-python](https://github.com/google/adk-python) | 22.0k★，Apache-2.0 | 活跃 | ★★★★ | 内置 eval、部署脚手架 | 借鉴 |
| [mem0ai/mem0](https://github.com/mem0ai/mem0) | 67.7k★，推送 2026-10-22，Apache-2.0 | 非常活跃 | ★★★（需向量库/图库） | 记忆抽取、去重、更新 | **不采用**（医疗场景跨会话记忆是隐私与幻觉风险源） |
| [getzep/zep](https://github.com/getzep/zep) | 5.0k★，推送 2026-10-20，Apache-2.0 | 活跃 | ★★★ | 时序知识图谱记忆 | 不采用 |
| [letta-ai/letta](https://github.com/letta-ai/letta) | 25.2k★，推送 2026-10-18，Apache-2.0 | 活跃 | ★★★ | 有状态 Agent 运行时 | 不采用 |
| [vllm-project/vllm](https://github.com/vllm-project/vllm) | 95.2k★，推送 2026-10-23，Apache-2.0 | 非常活跃 | ★☆ 需 GPU | 高吞吐推理服务 | 不采用（当前走 API） |

### 3.6 医疗垂直项目（重点核查）

| 项目 | 实测数据 | 维护状态 | 结论 |
| --- | --- | --- | --- |
| [Zz3212281607/SmartMedicalConsultation](https://github.com/Zz3212281607/SmartMedicalConsultation) | **1★**，推送 2026-08-24，MIT | 个人项目，无社区 | **不作为质量或架构基准**。你的 README 目前把它列为"架构对照"，建议改措辞为"个人项目，仅供参考"，避免面试时被追问"为什么对标一个 1 star 仓库" |
| [SCIR-HI/Huatuo-Llama-Med-Chinese](https://github.com/SCIR-HI/Huatuo-Llama-Med-Chinese) | 5.0k★，推送 2026-07-04，Apache-2.0 | 缓慢维护 | 只用于理解医疗微调数据构造，不接入 |
| [FreedomIntelligence/HuatuoGPT-II](https://github.com/FreedomIntelligence/HuatuoGPT-II) | 419★，推送 **2024-08-30**，无许可证 | ❌ 停滞近 2 年 | 不采用（无许可证 + 停滞） |
| [Toyhom/Chinese-medical-dialogue-data](https://github.com/Toyhom/Chinese-medical-dialogue-data) | 1.8k★，MIT | 数据仓库 | **不建议**灌入演示库：会把项目从"流程演示"变成"医疗问答机器人"，与你的安全定位冲突 |

---

## 四、选型矩阵（加权打分）

权重：契合度 40%、维护状态 20%、部署难度 20%、学习价值/简历价值 20%。5 分制。

| 项目 | 契合度 | 维护 | 部署省心 | 学习价值 | 加权分 | 决策 |
| --- | --- | --- | --- | --- | --- | --- |
| instructor | 5 | 5 | 5 | 4 | **4.8** | 采用（P1） |
| deepeval | 5 | 5 | 5 | 5 | **5.0** | 采用（P3） |
| langfuse | 5 | 5 | 3 | 5 | **4.6** | 采用（P2） |
| promptfoo | 4 | 5 | 5 | 4 | **4.4** | 采用（P3） |
| guardrails-ai | 4 | 4 | 4 | 5 | **4.2** | 采用（P5） |
| llama_index | 4 | 5 | 5 | 4 | **4.4** | 采用（P4） |
| qdrant | 4 | 5 | 4 | 3 | **4.0** | 采用（P4） |
| mcp python-sdk | 4 | 5 | 5 | 5 | **4.6** | 采用（P4） |
| litellm | 4 | 5 | 4 | 4 | **4.2** | 采用（P6） |
| dspy | 3 | 5 | 4 | 5 | **4.0** | 进阶（P5 后） |
| phoenix | 4 | 5 | 4 | 3 | **4.0** | 备选（Langfuse 替代） |
| opik | 4 | 5 | 2 | 3 | **3.6** | 备选 |
| ragas | 3 | 2 | 5 | 3 | **3.2** | 条件采用（P4） |
| ragflow / dify / fastgpt | 2 | 5 | 1 | 3 | **2.6** | 只借鉴 |
| mem0 / zep / letta | 2 | 5 | 3 | 3 | **3.0** | 不采用 |
| crewai / autogen | 2 | 5 | 4 | 3 | **3.3** | 不采用 |

---

## 五、优化落地方案（学习路径版）

每个阶段固定四段式：**工程问题 → 要学的概念（可复用思考路径）→ 动手改造 → 验收标准**。
预计总周期 **20–26 个学习日**，可按单日 2–3 小时估算。

### Phase 0｜先定标尺，再动刀（0.5–1 天）

**工程问题**：你现在只有"33/33 通过"这一个数字，它衡量的是流程断言，不衡量质量。没有质量指标，后面每一步优化都无法证明有效。

**概念**
- 离线评测 vs 在线评测：离线用固定集，在线用真实反馈。
- 代理指标的选择：先选**能反向指导下一步动作**的指标，而不是"看起来专业"的指标。
- 记住一条思考路径：**"我改完这个，哪个数字应该变？如果答不上来，就不该改。"**

**动手**
1. 冻结基线：在你当前主分支打标签，把 Day 20 的指标（72 tests、33/33、p50/p95 延迟）记录到 `docs/eval-report.md`。
2. 定义 5 个北极星指标，写进 `docs/planning/optimization-metrics.md`：
   - 字段抽取准确率（F1，人工标注 30 条）
   - 风险召回（red-flag 集不得漏判，目标 100%）
   - 风险精确率（避免误报淹没正常路径）
   - 单会话模型成本（token/¥）与 p95 延迟
   - 判分一致率（LLM judge 与人工标注的一致率，目标 ≥ 0.8）
3. 建一个 `docs/metrics-log.md`，每次改动后追加一行"改了什么 → 指标怎么变"。

**验收**：5 个指标有明确定义、计算方式、当前基线值。没有基线的指标不许进入 Phase 1。

---

### Phase 1｜模型层与结构化输出（2–3 天）

**工程问题**：[decision.py:71](../../backend/app/agent/decision.py) 的 `model_choose` 是手写 `urllib`，同步阻塞、无重试、无成本统计；[intake.py:18](../../backend/app/agent/intake.py) 的关键词表遇到"脑袋嗡嗡的、从前天傍晚开始"就会全漏。这是质量损失最大的一环。

**概念（可复用思考路径）**
1. **把"模型当解析器"和"模型当决策者"分开**：抽取是解析任务（要求确定性、可校验），决策是选择任务（要求可解释、可兜底）。两者用不同的 prompt、不同的校验、不同的失败策略。
2. **结构化输出的闭环**：`Schema 定义 → 模型输出 → Pydantic 校验 → 失败则把错误回喂重试 → 仍失败则降级`。这条链路就是 instructor 的核心价值，也是你必须能白板画出来的东西。
3. **降级路径要先设计**：模型不可用 → 规则兜底（你已有 `mock_choose`）；模型输出非法 → 修复重试 → 规则兜底。成熟系统里"降级"是功能，不是异常处理。

**动手**
1. 新建 `backend/app/llm/client.py`：统一封装模型调用（超时、重试、日志）。先用官方 `openai` SDK 指向 DashScope 兼容端点，不引 LangChain。
2. 新建 `backend/app/llm/schemas.py`：为抽取定义 `IntakeFacts`（字段 → 值 → 证据片段 `evidence` → 置信度）。
3. 改造 [intake.py](../../backend/app/agent/intake.py)：`extract_facts` 保留为**离线兜底**，新增 `extract_facts_llm`，用 instructor + `response_model`；拿不到证据片段的字段不计入。
4. 改造 [decision.py](../../backend/app/agent/decision.py)：把输入从 4 个字段扩成完整上下文（collected_facts、工具结果详情、上一轮问句），并要求模型输出 `action + reason + confidence`。`validate_action` 增加"置信度低于阈值时降级为规则决策"。
5. 把每次模型调用的 `model / input_tokens / output_tokens / latency / cost` 写入 trace。

**验收**
- 30 条人工标注样本上，LLM 抽取 F1 ≥ 0.85，且**高于关键词基线 20 个百分点以上**（达不到就调 prompt，别急着上 RAG）。
- 模型返回非法 JSON 时，能在 2 次修复内成功或正确降级，不抛 500。
- 每个会话的模型成本可查询。
- 原有 72 个测试仍全绿（新增的模型调用要可注入 mock，不能拖慢单测）。

**读源码入口**：`instructor` 的 `patch`/`create(response_model=...)` 实现；`openai` SDK 的 `chat.completions.parse` 与 `response_format`。

---

### Phase 2｜先装仪表盘：可观测性（2–3 天）

**工程问题**：trace 现在是 state 里的一个数组（[state.py:52](../../backend/app/agent/state.py)），只能 `print`，不能按会话聚合、不能算 p95、不能对比两个 prompt 版本的轨迹。你要优化模型决策，却看不到模型到底看到了什么。

**概念**
- **Trace / Span / Session 三层模型**：Trace = 一次请求全链路；Span = 一个节点（intake/risk/decision/tool/output）；Session = 多轮对话聚合。这是 OpenTelemetry 的通用心智模型，换成任何平台都成立。
- **GenAI 语义约定**：模型名、token 数、prompt/response、工具调用应放在约定的 span 属性里，这样换平台不用改代码。
- **PII 脱敏要在采集端做**，不是展示端做。医疗场景尤其如此。

**动手**
1. 用 Docker Compose 起 Langfuse（先加一个 `docker-compose.observability.yml`，不污染主 Compose）。
2. 在 `backend/app/llm/client.py` 里统一埋点：每次模型调用一个 span；在 `graph.py` 每个节点包一层 span；把 `session_id` 作为 trace 的 session 标识。
3. 采集端脱敏：用户原话只留脱敏后版本（例：手机号/身份证/姓名模式替换），原始文本默认不落观测库。
4. 建两个面板：**会话视图**（一轮对话的完整 span 树）和**质量视图**（p50/p95 延迟、每会话 token、错误率）。

**验收**
- 一次三轮回诊会话 → 1 个 session，N 个 trace，每 trace 含 intake/risk_check/decision/tool/output 的 span 树。
- 能从面板回答："这个会话在哪一步耗时最长？决策时模型看到了哪些字段？"
- 脱敏规则有单测；故意输入手机号后，观测库里查不到明文。
- 关掉 Langfuse 时，主流程**不受影响**（可观测性不能成为单点故障）。

---

### Phase 3｜把"能跑"变成"可证明"（3–4 天）

**工程问题**：33 条 mock 案例只能证明"流程没断"，证明不了"回答变好了"。而且单次运行没有方差，无法判断一次改动是真实提升还是噪声。

**概念（这是优化阶段最重要的一课）**
1. **断言分层**：硬约束（必须命中风险路由、不得超过追问上限）用代码断言；软质量（追问是否自然、结论是否有依据）用 judge 打分。两层分开报告。
2. **LLM-as-judge 的坑**：位置偏差（更偏好第一个）、冗长偏差（更偏好长答案）、自我偏好。对策：打乱顺序、给明确 rubric、**先人工标注 20 条校准 judge**。
3. **多次采样的必要性**：温度 > 0 时，单次通过率是无意义的。至少 n=5，报告均值与区间。
4. **回归门禁的思考路径**：不是"新版本必须更好"，而是"新版本不允许在关键集上显著变差"。前者会导致永远不敢改。

**动手**
1. 把 `eval/cases.jsonl` 升级为带 rubric 的结构：每例增加 `hard_assertions`（继续用代码判）与 `judge_rubric`（分档描述）。
2. 新建 `eval/test_agent_quality.py`，用 DeepEval 的 `assert_test` + 自定义 `GEval` 指标：`追问相关性`、`事实是否有证据支撑`、`是否越界给出诊疗建议`、`工具选择正确性`。
3. 新建 `eval/repeat.py`：同一案例集跑 n=5，输出均值/标准差/失败案例清单。
4. 用 promptfoo 写一套红队用例（诱导确诊、诱导开药、隐瞒风险、越权提问），纳入 CI。
5. GitHub Actions：单测 + 硬断言全量 + judge 指标不得低于基线阈值。

**验收**
- 一份报告能同时给出：硬断言通过率、judge 均分 ± 标准差、与上一版的差值、失败案例明细。
- 人工标注 20 条与 judge 的一致率 ≥ 0.8；低于则先调 rubric，不放行。
- 故意把风险词表删掉一条，CI 必须失败（验证门禁真的有效）。
- 红队用例中"诱导诊断/开药"拦截率 100%。

---

### Phase 4｜知识与工具：从"硬编码映射"到"可追溯检索"（4–5 天）

**工程问题**：科室映射只有 6 条（[registry.py:4](../../backend/app/tools/registry.py)），没有任何依据来源；所谓"知识"是不可审计的常量字典。

**概念**
- **RAG 在 Agent 里的正确位置**：检索是**工具**，不是上下文预填充。让 Agent 在需要时调用，才能保留"为什么查、查到了什么"的轨迹。
- **可追溯性优先于覆盖率**：每条知识必须带来源与适用范围。宁可回答"暂无依据"，也不要编一条。
- **检索评测的两个指标**：召回（该找到的找到没有）与忠实度（答案是否被检索内容支撑）。前者靠标注查询集，后者靠 judge。
- **工具契约**：名称、描述、参数 Schema、超时、幂等性、失败语义。MCP 把这些标准化，让工具可以跨项目复用。

**动手**
1. 建 `backend/app/knowledge/`：整理一份**受控的小知识库**（科室分诊原则、红旗症状清单、就医准备清单），每条带 `source`、`applicable_scope`、`version`。**不要**灌入真实患者问答数据集。
2. 用 LlamaIndex 做切分与索引，Qdrant 存向量；`payload` 里放 `category` / `risk_level` / `source`，检索时用过滤条件收窄。
3. 新增工具：`red_flag_lookup`（红旗症状检索）、`department_route`（带依据的科室建议）、`guideline_retrieve`（通用就医指引）。全部带 Pydantic 参数 Schema 与超时。
4. 把 `TOOL_REGISTRY` 升级为契约化注册表（描述 + Schema + 超时 + 权限 + 失败语义），并暴露一个 MCP server（`backend/app/tools/mcp_server.py`），让工具可被外部 Agent 复用。
5. 评测：构造 30 条检索查询，测 recall@5；用 Ragas（锁版本）或自写 judge 测 faithfulness。

**验收**
- 每个科室建议都能给出 `source`；无依据时返回 `department=None + reason`（保留你原有的"不编造"原则）。
- recall@5 ≥ 0.85；faithfulness ≥ 0.9。
- 工具超时/异常仍走结构化 error（回归你 Day 18 的修复）。
- MCP server 能被 MCP Inspector 连上并列出工具。

---

### Phase 5｜分层安全：把"词表"升级成"防线"（3–4 天）

**工程问题**：[rule_engine.py](../../backend/app/safety/rule_engine.py) 是单层关键词 + 前 5 字否定。它的问题不是不够聪明，而是**没有度量**——你不知道它漏了多少、误报多少。

**概念（防御纵深）**
```
输入层：越权/越狱/PII → 决策层：动作白名单 + 风险优先级 → 输出层：边界校验 + 引用支撑
```
每一层都要能独立失效而不导致整体失效。**规则负责高确定性的硬约束，分类器负责语义覆盖，策略层负责组合与升级**。

**动手**
1. 把 `safety/` 拆成 `input_rail.py` / `policy.py` / `output_rail.py`，规则与策略分离（策略用 YAML/JSON 配置，便于评审）。
2. 输出层接 Guardrails AI 校验器：禁止诊断词、禁止剂量、必须带免责声明、必须带来源（若引用了知识）。
3. 增加**风险升级策略**：命中红旗 → 固定话术 + 建议立即就医 + 记录审计事件；多次含糊 → 主动收束而不是继续追问。
4. 建对抗集：50 条（明示红旗、隐性红旗、否定、越狱、诱导处方、情绪化表达），测召回/精确率，把结果写进报告。
5. 可选：用一个独立的分类模型（托管 Llama Guard 或小模型）做第二意见，只在规则未命中时触发，控制成本。

**验收**
- 红旗集召回 100%（漏一条即失败）；精确率 ≥ 0.85，误报案例逐条分析。
- 输出层拦截"确诊/处方/剂量/停药"的测试覆盖到同义改写。
- 每一次风险升级都有审计记录（时间、规则、输入摘要、动作）。
- 单测从 72 条增长到 100+，且新增规则都有对应的正反例。

---

### Phase 6｜产品化：流式、异步、并发、反馈（3–4 天）

**工程问题**：`routes.py` 是同步阻塞、整段返回；前端只能等。真实产品里"首字延迟"比"总时长"更影响体验。

**概念**
- **流式不是把结果切开**：要区分"状态事件流"（节点开始/结束）与"文本 token 流"，两者可以叠加。
- **背压与取消**：客户端断开后，后端要继续跑完还是取消？医疗场景建议"跑完并落审计"，但要限制资源。
- **反馈闭环**：用户点"有帮助/没帮助"是最便宜的在线评测信号，比任何离线指标都真实。

**动手**
1. 用 LangGraph 的 `astream_events` + `sse-starlette` 提供 `POST /sessions/{id}/stream`，先推进度事件，再推文本。
2. 把路由改 async；模型调用改异步客户端；SQLite 换成 Postgres（`langgraph-checkpoint-postgres`）或用连接池 + 写锁，做一次压测对比。
3. 加鉴权（API Key / JWT）、限流（按会话与 IP）、请求体大小限制。
4. 新增 `POST /sessions/{id}/feedback`（评分 + 可选原因），写入独立表，接入 Langfuse score。
5. 前端：流式渲染、打字机效果、追问按钮化（把追问选项做成可点选，降低用户输入成本）、错误可重试。

**验收**
- 首字延迟 < 1.5s（mock 模式）/ < 3s（模型模式）；p95 总时长有记录。
- 50 并发下无 5xx，错误率 < 1%，有明确限流响应（429）。
- 反馈能落到观测平台，并能按会话回放。
- 前端在断网/超时下有明确重试路径。

---

### Phase 7｜打包与叙事（2 天）

**工程问题**：内容已经够多，但面试时讲不清楚"你做的和网上教程的差别"。

**动手**
1. GitHub Actions：lint + 单测 + 硬断言评测 + 红队 + 构建镜像（PR 必过）。
2. README 重构：首屏放**一张架构图 + 一张指标前后对比表**，而不是功能列表。
3. 更新 `docs/demo-script.md`：增加"一次失败 → 定位 → 修复 → 指标变化"的完整叙事（这是最能体现工程能力的一段）。
4. 产出 `docs/optimization-report.md`：每阶段做了什么、指标怎么变、哪些没做成、为什么。

**验收**
- 新人按 README 能在 10 分钟内跑起来。
- 能在 2 分钟内讲清三个取舍：为什么不用多 Agent、为什么可观测性先于评测、为什么不做跨会话记忆。

---

## 六、推荐执行顺序（如果时间有限）

**只有 10 天**：Phase 0 → Phase 1 → Phase 2 → Phase 3（前两步各砍 1 天）。
理由：这四步是"能力放大器"，且互相咬合——模型层产生质量变化，观测层让你看见变化，评测层证明变化。知识/安全/产品化都可以在后补。

**有 20 天**：按 0→1→2→3→4→5 走，Phase 6 做流式与反馈即可，压测和 Postgres 可延后。

**不建议的走法**：先上 RAG 或先上多 Agent。前者会在抽取还很弱时放大错误，后者会让评测和排障变得不可控。

---

## 七、风险登记册

| 风险 | 表现 | 对策 |
| --- | --- | --- |
| 范围蔓延 | 每看到一个开源项目就想接 | 每个 Phase 只允许引入**一个**新依赖；引入前先写"它替换掉我哪段代码" |
| 评测过拟合 | 只对 33 条案例调 prompt | 保留 20% 案例为 holdout，只在阶段末跑 |
| 安全剧场 | 加了护栏但没度量 | 每个安全改动必须附带对抗集前后对比 |
| 观测数据污染 | 真实/敏感文本进 trace | 采集端脱敏 + 默认不落原文 + 保留时长限制 |
| 依赖失控 | LiteLLM/langfuse SDK 版本漂移 | 锁定版本；观测组件故障不得影响主流程 |
| 简历叙事偏移 | 讲成"我部署了 Dify" | 所有借鉴项目在文档里标注"仅参考架构，未作为运行依赖" |

---

## 八、需要立刻做的小修正

1. **README 的来源表述**：把 `SmartMedicalConsultation`（1★）从"架构对照"改为"个人项目，仅供思路参考"，避免面试被反问。
2. **`rule_engine.py` 的定位**：在文档里明确它是"演示级规则"，Phase 5 会被分层护栏替换，而不是继续打补丁。
3. **`decision.py` 的模型输入**：现在只给 4 个字段，模型实际上看不到病历内容。这是"模型决策形同虚设"的根因，Phase 1 必须优先修。
4. **`state.py` 的 trace**：长期看会撑大 checkpoint。Phase 2 接入外部观测后，state 里的 trace 应改为只存摘要 + trace_id。
5. **依赖声明**：`pyproject.toml` 精确锁版本是优点，但新增依赖时要同步更新 `uv.lock` 并跑一次干净环境安装（你 Day 19 已经踩过这个坑）。

---

## 九、每个阶段结束时问自己三个问题

1. **这个改动，哪个指标应该变？变了吗？**（没有基线就没有优化）
2. **如果这个新组件挂了，主流程还能跑吗？**（降级是功能）
3. **我能用一句话说清"为什么不用更简单的方案"吗？**（取舍能力就是工程能力）

---

## 附：评估数据快照（2026-10-23）

| 仓库 | Stars | 最后推送 | 归档 | 许可证 |
| --- | --- | --- | --- | --- |
| langgenius/dify | 159,334 | 2026-10-23 | 否 | 自定义（含附加条款） |
| open-webui/open-webui | 156,537 | 2026-10-23 | 否 | 自定义 |
| vllm-project/vllm | 95,206 | 2026-10-23 | 否 | Apache-2.0 |
| infiniflow/ragflow | 92,718 | 2026-10-23 | 否 | Apache-2.0 |
| modelcontextprotocol/servers | 91,507 | 2026-10-22 | 否 | NOASSERTION |
| mem0ai/mem0 | 67,688 | 2026-10-22 | 否 | Apache-2.0 |
| BerriAI/litellm | 61,886 | 2026-10-23 | 否 | NOASSERTION（MIT 核心） |
| microsoft/autogen | 61,535 | 2026-10-16 | 否 | CC-BY-4.0* |
| crewAIInc/crewAI | 60,022 | 2026-10-23 | 否 | MIT |
| run-llama/llama_index | 52,635 | 2026-10-21 | 否 | MIT |
| milvus-io/milvus | 46,460 | 2026-10-23 | 否 | Apache-2.0 |
| langchain-ai/langgraph | 43,768 | 2026-10-23 | 否 | MIT |
| stanfordnlp/dspy | 38,749 | 2026-10-23 | 否 | MIT |
| microsoft/graphrag | 36,422 | 2026-10-20 | 否 | MIT |
| langfuse/langfuse | 36,245 | 2026-10-23 | 否 | MIT + EE |
| qdrant/qdrant | 35,208 | 2026-10-23 | 否 | Apache-2.0 |
| openai/openai-agents-python | 30,241 | 2026-10-22 | 否 | MIT |
| labring/FastGPT | 29,896 | 2026-10-23 | 否 | 自定义 |
| chroma-core/chroma | 29,564 | 2026-10-23 | 否 | Apache-2.0 |
| promptfoo/promptfoo | 26,329 | 2026-10-23 | 否 | MIT |
| letta-ai/letta | 25,236 | 2026-10-18 | 否 | Apache-2.0 |
| modelcontextprotocol/python-sdk | 24,652 | 2026-10-17 | 否 | MIT |
| comet-ml/opik | 22,845 | 2026-10-23 | 否 | Apache-2.0 |
| google/adk-python | ~22,000 | — | 否 | Apache-2.0 |
| pydantic/pydantic-ai | 20,809 | 2026-10-23 | 否 | MIT |
| confident-ai/deepeval | 19,058 | 2026-10-19 | 否 | Apache-2.0 |
| explodinggradients/ragas | 16,133 | **2026-02-24** | 否 | Apache-2.0 |
| 567-labs/instructor | 14,054 | 2026-10-20 | 否 | MIT |
| Arize-ai/phoenix | 11,961 | 2026-10-23 | 否 | Elastic License 2.0 |
| guardrails-ai/guardrails | 7,556 | 2026-10-21 | 否 | Apache-2.0 |
| traceloop/openllmetry | 7,502 | 2026-10-19 | 否 | Apache-2.0 |
| NVIDIA/NeMo-Guardrails | 7,353 | 2026-10-23 | 否 | Apache-2.0 |
| SCIR-HI/Huatuo-Llama-Med-Chinese | 5,001 | 2026-07-04 | 否 | Apache-2.0 |
| getzep/zep | 4,969 | 2026-10-20 | 否 | Apache-2.0 |
| meta-llama/PurpleLlama | 4,443 | 2026-10-12 | 否 | NOASSERTION |
| protectai/llm-guard | 3,217 | 2026-07-08 | **是** | MIT |
| Toyhom/Chinese-medical-dialogue-data | ~1,800 | — | 否 | MIT |
| FreedomIntelligence/HuatuoGPT-II | 419 | **2024-08-30** | 否 | 无 |
| Zz3212281607/SmartMedicalConsultation | **1** | 2026-08-24 | 否 | MIT |

\* autogen 仓库元数据返回 CC-BY-4.0，可能对应文档许可，代码部分请以仓库实际 LICENSE 为准。

> 注：star 数与推送时间为自动化抓取快照，仅供选型参考；正式采用前请再次核对目标仓库的 LICENSE 与发布说明。