"""Phase 4：关键词提取、双路融合与 pgvector 检索。

前两组是纯函数，离线可跑；数据库那组在没有 pgvector 时自动跳过。
"""

import hashlib
import math
import random

import pytest

from backend.app.knowledge import db
from backend.app.knowledge.indexer import build_index
from backend.app.knowledge.keywords import extract_keywords, keyword_score
from backend.app.knowledge.retrieval import _filter_sql, fuse, retrieve

# ---------- 关键词提取 ----------


def test_extracts_phrase_and_bigrams():
    keywords = extract_keywords("肚子疼从肚脐周围开始")
    assert "肚子疼从肚脐周围开始" in keywords or "肚子疼" in keywords
    assert "肚脐" in keywords  # 二元组展开


def test_drops_question_fillers():
    assert "什么" not in extract_keywords("什么是头痛")
    assert "怎么" not in extract_keywords("怎么办才好")


def test_keeps_longest_first_and_respects_cap():
    long_text = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥甲乙丙丁戊己庚辛壬癸子丑寅"
    keywords = extract_keywords(long_text)
    assert len(keywords) <= 32
    assert keywords[0] == long_text  # 整段最长，排在第一位


def test_latin_terms_are_lowercased():
    assert "chest" in extract_keywords("Chest pain 怎么办")


def test_empty_question_returns_empty():
    assert extract_keywords("") == []


def test_keyword_score_is_a_ratio():
    content = "腹痛伴呕吐、腹胀且停止排气排便"
    assert keyword_score(content, ["腹痛", "呕吐"]) == 1.0
    assert keyword_score(content, ["腹痛", "皮疹"]) == 0.5
    assert keyword_score(content, ["皮疹"]) == 0.0
    assert keyword_score(content, []) == 0.0


# ---------- 融合 ----------


def _row(id: str, similarity: float, **extra) -> dict:
    return {
        "id": id,
        "title": id,
        "content": "内容",
        "source_id": "src",
        "scope_text": "范围",
        "version": "v1",
        "similarity": similarity,
        **extra,
    }


def test_vector_only_hit_keeps_its_similarity():
    hits = fuse([_row("a", 0.8)], [], limit=5)
    assert hits[0].matched_by == "vector"
    assert hits[0].score == pytest.approx(0.8)
    assert hits[0].keyword_score == 0.0


def test_keyword_hit_is_boosted_by_lambda():
    hits = fuse([], [_row("b", 0.2, keyword_score=1.0)], limit=5, lam=0.1)
    assert hits[0].matched_by == "keyword"
    assert hits[0].score == pytest.approx(0.3)


def test_both_paths_merge_and_keep_best_keyword_score():
    hits = fuse(
        [_row("c", 0.7)],
        [_row("c", 0.7, keyword_score=0.4), _row("c", 0.7, keyword_score=0.9)],
        limit=5,
        lam=0.1,
    )
    assert len(hits) == 1
    assert hits[0].matched_by == "both"
    assert hits[0].keyword_score == pytest.approx(0.9)
    assert hits[0].score == pytest.approx(0.79)


def test_ranking_prefers_higher_combined_score():
    hits = fuse(
        [_row("low", 0.4)],
        [_row("high", 0.35, keyword_score=1.0)],
        limit=5,
        lam=0.1,
    )
    assert [hit.id for hit in hits] == ["high", "low"]


def test_limit_truncates_results():
    rows = [_row(str(index), 0.9 - index * 0.01) for index in range(10)]
    assert len(fuse(rows, [], limit=3)) == 3


# ---------- 过滤条件 ----------


def test_filter_sql_is_empty_without_filters():
    assert _filter_sql(None, None) == ("", [])


def test_filter_sql_uses_parameters_not_literals():
    clause, params = _filter_sql("department_route", "high")
    assert "%s" in clause
    assert params == ["department_route", "high"]


# ---------- 数据库（无 pgvector 时跳过） ----------


DIMENSION = 1024


class _Item:
    def __init__(self, index, embedding):
        self.index = index
        self.embedding = embedding


class _Response:
    def __init__(self, data):
        self.data = data
        self.usage = type("Usage", (), {"prompt_tokens": 1})()


class _FakeEmbeddings:
    def create(self, model, input):
        return _Response([_Item(index, _vector(text)) for index, text in enumerate(input)])


class _FakeClient:
    """离线向量：只保证同样文本同样向量，没有语义能力。

    因此下面那条"关键词能把正确条目捞回来"的断言，恰恰说明命中的是关键词路。
    """

    def __init__(self):
        self.embeddings = _FakeEmbeddings()


def _vector(text: str) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(DIMENSION)]
    norm = math.sqrt(sum(value * value for value in raw)) or 1.0
    return [value / norm for value in raw]


@pytest.fixture
def conn(monkeypatch):
    # 写独立的表：跑单测不该把开发库里的真实向量换成假向量
    monkeypatch.setenv("KNOWLEDGE_TABLE", "knowledge_chunks_test")
    try:
        connection = db.connect()
    except Exception as exc:  # 没起容器时跳过，不让单测依赖环境
        pytest.skip(f"需要 postgres+pgvector：{exc}")
    yield connection
    with connection.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS knowledge_chunks_test")
    connection.close()


@pytest.fixture
def indexed(conn):
    summary = build_index(conn=conn, client=_FakeClient())
    assert summary["count"] == 13
    return conn


def test_index_writes_every_entry(indexed):
    with indexed.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {db.table_name()}")
        assert cur.fetchone()[0] == 13


def test_index_is_idempotent(indexed):
    build_index(conn=indexed, client=_FakeClient())
    with indexed.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {db.table_name()}")
        assert cur.fetchone()[0] == 13


def test_keyword_path_rescues_exact_term(indexed):
    """随机向量下，只有关键词路能把"停止排气排便"精确对上。"""
    hits = retrieve(
        "肚子绞着疼，还吐了，好几天没排气排便",
        conn=indexed,
        client=_FakeClient(),
        top_k=5,
    )
    assert "route-abdomen-obstruction" in [hit.id for hit in hits]


def test_results_carry_traceability_fields(indexed):
    hits = retrieve("突然剧烈头痛", conn=indexed, client=_FakeClient(), top_k=3)
    assert hits
    for hit in hits:
        assert hit.source_id
        assert hit.scope_text
        assert hit.version == "v1"
        assert hit.matched_by in {"vector", "keyword", "both"}


def test_category_filter_narrows_results(indexed):
    hits = retrieve(
        "头痛",
        conn=indexed,
        client=_FakeClient(),
        category="red_flag",
        top_k=5,
    )
    assert hits == []


def test_empty_question_returns_nothing(indexed):
    assert retrieve("   ", conn=indexed, client=_FakeClient()) == []