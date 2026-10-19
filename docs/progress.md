# 开发进度

> 项目只使用虚构模拟病例；这里记录实际完成项与验收证据，不把演示结果写成医疗能力指标。

## Day 1｜2026-10-08

- **完成项**：理解目标、状态、动作、观察和终止；区分 Chatbot、固定 Workflow 与状态驱动 Agent；手工推演信息不足和信息足够两条模拟轨迹。
- **产物**：`learning/agent-basics.md`，含流程图、状态／历史／轨迹对照和两条轨迹。
- **验收证据**：能说明“回答不知道”应记为已问、追问达到上限后停止普通追问，以及风险检查在动作选择前执行。
- **阻塞**：无。

## Day 2｜2026-10-09

- **完成项**：使用 Python 3.11.17 虚拟环境；实现预设决策器、两个本地字符串工具、工具白名单、最多 5 步的循环、状态写回、轨迹记录和可读失败退路。
- **产物**：`learning/minimal_loop.py`。这一版用预设规则模拟决策，还没有接模型 API。
- **验收证据**：
  - 正常路径：自动循环由助手在项目虚拟环境运行，执行 `to_upper → count_words → FINALIZE`，结果为 `HELLO AGENT WORLD（3 个单词）`；用户此前已在本机运行成功手动写回状态的第一版。
  - 未知工具：返回 `未知工具：missing`。
  - 工具异常：返回包含 `执行失败` 的可读结果并记录 `tool_error`。
  - 步数上限：重复调用工具时，在第 5 步后返回 `达到最大步骤数 5，任务已停止。`。
- **阻塞**：无。
- **下一步**：Day 3 为决策动作增加结构校验和 JSONL 轨迹，并接入单一模型 API；模型配置不可用时继续用预设决策器验证循环。

## Day 3｜2026-10-09

- **已完成**：JSON 动作解析与白名单、参数校验；把 JSON 文本决策接入最小循环；百炼北京地域的单一 HTTP 适配层；请求超时、模型错误退路与 JSONL 轨迹。练习内容仍为英文字符串，不涉及病例。
- **验收证据**：四个动作校验示例符合预期；模拟 HTTP 响应驱动循环完成 `CALL_TOOL → CALL_TOOL → FINALIZE`，结果为 `HELLO AGENT WORLD（3 个单词）`；当前 Codex 进程没有密钥时返回可读错误并留下轨迹。
- **真实调用验收**：已用项目 `.env` 中的北京地域 Key，在允许联网的执行环境完成 `CALL_TOOL → CALL_TOOL → FINALIZE`；最终得到 `HELLO AGENT WORLD（3 个单词）`。受限环境的连接失败也按预期写入 JSONL 轨迹。
- **学习验收**：用户已在自己的终端运行；五个固定任务完成，其中一次最终文案空格偏差已通过确定性渲染修复并真实复测。四条失败路径检查通过。
- **环境说明**：当前执行环境无法解析 PyPI 域名，因此本练习用 Python 标准库请求 API，不依赖未安装的第三方 SDK。密钥从本地 `.env` 或 `DASHSCOPE_API_KEY` 环境变量读取。
- **本地配置**：已增加忽略提交的 `.env` 文件与自动加载器；终端环境变量优先于 `.env`。用户已在本机填写密钥，进度文档不记录其内容。
- **验收补充**：用户本机三步调用成功；五个固定任务首次真实运行的严格文本匹配为 4/5，模型将 `2 个单词` 写成 `2个单词`。已增加基于工具结果的确定性最终渲染，偏差案例真实复测通过。本地未知工具、无效参数、步骤上限、工具实际超时均通过。详见 `docs/day3-retrospective.md`。

## Day 4｜2026-10-12

- **完成项**：编写产品规格与首批 10 条模拟案例预期；建立产品目录骨架，练习代码继续留在 `learning/`；固定后端直接依赖版本并生成 `uv.lock`。
- **验收证据**：`uv sync --locked` 安装 51 个包；Python 3.11.17 成功导入 `StateGraph`、`SqliteSaver`、`FastAPI`、`BaseModel`；`.env`、`.venv`、轨迹和 SQLite 文件经 `git check-ignore` 确认忽略。
- **边界**：目前仅完成设计和依赖验证，尚无产品 Agent 或真实病例评测；E01–E10 的结果将在后续实现后运行记录。

## Day 5｜2026-10-15

