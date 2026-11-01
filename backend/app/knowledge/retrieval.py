"""双路检索与加权融合。

向量路管语义相近，关键词路管术语精确命中；两条路各自取候选后按
`similarity + λ × keyword_score` 合并，不做 RRF。
"""

from dataclasses import dataclass

from backend.app.knowledge.db import connect, table_name, to_vector_literal
from backend.app.knowledge.keywords import extract_keywords, keyword_score
from backend.app.llm.embeddings import embed_texts

DEFAULT_TOP_K = 5
# 按 text-embedding-v3 实测分布定的，不是照搬别的项目：
# 相关问句 top-1 落在 0.635–0.851，闲聊问句落在 0.370–0.482。
# 取 0.6 而不是取中间的 0.55："说不上来哪里不舒服"这类含糊说法能到 0.569，
# 按"宁可说暂无依据"的原则应该给出 None。样本少，D4 拿到标注集后要重定。
DEFAULT_THRESHOLD = 0.6
DEFAULT_LAMBDA = 0.1
CANDIDATE_MULTIPLIER = 3

_COLUMNS = "id, title, content, source_id, department, scope_text, version"


@dataclass(frozen=True)
class Hit:
    """一条检索结果，带上它是怎么被找出来的。"""

    id: str
    title: str
    content: str
    source_id: str
    department: str | None
    scope_text: str
    version: str
    similarity: float
    keyword_score: float
    score: float
    matched_by: str  # vector / keyword / both


def _filter_sql(category: str | None, risk_level: str | None) -> tuple[str, list]:
    """拼过滤条件。只拼固定片段，用户值一律走参数，避免注入。"""
    clauses: list[str] = []
    params: list = []
    if category:
        clauses.append("category = %s")
        params.append(category)
    if risk_level:
        clauses.append("scope_payload->>'risk_level' = %s")
        params.append(risk_level)
    if not clauses:
        return "", params
    return " AND " + " AND ".join(clauses), params


def vector_search(
    conn,
    vector_literal: str,
    *,
    limit: int,
    threshold: float = DEFAULT_THRESHOLD,
    category: str | None = None,
    risk_level: str | None = None,
) -> list[dict]:
    """按余弦相似度取候选，低于阈值的直接丢掉。"""
    where, filter_params = _filter_sql(category, risk_level)
    sql = f"""
        SELECT {_COLUMNS}, 1 - (embedding <=> %s::vector) AS similarity
        FROM {table_name()}
        WHERE 1 - (embedding <=> %s::vector) >= %s
        {where}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    params = [vector_literal, vector_literal, threshold, *filter_params, vector_literal, limit]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        names = [description.name for description in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def keyword_search(
    conn,
    vector_literal: str,
    keywords: list[str],
    *,
    limit: int,
    category: str | None = None,
    risk_level: str | None = None,
) -> list[dict]:
    """按 ILIKE 命中取候选。这里**不过相似度阈值**。

    要救的正是"关键词命中、但向量分低"的条目，加阈值就等于把这条路废掉。
    similarity 仍然算出来，作为合并时的基础分。
    """
    if not keywords:
        return []

    where, filter_params = _filter_sql(category, risk_level)
    # 正文和 symptom_cluster 一起匹配：后者是条目的同义词表，
    # 用户说"挂号"、正文写"就诊前准备"时，靠它才捞得到。
    like_clause = " OR ".join(
        ["content ILIKE %s OR scope_payload->>'symptom_cluster' ILIKE %s"] * len(keywords)
    )
    sql = f"""
        SELECT {_COLUMNS}, 1 - (embedding <=> %s::vector) AS similarity
        FROM {table_name()}
        WHERE ({like_clause})
        {where}
        ORDER BY similarity DESC
        LIMIT %s
    """
    like_params: list[str] = []
    for keyword in keywords:
        like_params.extend([f"%{keyword}%", f"%{keyword}%"])
    params = [vector_literal, *like_params, *filter_params, limit]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        names = [description.name for description in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def fuse(
    vector_hits: list[dict],
    keyword_hits: list[dict],
    *,
    limit: int,
    lam: float = DEFAULT_LAMBDA,
) -> list[Hit]:
    """按 id 合并两路结果，最终分 = 相似度 + λ × 关键词命中率。"""
    merged: dict[str, dict] = {}

    for row in vector_hits:
        merged[row["id"]] = {**row, "keyword_score": 0.0, "matched_by": "vector"}

    for row in keyword_hits:
        current = merged.get(row["id"])
        score = row.get("keyword_score", 0.0)
        if current is None:
            merged[row["id"]] = {**row, "keyword_score": score, "matched_by": "keyword"}
            continue
        # 两路都命中：取较高的关键词得分，并标记来源
        current["keyword_score"] = max(current["keyword_score"], score)
        current["matched_by"] = "both"

    hits = [
        Hit(
            id=row["id"],
            title=row["title"],
            content=row["content"],
            source_id=row["source_id"],
            department=row.get("department"),
            scope_text=row["scope_text"],
            version=row["version"],
            similarity=row["similarity"],
            keyword_score=row["keyword_score"],
            score=row["similarity"] + lam * row["keyword_score"],
            matched_by=row["matched_by"],
        )
        for row in merged.values()
    ]
    # 分数相同时用相似度兜底，保证排序稳定、可复现
    hits.sort(key=lambda hit: (hit.score, hit.similarity), reverse=True)
    return hits[:limit]


def retrieve(
    question: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_THRESHOLD,
    lam: float = DEFAULT_LAMBDA,
    category: str | None = None,
    risk_level: str | None = None,
    conn=None,
    client=None,
) -> list[Hit]:
    """一次完整检索：向量化 → 双路取候选 → 融合。"""
    if not question or not question.strip():
        return []

    active_conn = conn or connect()
    embedding = embed_texts([question], client=client)
    if not embedding.vectors:
        return []

    vector_literal = to_vector_literal(embedding.vectors[0])
    candidate_limit = top_k * CANDIDATE_MULTIPLIER

    vector_hits = vector_search(
        active_conn,
        vector_literal,
        limit=candidate_limit,
        threshold=threshold,
        category=category,
        risk_level=risk_level,
    )

    keywords = extract_keywords(question)
    keyword_hits = keyword_search(
        active_conn,
        vector_literal,
        keywords,
        limit=candidate_limit,
        category=category,
        risk_level=risk_level,
    )
    for row in keyword_hits:
        row["keyword_score"] = keyword_score(row["content"], keywords)

    return fuse(vector_hits, keyword_hits, limit=top_k, lam=lam)