from __future__ import annotations

import pytest

from pmagent.config import EmbeddingConfig
from pmagent.errors import ConfigError
from pmagent.gateway.base import GatewayError
from pmagent.gateway.embedding import BailianEmbedding, EmbeddingCache


class FakeEmbeddingTransport:
    def __init__(self, dim: int = 4, fail_first: int = 0) -> None:
        self.dim = dim
        self.fail_first = fail_first
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):  # noqa: ANN001
        self.calls.append(payload)
        if self.fail_first > 0:
            self.fail_first -= 1
            return 429, {"error": {"message": "rate limited"}}
        inputs = payload["input"]
        return 200, {
            "data": [
                {"index": index, "embedding": [float(len(text))] * self.dim}
                for index, text in enumerate(inputs)
            ]
        }


def _embedding(transport: FakeEmbeddingTransport, **kwargs) -> BailianEmbedding:
    return BailianEmbedding(
        EmbeddingConfig(batch_size=20, dim=4),
        api_key="test",
        transport=transport,
        sleep=lambda _s: None,
        **kwargs,
    )


def test_batches_by_twenty() -> None:
    transport = FakeEmbeddingTransport()
    embedding = _embedding(transport)
    texts = [f"text-{i}" for i in range(45)]
    vectors = embedding.embed(texts)
    assert len(vectors) == 45
    assert [len(call["input"]) for call in transport.calls] == [20, 20, 5]
    assert transport.calls[0]["dimensions"] == 4
    assert transport.calls[0]["model"] == "qwen3.7-text-embedding"


def test_cache_hit_avoids_second_call() -> None:
    transport = FakeEmbeddingTransport()
    embedding = _embedding(transport)
    embedding.embed(["a", "b"])
    embedding.embed(["a", "b"])
    assert len(transport.calls) == 1
    assert embedding.cache.hits == 2
    assert embedding.cache.misses == 2


def test_retry_on_429() -> None:
    transport = FakeEmbeddingTransport(fail_first=1)
    embedding = _embedding(transport)
    assert embedding.limiter.limit == 4
    vectors = embedding.embed(["a"])
    assert len(vectors) == 1
    assert len(transport.calls) == 2
    # 遇到限流后对半降并发，成功一次后回升一档
    assert embedding.limiter.limit == 3


def test_adaptive_limiter_bounds() -> None:
    from pmagent.gateway.embedding import AdaptiveLimiter

    limiter = AdaptiveLimiter(initial=4, minimum=1)
    for _ in range(5):
        limiter.on_throttle()
    assert limiter.limit == 1
    for _ in range(10):
        limiter.on_success()
    assert limiter.limit == 4


def test_missing_key_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        BailianEmbedding(EmbeddingConfig(), api_key=None, environ={})


def test_count_mismatch_raises() -> None:
    transport = FakeEmbeddingTransport()
    embedding = _embedding(transport)
    embedding.transport = lambda *args: (200, {"data": []})
    with pytest.raises(GatewayError):
        embedding.embed(["a"])


def test_empty_input() -> None:
    transport = FakeEmbeddingTransport()
    assert _embedding(transport).embed([]) == []
    assert transport.calls == []


def test_cache_counts_persist() -> None:
    cache = EmbeddingCache()
    cache.put("k", [1.0])
    assert cache.get("k") == [1.0]
    assert cache.get("missing") is None
    assert len(cache) == 1
