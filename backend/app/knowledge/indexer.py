"""把知识条目向量化并写入 pgvector。

入库是幂等的：同一个 id 重复执行只会覆盖，不会产生重复行。
"""

import json

from backend.app.knowledge.db import connect, ensure_schema, to_vector_literal
from backend.app.knowledge.schema import KnowledgeEntry, load_entries
from backend.app.llm.embeddings import embed_texts

UPSERT_SQL = """
INSERT INTO {table}
    (id, category, title, content, source_id, scope_payload, scope_text, version, embedding)
VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::vector)
ON CONFLICT (id) DO UPDATE SET
    category      = EXCLUDED.category,
    title         = EXCLUDED.title,
    content       = EXCLUDED.content,
    source_id     = EXCLUDED.source_id,
    scope_payload = EXCLUDED.scope_payload,
    scope_text    = EXCLUDED.scope_text,
    version       = EXCLUDED.version,
    embedding     = EXCLUDED.embedding
"""


def build_index(
    entries: list[KnowledgeEntry] | None = None,
    *,
    conn=None,
    client=None,
) -> dict:
    """向量化全部条目并入库，返回本次入库的摘要。

    conn 与 client 都可注入，测试时不需要真实数据库和网络。
    """
    active_entries = load_entries() if entries is None else entries
    if not active_entries:
        return {"count": 0, "dimension": 0, "model": "none", "tokens": None}

    result = embed_texts([entry.content for entry in active_entries], client=client)
    if result.dimension <= 0:
        raise ValueError("向量化结果为空，无法建表")

    active_conn = conn or connect()
    table = ensure_schema(active_conn, result.dimension)

    rows = [
        (
            entry.id,
            entry.category,
            entry.title,
            entry.content,
            entry.source_id,
            json.dumps(entry.scope_payload.model_dump(), ensure_ascii=False),
            entry.scope_text,
            entry.version,
            to_vector_literal(vector),
        )
        for entry, vector in zip(active_entries, result.vectors)
    ]

    with active_conn.cursor() as cur:
        cur.executemany(UPSERT_SQL.format(table=table), rows)

    return {
        "count": len(rows),
        "dimension": result.dimension,
        "model": result.model,
        "tokens": result.input_tokens,
    }