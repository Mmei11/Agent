"""知识库的 Postgres 连接与建表。

表和索引都由应用自己建（IF NOT EXISTS），不依赖容器的初始化脚本，
本地起一个空库也能直接跑。

表名可用 KNOWLEDGE_TABLE 覆盖，测试靠它写到独立的表里，
避免跑一次单测就把开发库的向量换成假向量。
"""

import os
import re

import psycopg

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:15433/knowledge"
DEFAULT_TABLE = "knowledge_chunks"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table} (
    id            text PRIMARY KEY,
    category      text NOT NULL,
    title         text NOT NULL,
    content       text NOT NULL,
    source_id     text NOT NULL,
    scope_payload jsonb NOT NULL,
    scope_text    text NOT NULL,
    version       text NOT NULL,
    embedding     vector({dimension}) NOT NULL
)
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS {table}_embedding_idx
    ON {table} USING hnsw (embedding vector_cosine_ops)
"""


def dsn() -> str:
    return os.getenv("KNOWLEDGE_DB_URL", DEFAULT_DSN)


def table_name() -> str:
    """表名会拼进 SQL，只允许小写标识符，挡住配置写错带来的注入。"""
    name = os.getenv("KNOWLEDGE_TABLE", DEFAULT_TABLE)
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise ValueError(f"非法的表名：{name!r}")
    return name


def connect():
    """每次调用开一条新连接。脚本场景够用，接入 API 时再换连接池。"""
    return psycopg.connect(dsn(), autocommit=True)


def ensure_schema(conn, dimension: int) -> str:
    """建扩展、建表、建索引，返回实际使用的表名。"""
    # 维度会被拼进 SQL，必须先确认是正整数，不能直接信任外部传入。
    if not isinstance(dimension, int) or dimension <= 0:
        raise ValueError(f"向量维度非法：{dimension!r}")

    table = table_name()
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(CREATE_TABLE_SQL.format(table=table, dimension=dimension))
        # 表已存在时 CREATE TABLE IF NOT EXISTS 不会改列宽。维度变了却继续入库，
        # 错误会拖到插入时才爆，而且信息更难读，所以这里先挡一次。
        cur.execute(
            """
            SELECT format_type(atttypid, atttypmod)
            FROM pg_attribute
            WHERE attrelid = %s::regclass AND attname = 'embedding'
            """,
            (table,),
        )
        row = cur.fetchone()
        if row and row[0] != f"vector({dimension})":
            raise ValueError(
                f"已有表的 embedding 列是 {row[0]}，与当前维度 {dimension} 不一致；"
                f"请先 DROP TABLE {table} 再重建索引"
            )
        cur.execute(CREATE_INDEX_SQL.format(table=table))
    return table


def to_vector_literal(vector: list[float]) -> str:
    """转成 pgvector 认的 '[0.1,0.2,...]' 字面量，配合 %s::vector 使用。"""
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"