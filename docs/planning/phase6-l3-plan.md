# Phase 6 L3：鉴权、限流与资源边界

## Context

L1 让进度可见，L2 让并发可测。到 L2 结束，50 并发 mock 模式已经能做到 0 个 5xx，
但系统仍然对"谁能用、能用多快、一次能带多少"没有任何约束：

- 拿到 URL 就能调用，模型费用没有上限；
- 一个客户端可以同时占满 8 / 64 个图线程，把别人挤到队列里；
- 消息体没有长度限制，超长文本或大 JSON 直接进内存；
- 同一个会话可以并发提交两条消息，checkpoint 互相覆盖；
- SSE 可以无限开连接，断线后后台任务仍继续跑，资源会累积。

L3 在"请求进入图"之前加四道闸门：

```
体积  →  身份  →  频率  →  会话在途  →  图线程池
```

目标不是做一个完整的账号系统，而是让边界**明确、可配置、可测试**。

---

## 设计原则

1. **先便宜后昂贵**：体积检查不解析 JSON，认证只做哈希比较，限流只读内存，
   最后才进线程池。便宜的检查放在前面，攻击者不能靠大 body 拖垮解析器。
2. **拒绝要可解释**：401 未认证、403 无权限、413 体积过大、429 频率超限、409 会话在途。
   每个响应都能让客户端知道下一步该做什么；429 必须带 `Retry-After`。
3. **默认开发可用、部署必须收紧**：`AUTH_MODE=off` 只用于本地和公开演示；
   生产部署文档要求 `required`，并在 compose / 启动脚本里显式写明。
4. **所有状态有上限**：令牌桶数量、在途会话、SSE 连接、消息长度、会话轮次。
   没有上限的内存结构，迟早会变成内存泄漏。
5. **单实例先做对**：限流和会话锁都在进程内。多实例会退化成"每实例限流"，
   文档里写清楚，不假装它是全局的。

---

## 关键设计决策

### 1. API Key 放在 header，不进前端

- 头：`X-API-Key`。
- 配置：`API_KEYS=key1,key2`，逗号分隔，支持轮换。
- 启动时只保存每个 key 的 SHA-256 摘要；比较用 `secrets.compare_digest`，
  避免普通 `==` 的时序泄漏。
- 日志和审计只记 key 的短哈希（前 8 位），绝不落原文。

**为什么浏览器端不携带 key**：前端代码是公开的，任何写进 JS 的 key 都等于公开。
所以：

- 公开演示（浏览器）走 `AUTH_MODE=off`，靠 IP 限流；
- 机器客户端（curl、评测脚本、未来的集成方）用 `AUTH_MODE=required` + API Key。

这不是"偷懒不做前端认证"，而是承认 API Key 不是用户体系：它认证的是**调用方程序**，
不是终端用户。真正的登录、会话、权限模型不在 L3 范围内。

### 2. 认证用 FastAPI dependency，不用全局中间件

```python
@app.post("/sessions", dependencies=[Depends(require_api_key)])
```

理由：

- `/docs`、`/openapi.json`、健康检查不需要 key；
- SSE 路由也能正常走依赖，不会和流式响应打架；
- 语义显式：看路由就知道它受保护。

### 3. 限流用令牌桶，不用固定窗口

固定窗口的问题是窗口边界会放行两倍突发（59 秒和 61 秒各打满一次）。
令牌桶更自然：

- `capacity`：桶容量，决定允许多大的突发；
- `refill_rate`：每秒补充多少令牌，决定长期速率；
- 每次请求消耗 1 个；不足时计算还需要等多久。

```python
tokens = min(capacity, tokens + elapsed * refill_rate)
if tokens < 1:
    retry_after = (1 - tokens) / refill_rate
```

时间用 `time.monotonic()`，不受系统改时间影响。

### 4. 三个维度，而不是一个

| 维度 | 键 | 作用 |
|---|---|---|
| 全局 | `"global"` | 保护进程和 provider 配额 |
| 调用方 | API Key 摘要，没有则 IP | 防止单个客户端刷爆 |
| 会话 | `session_id` | 防止一个会话被反复追问/重放 |

顺序：全局 → 调用方 → 会话。任何一层超限都返回 429，`Retry-After` 取最长等待。

**为什么要会话维度**：同一个会话的请求会读写同一份 checkpoint，天然应该串行；
频率限制再叠一层，防止脚本对同一会话高频重放。

### 5. 会话在途锁：同一会话一次只跑一轮

在 API 层维护一个进程内集合：

```python
_inflight_sessions: set[str] = set()
```

- 请求进入时 `if session_id in _inflight_sessions: 409`；
- 否则加入，`finally` 移除；
- 不同 session 互不影响，不同 session 仍可并行。

为什么不是 `asyncio.Lock` 字典：锁需要管理生命周期和清理；集合更简单，
而且事件循环是单线程的，检查 + 加入之间没有 `await`，不存在竞态。

**边界**：多 uvicorn worker 或多副本时，每个进程各有一份集合，
同一会话仍可能在不同进程并行。真正全局的锁需要 Redis/Postgres  advisory lock，
L3 不做，文档写明。

