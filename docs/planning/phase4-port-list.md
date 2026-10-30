# Phase 4 移植清单（来自 rag-knowledge 项目）

来源：`/Users/meijinchao/Documents/Codex/2026-09-19/v/rag-knowledge`（Java / Spring Boot / pgvector）
目标：`backend/app/knowledge/`（Python / LangGraph）

语言不同，移植的是**算法、SQL 结构与设计**，不是代码。

---

## 一、第一阶段要做（D2–D3）

### 1. 双路检索：向量 + 关键词
- 来源：`rag/repository/RagRetrievalRepository.java` 的 `SEARCH_SQL` / `KEYWORD_SEARCH_SQL`
- **向量路**：余弦距离 `<=>`、`similarity = 1 - distance`、受阈值约束
- **关键词路**：ILIKE 命中计数；**不用 `ts_rank_cd`**（中文分词差，原项目也是靠 LIKE + bigram 兜的）
- 参数沿用：`similarityThreshold = 0.35`、`candidateMultiplier = 3`

### 2. 融合规则（加权，不做 RRF）
- 两路各取 `topK × 3` 候选，按 `chunk_id` 去重
- **关键词路不受 similarity 阈值约束**——否则救不回"关键词命中但向量分低"的条目
- 最终分 = `similarity + λ × keyword_score`，`λ = 0.1` 可配
- `keyword_score` = 命中关键词数 / 查询关键词数，封顶 1.0
- 关键词路进来的条目标记 `matched_by = "keyword"`，写进 trace

理由：只有一个可解释的参数 λ。关键词全命中等于"相似度加 0.1"，够用且说得清。取 0.1 而非 0.2：库小、向量召回本就不差，权重太高会让"胸闷"压过语义更近但用词不同的条目；D4 拿到 recall@5 再决定是否上调。

### 3. 中文关键词提取
- 来源：`rag/service/SearchKeywordExtractor.java`
- 内容：中文短语 → 二元组展开 → 停用词 → 最长优先、上限 12
- 医疗术语（胸痛、呼吸困难）靠这一路保证不漏

### 4. scope 过滤
- 来源：`rag/dto/RetrievalFilters.java` + `filterSql()`
- 移植：动态 WHERE + 参数化（防注入）的写法
- 字段替换：`documentIds/startDate/endDate` → `category / risk_level / symptom_cluster / age_min / age_max`
- 新增规则：用户字段未知 → 该维度不拼进 WHERE

### 5. 溯源落库
- 来源：`chat/entity/MessageReference.java`
- 移植：每条结果存 `source / chunk_id / similarity / matched_by`
- 落地：写进 `tool_results`，并由 trace span 记录

### 6. Embedding 接入
- 来源：`rag/model/QwenEmbeddingService.java`
- 配置照搬：qwen3.7-text-embedding-flash、1024 维、batch 20、concurrency 3
- 接入点：复用现有 `backend/app/llm/client.py`（DashScope 兼容端点），新增 embedding 通道
- 保留 mock 实现，保证离线测试不依赖网络

### 7. 输出约束措辞
- 来源：`rag/prompt/PromptBuilder.java` 的 `STRICT_KB`
- 规则："只能根据资料回答 / 资料不足必须说无法回答 / 不编造"
- 用在 `guideline_retrieve` 的结果约束与 judge rubric

---

## 二、不移植

- **RRF 融合**（本人未实现，不做；改用加权融合）
- Spring / JPA / JWT / Redis / 前端
- 文档解析（PDFBox / POI）——除非要导入 PDF 版指南
- 会话、限流、缓存等平台层

---

## 三、基础设施注意

pgvector 需要一个 Postgres 实例。现有 `docker-compose.observability.yml` 里的 PG 是 Langfuse 专用，**不要混用**；建议新增独立的 `postgres + pgvector` 服务，端口避开主栈与 Langfuse。

---

## 四、对应到 Phase 4 日程

| 天 | 用到哪几项 |
|---|---|
| D1 知识库条目 | 无（新写） |
| D2 索引与检索 | 1、2、3、6 |
| D3 工具与契约 | 4、5、7 |
| D4 评测 | 全部（recall@5 验证双路是否真的带来增益） |
| D5 MCP + 回归 | — |