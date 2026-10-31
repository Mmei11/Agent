"""Phase 4：文本向量化。

与 chat 共用同一个 OpenAI 兼容客户端（同一个 Key、同一个 Base URL），
上层只认 EmbeddingResult，不认 SDK 的响应对象。
"""

import hashlib
import math
import os
import random
import time
from dataclasses import dataclass

from backend.app.config import load_env_file
from backend.app.llm.client import LLMError, _is_retryable, get_client
from backend.app.observability import span

DEFAULT_EMBEDDING_MODEL = "text-embedding-v3"
DEFAULT_EMBEDDING_DIMENSION = 1024
DEFAULT_BATCH_SIZE = 10  # DashScope text-embedding-v3 单批上限就是 10，超过直接 400


@dataclass(frozen=True)
class EmbeddingResult:
    """一次向量化的归一化结果。vectors 与输入 texts 一一对应、顺序不变。"""

    vectors: list[list[float]]
    model: str
    input_tokens: int | None
    latency_ms: int
    cost_estimate: float | None

    @property
    def dimension(self) -> int:
        """向量维度；空输入时为 0。建表时用它决定 pgvector 的列宽。"""
        return len(self.vectors[0]) if self.vectors else 0


def _provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "dashscope").strip().lower()


def _dimension() -> int:
    return int(os.getenv("EMBEDDING_DIMENSION", DEFAULT_EMBEDDING_DIMENSION))


def _batch_size() -> int:
    return max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", DEFAULT_BATCH_SIZE)))


def _mock_vector(text: str, dimension: int) -> list[float]:
    """由文本哈希生成确定性伪向量。

    只用于离线跑通链路：同样的文本永远得到同样的向量，但没有语义能力，
    测出来的 recall 没有意义，不能拿去下结论。
    """
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(dimension)]
    norm = math.sqrt(sum(value * value for value in raw)) or 1.0
    return [value / norm for value in raw]


def _estimate_cost(input_tokens: int | None) -> float | None:
    """向量模型通常按输入 token 计价；没填单价就不猜。"""
    price = os.getenv("EMBEDDING_PRICE_PER_1K")
    if input_tokens is None or not price:
        return None
    try:
        return round(input_tokens / 1000 * float(price), 6)
    except (TypeError, ValueError):
        return None


def _mock_embed(texts: list[str]) -> EmbeddingResult:
    dimension = _dimension()
    started = time.perf_counter()
    vectors = [_mock_vector(text, dimension) for text in texts]
    return EmbeddingResult(
        vectors=vectors,
        model="mock",
        input_tokens=None,
        latency_ms=round((time.perf_counter() - started) * 1000),
        cost_estimate=None,
    )


def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
    client=None,
) -> EmbeddingResult:
    """把一批文本转成向量。空输入直接返回空结果，不发请求。

    client 参数用于测试注入假对象；正常调用走 get_client()。
    """
    if not texts:
        return EmbeddingResult(
            vectors=[], model="none", input_tokens=None, latency_ms=0, cost_estimate=None
        )

    if _provider() == "mock":
        return _mock_embed(texts)

    load_env_file()
    active_client = client or get_client()
    active_model = model or os.getenv("DASHSCOPE_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    attributes = {
        "gen_ai.system": "dashscope",
        "gen_ai.operation.name": "embed",
        "gen_ai.request.model": active_model,
        "embedding.batch_size": _batch_size(),
        "embedding.input_count": len(texts),
    }

    with span("llm.embed", kind="llm", attributes=attributes) as record:
        started = time.perf_counter()
        vectors: list[list[float]] = []
        total_tokens = 0
        for offset in range(0, len(texts), _batch_size()):
            batch = texts[offset : offset + _batch_size()]
            try:
                response = active_client.embeddings.create(model=active_model, input=batch)
            except Exception as exc:
                # 只暴露异常类型，不带原文和 Key，避免敏感内容进日志。
                record.attributes["error.retryable"] = _is_retryable(exc)
                raise LLMError(
                    f"向量化失败：{type(exc).__name__}",
                    retryable=_is_retryable(exc),
                ) from exc

            data = getattr(response, "data", None) or []
            if len(data) != len(batch):
                # 条数对不上就直接报错：错位入库比失败更难查。
                record.attributes["error.retryable"] = False
                raise LLMError(
                    f"向量数量不匹配：期望 {len(batch)}，实际 {len(data)}",
                    retryable=False,
                )
            # 按 index 归位，而不是相信返回顺序：顺序错乱不会有任何报错，
            # 只会让向量和文本静默错配。
            placed: list[list[float] | None] = [None] * len(batch)
            for item in data:
                index = getattr(item, "index", None)
                if not isinstance(index, int) or not 0 <= index < len(batch):
                    record.attributes["error.retryable"] = False
                    raise LLMError(f"向量 index 非法：{index}", retryable=False)
                placed[index] = item.embedding
            if any(vector is None for vector in placed):
                record.attributes["error.retryable"] = False
                raise LLMError("向量 index 有缺失，无法归位", retryable=False)
            vectors.extend(placed)  # type: ignore[arg-type]
            usage = getattr(response, "usage", None)
            total_tokens += getattr(usage, "prompt_tokens", 0) or 0

        latency_ms = round((time.perf_counter() - started) * 1000)
        tokens = total_tokens or None
        record.attributes["gen_ai.usage.input_tokens"] = tokens
        record.attributes["llm.latency_ms"] = latency_ms
        if vectors:
            record.attributes["embedding.dimension"] = len(vectors[0])

        return EmbeddingResult(
            vectors=vectors,
            model=active_model,
            input_tokens=tokens,
            latency_ms=latency_ms,
            cost_estimate=_estimate_cost(tokens),
        )