- **完成项**：定义 `ConsultationState`（Pydantic 模型，区分追加合并与覆盖字段）；搭建 LangGraph 状态图骨架，含入口、风险检查、决策、追问、工具、输出六个节点和两个条件边；用 mock 决策器跑通普通路径与风险路径。
- **产物**：`backend/app/agent/state.py`、`backend/app/agent/graph.py`、`backend/tests/test_graph.py`、`run.py`。
- **验收证据**：
  - 普通路径轨迹为 `intake → risk_check → decision → tool → decision → output`，最终输出"建议方向：内科方向（模拟）"。
  - 风险路径轨迹为 `intake → risk_check → output`，不经过 decision，最终输出固定风险提示。
  - `pytest backend/tests/test_graph.py` 2 passed。
- **mock 说明**：决策器当前用固定规则（检查 `tool_results` 是否已有 `department_suggest`）；追问节点为占位；工具节点返回模拟结果。Day 7-10 将逐步替换为真实追问生成和模型动作选择。
- **下一步**：Day 6 实现结构化信息提取，从用户输入中提取必要字段并区分已知、缺失与"不知道"。

## Day 6｜2026-10-16

- **完成项**：实现 `intake.py` 结构化信息提取——定义 6 个必要字段（主要症状、持续时间、部位、程度、伴随表现、已知背景），用关键词映射提取已知字段，多轮信息以列表追加合并（去重），每轮重新计算 `missing_fields`；将 intake 节点接入 graph 替换 Day 5 占位版本。
- **产物**：`backend/app/agent/intake.py`、`backend/tests/test_intake.py`。
- **验收证据**：`pytest backend/tests/` 10 passed，覆盖完整、缺失、模糊、否定、空输入、多轮追加、去重 8 类场景。
  - 场景 B（信息足够）：提取 5 字段，仅缺已知背景。
  - 场景 A（信息不足）：提取 2 字段，4 字段列为缺失。
  - 模糊表述"最近几天觉得不太舒服"：主要症状、程度、部位均未命中。
  - 否定句"暂无其他不适"：伴随表现提取为"暂无"，不当缺失。
- **局限**：关键词提取有固有局限——"头部不适"中的"头部"被同时匹配为部位；模糊时间"几天"被匹配为持续时间。后续可引入模型提取优化，当前规则版本满足骨架验证。
- **下一步**：Day 7 实现动态追问生成，基于 `missing_fields` 和 `asked_questions` 决定追问什么。

## Day 7｜2026-10-16

- **完成项**：实现 `followup.py` 动态追问——定义字段优先级表和问题模板；`select_next_field` 从缺失字段中筛掉已问过的、按优先级排序、取最高；`generate_question` 用模板生成简短问题；`followup` 节点更新 `asked_questions` 和 `followup_count`。将 followup 节点接入 graph 替换 Day 5 占位，mock 决策器增加"有缺失且未到上限 → ASK_FOLLOWUP"逻辑。
- **产物**：`backend/app/agent/followup.py`、`backend/tests/test_followup.py`、更新 `graph.py` 决策器和测试。
- **验收证据**：`pytest backend/tests/` 17 passed。
  - 追问路径轨迹：`intake → decision(ASK_FOLLOWUP) → followup(持续时间) → intake → decision → followup(伴随表现) → intake → decision → followup(程度) → intake → decision(CALL_TOOL) → tool → decision(FINALIZE) → output`。
  - 三次追问按优先级依次问了持续时间(2)、伴随表现(4)、程度(5)，三次到上限后不再追问，直接调工具结束。
  - 去重验证：三个问题各不相同。
  - 已问过字段跳过：`select_next_field` 在问过持续时间后选伴随表现，不重复问持续时间。
- **局限**：mock 决策器只要 `has_missing` 就追问，导致只缺低优先级字段（如已知背景）时也会追问三次。Day 8 将完善停止条件——低优先级字段不值得追问、到上限后带未知收束。
- **下一步**：Day 8 完善追问停止条件与恢复。

## Day 8｜2026-10-16

- **完成项**：修复 `missing_fields` 追加合并 bug（state 改为覆盖）；完善 decision 停止条件——`select_next_field` 返回 None（无值得问的字段）时不再追问，即使还没到上限也直接调工具。
- **产物**：更新 `state.py`（missing_fields 合并方式）、`graph.py`（decision 逻辑）、`test_graph.py`（4 个新测试）。
- **验收证据**：`pytest backend/tests/` 20 passed。
  - 修复前：只缺已知背景时白问 3 次，第 2、3 次 followup 选 None 仍继续追问。
  - 修复后：只缺已知背景时只问 1 次，问过后 `select_next_field` 返回 None，决策器选 CALL_TOOL。
  - `missing_fields` 不再重复累加，每轮都是干净的 4 元素列表。
  - 四种不死循环场景通过：正常结束、只缺低优先级、空输入、风险输入。
