"""工具与模型结果缓存：按内容哈希复用，恢复运行不重复计费。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def make_key(kind: str, payload: object, version: str = "") -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{kind}|{version}|{raw}".encode()).hexdigest()
    return digest


class ResultCache:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._data: dict[str, object] = {}
        self.hits = 0
        self.misses = 0
        if self.path is not None and self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    def get(self, key: str) -> object | None:
        if key in self._data:
            self.hits += 1
            return self._data[key]
        self.misses += 1
        return None

    def put(self, key: str, value: object) -> None:
        self._data[key] = value
        self._persist()

    def _persist(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def __len__(self) -> int:
        return len(self._data)


class CachedCaller:
    """包一层：命中直接返回，未命中调用并写入缓存。"""

    def __init__(self, cache: ResultCache, kind: str, version: str = "") -> None:
        self.cache = cache
        self.kind = kind
        self.version = version

    def call(self, payload: object, fn) -> object:  # noqa: ANN001
        key = make_key(self.kind, payload, self.version)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        value = fn()
        self.cache.put(key, value)
        return value