### 6. 体积边界分两层

- **中间件**：请求体超过 `MAX_BODY_BYTES`（默认 32KB）直接 413，不解析 JSON；
- **Pydantic**：`message` 字段 `max_length=2000`，作为第二层业务校验（422）。

为什么两层：中间件防大 body 占内存，Pydantic 防止合法大小内的超长文本进入图。
已知局限：chunked 传输且没有 `Content-Length` 时，中间件只能边读边计数；
如果要做严格限制，需要包一层 ASGI `receive`。L3 先做 `Content-Length` + 读取计数，
在文档里标注。

### 7. SSE 连接也要计数

流式接口的限制不是"每个事件限流"，而是：

- 单调用方同时打开的流数量上限（`MAX_STREAMS_PER_CLIENT`，默认 5）；
- 同一会话只允许一个在途轮次（409）；
- 断线后后台任务继续跑完，但**计入在途**，直到任务结束。

否则一个客户端可以开几百条流，每条断线后还留一个后台线程。

---

## 课程与改动

### L3.1 API Key 认证

- 新增 `backend/app/api/auth.py`
  - `AuthSettings`：`AUTH_MODE`、`API_KEYS`；
  - `require_api_key`：`off` 直接放行，`required` 校验 header；
  - key 摘要、`compare_digest`、审计短哈希。
- `backend/app/config.py` 增加 `AUTH_MODE`、`API_KEYS`。
- 测试：缺失 401、错误 401、正确 200、`off` 放行、日志不含原文、
  相同前缀不会误判。

### L3.2 令牌桶与限流

- 新增 `backend/app/api/ratelimit.py`
  - `TokenBucket`：`capacity`、`refill_rate`、`try_consume()`、`retry_after()`；
  - `RateLimiter`：按 key 管理桶，定期清理空闲桶；
  - `RateLimitDecision(allowed, remaining, retry_after)`。
- 依赖注入到写接口与流式接口：`Depends(rate_limit)`。
- 429 响应头：`Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`。
- 配置：`RATE_LIMIT_PER_MINUTE`、`RATE_BURST`、`SESSION_RATE_LIMIT_PER_MINUTE`。
- 测试：用可注入的假时钟验证补充、突发、等待时间、桶清理。

### L3.3 资源边界与会话锁

- `backend/app/api/limits.py`
  - `BodySizeLimitMiddleware`：`Content-Length` 超限 413；无长度时边读边计数；
  - `MAX_MESSAGE_CHARS`：Pydantic `max_length` 兜底；
  - 会话轮次上限（复用现有 followup / step 上限，不重复造）。
- `backend/app/api/inflight.py`
  - `try_enter(session_id)` / `leave(session_id)`；
  - 流式请求直到后台任务结束才释放。
- SSE 连接计数：按调用方记录，超限 429。
- 测试：413、422、409、429、不同会话并行、同会话串行。

### L3.4 接线、观测与压测

- 路由顺序：`体积中间件 → 认证依赖 → 限流依赖 → 会话在途 → _run_sync`。
- 新增 span：`api.auth`、`api.rate_limit`、`api.session_lock`；
  属性只记结果、原因、短哈希，不记原始 key / IP。
- 审计：拒绝事件写 `logs/audit.jsonl`（复用 Phase 5 的 `AuditEvent`）。
- `eval/load_test.py` 支持 `--api-key`，并统计 401/413/429/409 分布。
- 记录一组对照：无限流 vs 有限流，50 并发下的 5xx、429、p95。

---

## 验证

```bash
uv run pytest
uv run uvicorn backend.app.api.routes:app --port 8000
uv run python eval/load_test.py --concurrency 50 --turns 4 --api-key test-key
```

验收标准：

- 缺失/错误 key → 401；正确 key → 200；`AUTH_MODE=off` → 放行。
- 超过桶容量 → 429，带 `Retry-After`；等待后恢复 200。
- 消息超过 2000 字 → 422；请求体超过 32KB → 413。
- 同一会话并发两条消息 → 一条 200、一条 409；不同会话并发 → 都 200。
- 50 并发下 5xx = 0；429 数量符合配置；评测脚本带 key 时全部通过。
- 审计日志能按时间回放拒绝事件，且不含原始 key。

---

## 风险与回退

- **进程内限流不是全局限流**：多 worker / 多副本时每个进程各算各的。
  需要全局配额时换 Redis 令牌桶；L3 不引入 Redis，文档写明。
- **公开演示默认开放**：`AUTH_MODE=off` 是为了让浏览器前端可用；
  公开部署必须靠 IP 限流兜底，并在 README 里标注。
- **API Key 不是用户体系**：不做注册、登录、权限；它只回答"这个调用方程序是谁"。
- **chunked body 的体积限制**：无 `Content-Length` 时只能读取计数；
  如果要做严格防护，需要 ASGI `receive` 包装，L3 标注为后续项。
- **429 误伤长对话**：令牌桶按请求数计，不按 token 数；长对话用户可能被限。
  用突发容量 + 会话维度缓解，真实配额应按模型 token 计费另做。
- **会话锁的进程边界**：多进程时同一会话仍可能并行；需要跨进程锁时再升级。