- **下一步**：Day 9 工具设计与注册——实现 `department_suggest` 与 `visit_preparation` 两个受限工具。

## Day 9｜2026-10-16

- **完成项**：实现 `backend/app/tools/registry.py`——`department_suggest`（症状关键词→科室映射，无匹配返回"暂无法判断"不编造）、`visit_preparation`（从 collected_facts 生成就诊信息清单，无信息返回通用清单）、`TOOL_REGISTRY` 统一注册表。将 graph.py 的 tool 节点从写死"内科方向（模拟）"改为调用真实工具；output 节点改为读结构化结果渲染。
- **产物**：`backend/app/tools/registry.py`、`backend/tests/test_tools.py`、更新 `graph.py` 和 `test_graph.py`。
- **验收证据**：`pytest backend/tests/` 27 passed。
  - `department_suggest`：头部不适→神经内科，腹痛→消化内科，无匹配→department=None + reason。
  - `visit_preparation`：有事实时清单包含主要不适/持续时间/部位等条目，空参数返回通用清单。
  - 工具返回结构化字典而非字符串，output 节点按 schema 读取。
- **下一步**：Day 10 让 Agent 自主选择动作——替换 mock 决策器为模型决策，在 ASK_FOLLOWUP / CALL_TOOL / FINALIZE 中选择。

## Day 10｜2026-10-19

- **完成项**：实现 `backend/app/agent/decision.py`——模型决策器（`model_choose` 调百炼返回 JSON）、预设决策器（`mock_choose` 规则兜底）、`parse_action` 解析校验动作名、`validate_action` 校验模型选的动作是否合法并兜底。更新 graph.py decision 节点：模型选的动作记入 trace（`model_choice`），程序校验后的最终动作也记入（`final_action`），两者可能不同。用 `USE_MODEL=1` 环境变量切换模型/预设模式。
- **产物**：`backend/app/agent/decision.py`、`backend/tests/test_decision.py`、更新 `graph.py`。
- **验收证据**：`pytest backend/tests/` 35 passed。
  - 模型选 ASK_FOLLOWUP 但没有可问的字段 → 程序兜底为 CALL_TOOL。
  - 模型选 CALL_TOOL 但工具已调过 → 程序兜底为 FINALIZE（不重复调）。
  - 模型选的动作合法 → 程序放行。
  - 模型返回未知动作或非法 JSON → 抛 DecisionError。
  - 模型不可用时自动回退预设决策器。
- **里程碑意义**：这是第一个重要里程碑——Agent 现在可以自主选择动作。模型负责在 ASK_FOLLOWUP / CALL_TOOL / FINALIZE 中"提"，程序负责"判"（校验和兜底）。风险门控先于 decision，模型碰不到。trace 同时记录模型选择和最终执行，可从轨迹解释每步决策依据。
- **下一步**：Day 11 风险门控与输出边界——完善风险检查规则，处理否定句、含糊表述和跨轮补充。

## Day 11｜2026-10-19

- **完成项**：实现 `backend/app/safety/rule_engine.py`——`screen` 三级检查（风险词命中→risk、含糊表述→vague、安全→safe）；`check_negation` 检查风险词前面 5 字内有无否定词，否定句不误判；`final_check` 在输出前检查风险词和越界内容（确诊/处方/剂量/停药）。更新 graph.py：risk_check 节点调 `screen`，output 节点调 `final_check`，含糊走保守提示分支。
- **产物**：`backend/app/safety/rule_engine.py`、`backend/tests/test_safety.py`、更新 `graph.py` 和 `test_graph.py`。
- **验收证据**：`pytest backend/tests/` 46 passed。
  - 否定句"没有呼吸困难"→ safe，走正常路径调工具，不误分流。
  - 含糊"好像有点喘"→ vague（V001），走输出提示"寻求专业帮助"。
  - 肯定"突然呼吸困难"→ risk（R001），走固定提示。
  - 最终输出含"确诊"/"处方"/"剂量"/"停药"→ F002 拦截，替换为边界提示。
  - 最终输出含风险词→ F001 拦截。
- **局限**：否定检测只看风险词前 5 字，复杂长句可能漏判；含糊词表有限，不能覆盖所有表述。spec 要求"不声称临床可靠"，这些规则只展示软件如何处理预设输入。
- **下一步**：Day 12 结构化结果与端到端检查——统一最终结果格式，渲染前做 schema 与边界校验。
