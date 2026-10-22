# 智能问诊 Agent 新项目交接说明

> 更新于 2026-10-08。此文件用于把当时的讨论转入一个**独立于 RAG 项目**的新工作区或新对话。完整日程见同目录的 [20 天开发计划](20-day-plan.md)。

## 1. 已确定的决策

- **目标**：从零开发一个面向求职展示的“智能问诊与症状分析 Agent”，开发周期 20 天，按每天约 3～4 小时规划。
- **学习起点**：用户已有一个 RAG 项目，但几乎没有 Agent 知识。阶段 A 先学基本概念、手写最小 Loop，再接模型 API；[SmartMedicalConsultation](https://github.com/Zz3212281607/SmartMedicalConsultation) 仅作可选的后期对照，不要求克隆或复现。当前目录就是独立的新项目目录。
- **学习路线**：使用 [AgentGuide](https://github.com/adongwanai/AgentGuide) 的入门、最小 Loop、工具、评测与项目交付内容。优先学状态、动作选择、工具、循环、终止、轨迹和评测。
- **技术方向**：Python 3.11、LangGraph、Pydantic 2、FastAPI、SQLite、React + Vite + JavaScript、单一模型 API 的薄适配层；pytest 与自写 JSONL 评测脚本。完整选型和学习顺序见计划文档“1.4 技术栈与选型说明”。Day 4 在新仓库核实兼容性并固定依赖版本。
- **作品核心**：多轮动态追问；基于状态选择 `ASK_FOLLOWUP`、`CALL_TOOL`、`FINALIZE`；受限工具调用；独立风险门控；会话恢复；至少 30 条模拟案例与可复核评测报告。
- **范围边界**：演示使用虚构病例。不给确定诊断、处方、剂量或停药建议；不把项目作为真实医疗服务。暂不做 Neo4j、GraphRAG、微调、九 Agent、语音、图片和真实患者上线。
- **交付**：独立仓库、可运行网页、README、架构图、评测报告、演示视频和面试讲稿。20 天完成作品开发，不把 20 天等同于拿到 offer。

### 技术栈落地顺序

| 时间 | 先做什么 | 对应技术 |
|---|---|---|
| Day 1 | 学概念，手工推演状态与动作 | AgentGuide、Markdown；无需 API Key |
| Day 2～3 | 用预设决策手写 Loop，再接单一模型 API | Python 3.11、`venv`、模型 API、JSONL trace |
| Day 4 | 建立独立 Git 仓库、规格和评测草案，固定兼容依赖 | Git、Pydantic 2、Markdown、依赖锁定 |
| Day 5～12 | 完成状态、追问、工具、风险门控与结果 | LangGraph、Pydantic 2、Python 工具函数、pytest |
| Day 13～15 | 会话、API 与网页 | SQLite checkpointer、FastAPI、React + Vite |
| Day 16～20 | 评测与求职交付 | JSONL 案例和 trace、评测脚本、README、演示 |

新项目先用一家模型供应商；通过配置和薄适配层保留以后更换的能力。后端锁定已验证的依赖版本，前端提交 `package-lock.json`，密钥与会话数据不进 Git。Neo4j、GraphRAG、微调和九 Agent 不属于当前 20 天的交付门槛。

## 2. 可选参考仓库的静态审查记录

以下记录只供将来对照，阶段 A 不依赖运行该仓库。

1. [QUICKSTART.md](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/QUICKSTART.md)提供 Lite 模式；用户在 macOS 上应使用 `cp .env.example .env`，而不是文档中的 Windows `copy`。
2. [聊天接口源码](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/backend/api/chat.py)注册 **POST `/chat`**，由应用前缀形成 POST `/api/chat`。Quickstart 中普通 `curl` 示例会发 GET，可能遇到 405；优先用 Swagger `/docs` 验证。
3. [图路由](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/backend/agents/graph.py)是“意图 → 单一路径 → 安全 → 融合”的固定流程；九个命名节点不等于九个自主协作的 Agent。
4. [历史存储](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/backend/api/chat.py)在回答后写入，但请求初始化只传本轮 `query` 与 `session_id`，没有把历史取回用于下一轮决策。
5. [流式接口](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/backend/api/chat.py)发送 `on_chat_model_stream` 的模型中间内容，未限定为最终经过安全融合的答案；新项目先返回最终校验后的完整回答，再考虑流式。
6. [安全规则](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/backend/safety/rule_engine.py)主要依赖输入关键词和提示文本，`answer` 参数未用于检查，不能把这套规则视为临床级保护。
7. [公开金标数据](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/evaluation/golden_dataset.json)是 7 条；[RAGAS 与 LLM Judge 指标](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/evaluation/metrics.py)仍为占位实现。不要直接引用 README 宣传的 53 条或评测成绩。
8. 仓库的 [LICENSE](https://github.com/Zz3212281607/SmartMedicalConsultation/blob/main/LICENSE)为 MIT。自研方案优先以思路学习与独立实现；若实际复制实质性代码，保留原版权与许可文本。

以上是 **2026-10-08 的静态源码审查**，参考仓库尚未在当前机器运行；源码或依赖若有变化，以新检查为准。

## 3. 新项目开始时的第一步

1. 在新项目中读取完整计划文档：`docs/planning/20-day-plan.md`。
2. 确认新工作目录是独立目录；不要在 `/Users/meijinchao/.codex/worktrees/fac6/rag-knowledge` 中创建问诊项目文件。
3. 从 **Day 1** 开始：阅读 AgentGuide 基础章节，在 `learning/agent-basics.md` 写一页概念笔记和两条虚构病例的手工状态轨迹；无需克隆参考仓库或配置 API Key。
4. 按日程执行；每一天先解释当天要学的 Agent 概念，再动手实现，最后用当天验收标准检查。用户希望自己理解并从零开发，不希望只得到大段自动生成代码。
5. Day 2 再准备 Python 3.11 和 `venv`；若 Day 3 的模型 API 不可用，用预设决策器继续验证 Loop，记录外部阻塞。

## 4. 可以直接粘贴到新项目对话的交接提示词

```text
我要在这个全新的项目目录，从零开发“智能问诊与症状分析 Agent”求职作品。请先读取这两个交接文件：

1. docs/planning/20-day-plan.md
2. docs/planning/project-handoff.md

背景：我已有独立 RAG 项目，但 Agent 近乎零基础。开发周期是 20 天，每天约 3～4 小时。阶段 A 先学 Agent 概念、手写最小 Loop、再接模型 API；SmartMedicalConsultation 仅作为可选的后期对照，不克隆复现。AgentGuide 提供学习与评测路线。自研技术栈按计划文档 1.4 节执行：Python 3.11、LangGraph、Pydantic 2、FastAPI、SQLite、React + Vite + JavaScript、单一模型 API 适配层，测试用 pytest 和自写评测脚本；Day 4 固定兼容的依赖版本。请从 Day 1 开始，按“概念解释 → 我动手实现 → 验收和复盘”的节奏协作，不要直接把参考仓库代码复制成我的作品。项目只用模拟病例做求职演示，不提供真实医疗诊断或用药指导。请先检查本项目目录状态，再给我 Day 1 的具体操作，并在我运行时协助排错。
```

## 5. 后续更新规则

- 每完成一天，在新项目 `docs/progress.md` 中记录日期、完成项、验收证据、阻塞和次日任务。不要只标记“已学习”。
- 技术选型变化、范围变化或测试发现的问题，以新仓库内的 `docs/spec.md` 和 `docs/eval-report.md` 为准，并同步更新计划中的实际进度。
- 求职叙述只使用自己项目真实运行与评测得到的数据；参考仓库的数字不能当作自己的结果。
