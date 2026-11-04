# Phase 6：产品化（流式、异步、并发、鉴权限流、反馈闭环）

## Context

前五个 Phase 解决的是"答得对、看得见、测得准、有依据、不出界"。到 Phase 5 结束，
后端已经有 279 条测试、33 案例全过、红队与对抗集门禁，但用户体验仍是"发一条消息，
转圈等到全部结束才看到结果"。

`routes.py` 是同步阻塞的：`def` 路由跑在 FastAPI 的线程池里，`graph.invoke` 整段返回；
前端 `handleSend` 只能等 `response.json()`。模型模式下 intake 单项 p95 已到 3.3s，
首字延迟比总时长更影响体感。

Phase 6 只做产品化，不加新能力：

1. **流式**：让"正在做什么"先到前端，而不是等整轮结束。
2. **异步与并发**：把路由改为 async，暴露 SQLite 单连接在并发下的真实瓶颈，
   压测后决定连接策略。
3. **鉴权与限流**：演示作品也要有 API Key、限流和请求体上限，否则 50 并发就能打穿。
4. **反馈闭环**：把"有帮助/没帮助"变成可回放的在线信号，接进 Langfuse score。

**已完成的前置决策（Phase 4/5）：**

- 输出文案由 `result.render_text` 从结构化结果渲染，不是模型逐字生成。
- 观测是旁路，`start_trace` 用 ContextVar；节点埋点包在 `traced_node`。
- SQLite checkpointer 只需一个连接（`check_same_thread=False`），当前所有请求共享。

---

## 设计原则

1. **流的是状态，不是假 token**。系统输出不是自由生成文本，伪造逐字流会让
   "首字延迟"好看但毫无工程意义。SSE 先推进度事件，最后推完整结果；
   前端打字机是展示效果，不计入后端指标。
2. **同步接口不动**。`POST /sessions/{id}/messages` 是现有测试、评测和 CI 的契约，
   流式走新接口，前端灰度切换。
3. **断连不等于取消**。医疗场景下，客户端断开后让本轮跑完并落 checkpoint 与审计，
   避免半截状态；但要限制单会话并发，防止被当成免费算力。
4. **先测量再优化**。每一步都记录首字延迟、总时长、错误率和限流命中数，
   没有数字的优化不做。

---

## 关键设计决策

- **SSE 用 `sse-starlette`**：它已是 `mcp` 的传递依赖（3.6.0），显式加入依赖表，
  不引入新包。浏览器端用 `fetch` + `ReadableStream` 解析，因为 `EventSource`
  只支持 GET，而发消息必须带 body。
- **流式驱动用 `graph.stream(stream_mode="debug")`，不用 `astream_events`**：
  `astream_events` 会调用异步 checkpointer，而 `SqliteSaver` 不支持 async 方法；
  换 `AsyncSqliteSaver` 需要第二个连接和 `aiosqlite`，让 L1 变成 L2 的异步迁移。
  `debug` 模式同样能拿到 `task`（节点开始）与 `task_result`（节点结束）。
- **图跑在 worker 线程，事件经 `call_soon_threadsafe` 回传**：
  `asyncio.to_thread` 自动复制 ContextVar，`start_trace` 在 worker 里开、在 worker 里关，
  trace 生命周期与客户端连接解耦。客户端断开时线程继续跑完。
- **事件契约只有五种**：`meta` / `node` / `result` / `error`（外加 SSE 心跳）。
  前端只依赖这五种，后端换实现不影响 UI。
- **限流先用进程内存令牌桶**（L3）：单实例演示够用，不引入 Redis；
  文档里写明多实例时会退化成"每实例限流"。

---

## 课程与改动

### L1 流式：SSE 状态事件 + 前端流式渲染

- `pyproject.toml` 显式加入 `sse-starlette==3.6.0`。
- `backend/app/api/routes.py`
  - 抽出 `_to_response()`，同步接口与流式接口共用状态打包。
  - 新增 `POST /sessions/{session_id}/stream`，body 与 `/messages` 相同。
  - worker 线程执行 `graph.stream(..., stream_mode="debug")`：
    `task` → `node` phase=start；`task_result` → `node` phase=end（只带
    `instrument.SAFE_KEYS` 摘要）；结束后读 `get_state` 推 `result`。
  - 异常统一推 `error`，不把堆栈发给前端。
