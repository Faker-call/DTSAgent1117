"""重排：本地交叉编码器优先，缺模型时回退到词法重排。"""

from __future__ import annotations

from typing import Protocol

from pmagent.knowledge.retrieval.query import tokenize


class Reranker(Protocol):
    name: str

    def score(self, query: str, texts: list[str]) -> list[float]: ...


class LexicalReranker:
    """无模型可用时的确定性回退：按命中词覆盖率打分。"""

    name = "lexical"

    def score(self, query: str, texts: list[str]) -> list[float]:
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return [0.0 for _ in texts]
        scores: list[float] = []
        for text in texts:
            text_tokens = set(tokenize(text))
            overlap = len(query_tokens & text_tokens)
            scores.append(overlap / len(query_tokens))
        return scores


class CrossEncoderReranker:
    """本地交叉编码器；未安装 sentence-transformers 时抛出明确错误。"""

    name = "cross-encoder"

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise RuntimeError(
                "本地交叉编码器不可用：未安装 sentence-transformers 或缺少模型缓存"
            ) from exc
        self._model = CrossEncoder(model_name)

    def score(self, query: str, texts: list[str]) -> list[float]:  # pragma: no cover
        if not texts:
            return []
        return [float(value) for value in self._model.predict([(query, text) for text in texts])]


def default_reranker() -> Reranker:
    """优先交叉编码器，失败回退词法重排（不因缺模型阻断检索）。"""
    try:  # pragma: no cover - 依赖环境
        return CrossEncoderReranker()
    except Exception:
        return LexicalReranker()
