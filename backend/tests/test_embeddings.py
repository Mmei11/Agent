"""Phase 4：向量化客户端。全部离线，不依赖网络与 Key。"""

import math

import pytest

from backend.app.llm.client import LLMError
from backend.app.llm.embeddings import embed_texts


def test_empty_input_returns_empty_without_request():
    result = embed_texts([])
    assert result.vectors == []
    assert result.dimension == 0


def test_mock_is_deterministic(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    assert embed_texts(["腹痛"]).vectors == embed_texts(["腹痛"]).vectors


def test_mock_vectors_are_unit_length(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    for vector in embed_texts(["头痛", "眩晕", "咳嗽"]).vectors:
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


def test_mock_respects_dimension(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "8")
    assert embed_texts(["x"]).dimension == 8


def test_mock_gives_different_texts_different_vectors(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    vectors = embed_texts(["头痛", "腹痛"]).vectors
    assert vectors[0] != vectors[1]


class _Item:
    def __init__(self, index, embedding):
        self.index = index
        self.embedding = embedding


class _Usage:
    prompt_tokens = 7


class _Response:
    def __init__(self, data):
        self.data = data
        self.usage = _Usage()


class _Embeddings:
    def __init__(self, calls, drop=0, reverse=False):
        self.calls = calls
        self.drop = drop
        self.reverse = reverse

    def create(self, model, input):
        self.calls.append(list(input))
        items = [_Item(index, [float(index)] * 3) for index, _ in enumerate(input)]
        if self.drop:
            items = items[: len(items) - self.drop]
        if self.reverse:
            items = list(reversed(items))
        return _Response(items)


class _FakeClient:
    def __init__(self, calls, drop=0, reverse=False):
        self.embeddings = _Embeddings(calls, drop, reverse)


def test_batches_by_env_size_and_preserves_order(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "2")
    calls = []
    result = embed_texts(["a", "b", "c", "d", "e"], client=_FakeClient(calls))

    assert [len(batch) for batch in calls] == [2, 2, 1]
    assert len(result.vectors) == 5
    # 每批内部从 0 重新编号：错位拼接会让这个断言失败
    assert result.vectors[0] == [0.0, 0.0, 0.0]
    assert result.vectors[2] == [0.0, 0.0, 0.0]


def test_count_mismatch_raises(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    with pytest.raises(LLMError, match="向量数量不匹配"):
        embed_texts(["a", "b"], client=_FakeClient([], drop=1))


def test_tokens_are_summed_across_batches(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "2")
    result = embed_texts(["a", "b", "c"], client=_FakeClient([]))
    assert result.input_tokens == 14  # 每批 7，共两批


def test_shuffled_response_is_reordered_by_index(monkeypatch):
    """供应商乱序返回时，仍按 index 归位，向量不会和文本错配。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    result = embed_texts(["a", "b", "c"], client=_FakeClient([], reverse=True))
    assert result.vectors == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]


class _BadIndexClient:
    def __init__(self, index):
        self.embeddings = self
        self.index = index

    def create(self, model, input):
        # 条数对得上，但最后一条的 index 越界
        items = [_Item(position, [0.0, 0.0]) for position in range(len(input))]
        items[-1] = _Item(self.index, [0.0, 0.0])
        return _Response(items)


def test_out_of_range_index_raises(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")
    with pytest.raises(LLMError, match="index 非法"):
        embed_texts(["a", "b"], client=_BadIndexClient(99))


def test_duplicate_index_leaves_hole_and_raises(monkeypatch):
    """两条都声称自己是 0 号：1 号位空缺，必须报错而不是静默补零。"""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "dashscope")

    class _Duplicate(_BadIndexClient):
        def create(self, model, input):
            return _Response([_Item(0, [0.0, 0.0]), _Item(0, [0.0, 0.0])])

    with pytest.raises(LLMError, match="index 有缺失"):
        embed_texts(["a", "b"], client=_Duplicate(0))