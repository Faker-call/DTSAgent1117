"""百炼向量化适配：批量、退避、结果缓存。"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Any

from pmagent.config import EmbeddingConfig
from pmagent.gateway.base import GatewayError, Transport
from pmagent.gateway.keys import require_keys

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


def http_embedding_transport(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={**headers, "Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return int(exc.code), json.loads(raw)
        except json.JSONDecodeError:
            return int(exc.code), {"error": raw}
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, {"error": str(exc)}


class EmbeddingCache:
    def __init__(self) -> None:
        self._data: dict[str, list[float]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> list[float] | None:
        value = self._data.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    def put(self, key: str, vector: list[float]) -> None:
        self._data[key] = vector

    def __len__(self) -> int:
        return len(self._data)


class AdaptiveLimiter:
    """遇限流时对半降并发，成功时缓慢恢复。"""

    def __init__(self, initial: int = 4, minimum: int = 1, maximum: int | None = None) -> None:
        self.limit = max(minimum, initial)
        self.minimum = minimum
        self.maximum = maximum if maximum is not None else initial

    def on_throttle(self) -> None:
        self.limit = max(self.minimum, self.limit // 2)

    def on_success(self) -> None:
        if self.limit < self.maximum:
            self.limit = min(self.maximum, self.limit + 1)


class BailianEmbedding:
    def __init__(
        self,
        config: EmbeddingConfig | None = None,
        *,
        api_key: str | None = None,
        transport: Transport | None = None,
        sleep: Any = time.sleep,
        cache: EmbeddingCache | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.config = config or EmbeddingConfig()
        if api_key is None:
            api_key = require_keys(embedding=True, environ=environ).embedding
        if not api_key:
            raise GatewayError("向量化密钥为空")
        self.api_key = api_key
        self.transport: Transport = transport or http_embedding_transport
        self._sleep = sleep
        self.cache = cache if cache is not None else EmbeddingCache()
        self.limiter = AdaptiveLimiter(initial=max(2, self.config.batch_size // 5))
        self.max_retries = max_retries
        self.timeout = timeout

    def _cache_key(self, text: str) -> str:
        digest = hashlib.sha256(
            f"{self.config.model}:{self.config.dim}:{text}".encode()
        ).hexdigest()
        return digest

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float] | None] = [None] * len(texts)
        pending: list[tuple[int, str]] = []

        for index, text in enumerate(texts):
            cached = self.cache.get(self._cache_key(text))
            if cached is None:
                pending.append((index, text))
            else:
                vectors[index] = cached

        batch_size = max(1, self.config.batch_size)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            batch_vectors = self._embed_batch([text for _, text in batch])
            for (index, text), vector in zip(batch, batch_vectors, strict=True):
                vectors[index] = vector
                self.cache.put(self._cache_key(text), vector)

        return [vector if vector is not None else [] for vector in vectors]

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.config.model,
            "input": texts,
            "dimensions": self.config.dim,
            "encoding_format": "float",
        }
        url = f"{self.config.base_url.rstrip('/')}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error = "未知错误"

        for attempt in range(1, self.max_retries + 2):
            status, body = self.transport(url, headers, payload, self.timeout)
            if status == 200:
                self.limiter.on_success()
                return self._parse(body, len(texts))
            last_error = self._error_message(status, body)
            if status == 429:
                self.limiter.on_throttle()
            if status != 0 and status not in RETRYABLE_STATUS:
                raise GatewayError(f"向量化请求失败（HTTP {status}）：{last_error}")
            if attempt <= self.max_retries:
                self._sleep(min(8.0, 0.5 * (2 ** (attempt - 1))) * (0.5 + random.random()))

        raise GatewayError(f"向量化重试 {self.max_retries} 次后仍失败：{last_error}")

    @staticmethod
    def _parse(body: dict[str, Any], expected: int) -> list[list[float]]:
        data = body.get("data") or []
        if len(data) != expected:
            raise GatewayError(f"向量化返回数量不符：期望 {expected}，实际 {len(data)}")
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        return [list(item.get("embedding") or []) for item in ordered]

    @staticmethod
    def _error_message(status: int, body: dict[str, Any]) -> str:
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message", error))
        if error:
            return str(error)
        return f"HTTP {status}"


def embedding_available(environ: dict[str, str] | None = None) -> bool:
    environ = dict(os.environ if environ is None else environ)
    from pmagent.config import EMBEDDING_ENV_KEY

    return bool(environ.get(EMBEDDING_ENV_KEY))