- `backend/tests/test_stream.py`：事件顺序、meta/result 字段、404/409/400、
  节点集合、结果与 GET 状态一致。
- `eval/stream_latency.py`：对运行中的服务测首事件延迟与总时长（mock/模型两种模式）。
- 前端：`streamMessage()` 解析 SSE；输入框上方显示当前节点；结果文本打字机；
  错误沿用现有"放回输入框"重试路径。
- 验收：mock 首事件 < 1.5s，模型模式 < 3s；结果与同步接口逐字段一致。

### L2 异步与并发：async 路由、异步模型客户端、SQLite 写竞争

- 路由改 `async def`，阻塞调用统一 `asyncio.to_thread`，避免堵事件循环。
- `llm/client.py` 增加异步入口（OpenAI `AsyncOpenAI`），intake/decision 走异步版本；
  同步入口保留给评测脚本。
- SQLite checkpointer 的并发写实验：50 并发下观察 `database is locked`，
  对比三种方案（单连接 + 写锁 / 每线程连接 + WAL / 换 Postgres）后选一种。
- `eval/load_test.py`：50 并发，统计 p50/p95、5xx 率、锁等待。
- 验收：50 并发无 5xx、错误率 < 1%。

### L3 鉴权、限流与资源边界

- `backend/app/api/auth.py`：`X-API-Key` 校验（演示用环境变量配置），
  未配置时本地开发放行、生产模式强制。
- `backend/app/api/ratelimit.py`：进程内令牌桶，按 IP + 会话双维度；
  超限返回 429 + `Retry-After`。
- 请求体大小上限（消息长度、字段数），超限 413。
- 流式接口额外限制"单会话同时只允许一个进行中的轮次"，重入返回 409。
- 验收：限流可复现、429 带重试时间、超长输入被拒。

### L4 反馈闭环：评分、Langfuse score、前端反馈与重试

- 新增 `feedback` 表（SQLite 独立库或同库独立表）：session_id、turn_id、
  rating、reason、trace_id、created_at。
- `POST /sessions/{session_id}/feedback`：`rating` 为 helpful / unhelpful，
  `reason` 可选枚举；同会话同轮次重复提交做覆盖。
- `observability` 增加 score 接口：有 Langfuse 时写 score，没有时落本地 JSONL。
- 前端：结果卡片下加"有帮助 / 没帮助"按钮，提交后只读；错误提示带"重试"。
- 追问按钮化：按当前追问字段生成 2–3 个可点选的回答短语（如持续时间→
  "一天内 / 两三天 / 一周以上"），点击即发送，输入框仍可自由输入。
- 验收：反馈能按 trace 回放；离线反馈文件可直接统计好评率。

---

## 验证

```bash
uv run pytest
uv run uvicorn backend.app.api.routes:app --port 8000   # 另开一个终端
uv run python eval/stream_latency.py
uv run python eval/load_test.py
```

L1 验收标准：

- 事件顺序固定为 `meta → node(start/end)* → result`，异常时以 `error` 结束。
- 流式结果与 `GET /sessions/{id}` 的字段一致。
- mock 模式首事件 < 1.5s；模型模式 < 3s（记录到 `docs/metrics-log.md`）。
- 客户端断开后，会话仍能在下一次 GET 中看到完整结果（跑完不丢）。
- 旧同步接口的所有测试保持通过。

---

## 风险与回退

- **同步/异步 checkpointer 混用**：L1 明确不引入 `AsyncSqliteSaver`；若 L2 决定迁移，
  先做双连接并发实验，再切换全部路由，避免一半同步一半异步。
- **SQLite 并发写**：当前是演示级单连接；50 并发可能锁等待。L2 必须给出实测数据，
  不达标就换 Postgres checkpointer，不在 SQLite 上堆补丁。
- **SSE 被代理缓冲**：nginx 需要 `proxy_buffering off`、`X-Accel-Buffering: no`，
  否则事件会被攒到响应结束才发出。
- **断连后台任务**：进程重启会丢进行中的轮次；演示环境接受，生产应换任务队列。
- **鉴权只做演示级**：API Key 不等于用户体系，不做注册、登录、权限模型。
