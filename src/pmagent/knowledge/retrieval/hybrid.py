"""混合检索：BM25 + 向量 + 符号图三路召回，RRF 融合、相邻合并、重排与多样性。"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from pmagent.contracts import RetrievalHit
from pmagent.knowledge.corpus import DocCorpus, DocHit
from pmagent.knowledge.index import CodeIndex
from pmagent.knowledge.retrieval.query import QueryPlan, plan_query
from pmagent.knowledge.retrieval.rerank import LexicalReranker, Reranker

RRF_K = 60
ADJACENT_GAP = 5
MAX_PER_FILE = 2


@dataclass
class Candidate:
    key: str
    hit: RetrievalHit
    text: str
    rrf: float = 0.0
    channels: set[str] = field(default_factory=set)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    ln = math.sqrt(sum(a * a for a in left))
    rn = math.sqrt(sum(b * b for b in right))
    return dot / (ln * rn) if ln and rn else 0.0


def _from_blob(blob: bytes) -> list[float]:
    import array

    values = array.array("f")
    values.frombytes(blob)
    return list(values)


class HybridRetriever:
    def __init__(
        self,
        code_index: CodeIndex | None = None,
        doc_corpus: DocCorpus | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.code_index = code_index
        self.doc_corpus = doc_corpus
        self.reranker = reranker or LexicalReranker()

    def search(
        self,
        query: str,
        *,
        intent: str = "usage",
        version: str | None = None,
        limit: int = 8,
        top_k: int = 50,
        embedder: Any | None = None,
    ) -> list[RetrievalHit]:
        plan = plan_query(query, intent=intent)
        weights = plan.weights
        candidates: dict[str, Candidate] = {}
        channel_ranks: dict[str, list[Candidate]] = {
            "bm25": [],
            "vector": [],
            "graph": [],
            "doc": [],
        }

        for candidate in self._code_bm25(plan):
            channel_ranks["bm25"].append(candidate)
        for candidate in self._doc_hits(plan, version):
            channel_ranks["doc"].append(candidate)
        if embedder is not None and self.doc_corpus is not None:
            for candidate in self._doc_vector(plan, embedder, version):
                channel_ranks["vector"].append(candidate)
        if self.code_index is not None and embedder is not None:
            for candidate in self._code_vector(plan, embedder):
                channel_ranks["vector"].append(candidate)
        seeds = [
            hit.symbol
            for hit in (c.hit for c in channel_ranks["bm25"])
            if hit.symbol
        ][:5]
        for candidate in self._graph(seeds):
            channel_ranks["graph"].append(candidate)

        for channel, items in channel_ranks.items():
            if not items:
                continue
            weight = weights.get(channel, 1.0)
            for rank, item in enumerate(items):
                key = item.key
                existing = candidates.get(key)
                if existing is None:
                    item.rrf = weight / (RRF_K + rank + 1)
                    item.channels = {channel}
                    candidates[key] = item
                else:
                    existing.rrf += weight / (RRF_K + rank + 1)
                    existing.channels.add(channel)

        merged = self._merge_adjacent(list(candidates.values()))
        ranked = sorted(merged, key=lambda item: item.rrf, reverse=True)[:top_k]
        reranked = self._rerank(plan.raw, ranked)
        return self._diversify(reranked, limit)

    # -- 通道 -------------------------------------------------------------
    def _code_bm25(self, plan: QueryPlan) -> list[Candidate]:
        if self.code_index is None:
            return []
        match = " OR ".join(f'"{token}"' for token in plan.tokens if token)
        if not match:
            return []
        try:
            rows = self.code_index.conn.execute(
                "SELECT c.id, c.file_path, c.symbol_qualified, c.line_start, c.line_end, c.text"
                " FROM chunks_fts f JOIN chunks c ON c.id = f.chunk_id"
                " WHERE chunks_fts MATCH ? LIMIT 50",
                (match,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        return [self._row_candidate(row, "bm25") for row in rows]

    def _code_vector(self, plan: QueryPlan, embedder: Any) -> list[Candidate]:
        assert self.code_index is not None
        vectors = embedder.embed([" ".join(plan.tokens) or plan.raw])
        if not vectors or not vectors[0]:
            return []
        query_vector = vectors[0]
        rows = self.code_index.conn.execute(
            "SELECT v.vector, c.id, c.file_path, c.symbol_qualified, c.line_start,"
            " c.line_end, c.text FROM chunks_vec v JOIN chunks c ON c.id = v.chunk_id"
            " LIMIT 5000"
        ).fetchall()
        scored: list[tuple[float, Candidate]] = []
        for row in rows:
            similarity = _cosine(query_vector, _from_blob(row["vector"]))
            if similarity > 0:
                scored.append((similarity, self._row_candidate(row, "vector")))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [candidate for _, candidate in scored[:50]]

    def _doc_hits(self, plan: QueryPlan, version: str | None) -> list[Candidate]:
        if self.doc_corpus is None:
            return []
        hits = self.doc_corpus.search(plan.raw, limit=50, version=version)
        return [self._doc_candidate(hit) for hit in hits]

    def _doc_vector(
        self, plan: QueryPlan, embedder: Any, version: str | None
    ) -> list[Candidate]:
        assert self.doc_corpus is not None
        hits = self.doc_corpus.search(
            plan.raw, limit=50, version=version, embedder=embedder
        )
        return [
            self._doc_candidate(hit)
            for hit in hits
            if "vector" in hit.channels
        ]

    def _graph(self, seeds: list[str]) -> list[Candidate]:
        if self.code_index is None or not seeds:
            return []
        related: set[str] = set()
        for seed in seeds:
            rows = self.code_index.conn.execute(
                "SELECT to_name FROM refs WHERE from_symbol = ? LIMIT 10", (seed,)
            ).fetchall()
            related.update(row[0] for row in rows)
            callers = self.code_index.conn.execute(
                "SELECT DISTINCT from_symbol FROM refs WHERE to_name = ? LIMIT 10",
                (seed.split("::")[-1],),
            ).fetchall()
            related.update(row[0] for row in callers)
        related -= set(seeds)
        candidates: list[Candidate] = []
        for symbol in sorted(related)[:20]:
            rows = self.code_index.conn.execute(
                "SELECT id, file_path, symbol_qualified, line_start, line_end, text"
                " FROM chunks WHERE symbol_qualified = ? OR symbol_qualified LIKE ?"
                " LIMIT 3",
                (symbol, f"%::{symbol}"),
            ).fetchall()
            candidates.extend(self._row_candidate(row, "graph") for row in rows)
        return candidates[:50]

    # -- 组装 -------------------------------------------------------------
    def _row_candidate(self, row: Any, channel: str) -> Candidate:
        hit = RetrievalHit(
            chunk_id=str(row["id"]),
            kind="code",
            path=row["file_path"],
            line_start=int(row["line_start"]),
            line_end=int(row["line_end"]),
            symbol=row["symbol_qualified"],
            snippet=row["text"][:500],
            channels=[],
        )
        key = f"code::{row['file_path']}::{row['symbol_qualified']}::{row['line_start']}"
        return Candidate(key=key, hit=hit, text=row["text"])

    def _doc_candidate(self, hit: DocHit) -> Candidate:
        retrieval = RetrievalHit(
            chunk_id=f"doc-{hit.chunk_id}",
            kind="doc" if hit.doc_id.startswith("doc") else "doc",
            path=hit.url or f"doc:{hit.doc_id}",
            symbol=hit.breadcrumb,
            snippet=hit.text[:500],
            channels=list(hit.channels),
        )
        key = f"doc::{hit.doc_id}::{hit.breadcrumb}"
        return Candidate(key=key, hit=retrieval, text=hit.text)

    def _merge_adjacent(self, candidates: list[Candidate]) -> list[Candidate]:
        grouped: dict[tuple[str, str], list[Candidate]] = {}
        for candidate in candidates:
            if candidate.hit.kind != "code":
                grouped[(candidate.key, "")] = [candidate]
                continue
            key = (candidate.hit.path or "", candidate.hit.symbol or "")
            grouped.setdefault(key, []).append(candidate)

        merged: list[Candidate] = []
        for group in grouped.values():
            if len(group) == 1 or group[0].hit.kind != "code":
                merged.extend(group)
                continue
            group.sort(key=lambda item: item.hit.line_start or 0)
            current = group[0]
            for candidate in group[1:]:
                if (candidate.hit.line_start or 0) - (current.hit.line_end or 0) <= ADJACENT_GAP:
                    current.hit.line_end = max(
                        current.hit.line_end or 0, candidate.hit.line_end or 0
                    )
                    current.rrf = max(current.rrf, candidate.rrf)
                    current.channels |= candidate.channels
                    current.text = current.text + "\n" + candidate.text
                else:
                    merged.append(current)
                    current = candidate
            merged.append(current)
        return merged

    def _rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        if not candidates:
            return []
        scores = [
            float(value)
            for value in self.reranker.score(query, [item.text for item in candidates])
        ]
        peak = max(scores) if scores else 0.0
        for candidate, score in zip(candidates, scores, strict=True):
            normalized = (score / peak) if peak > 0 else 0.0
            candidate.rrf = candidate.rrf + 0.01 * normalized
        return sorted(candidates, key=lambda item: item.rrf, reverse=True)

    @staticmethod
    def _diversify(candidates: list[Candidate], limit: int) -> list[RetrievalHit]:
        per_file: dict[str, int] = {}
        results: list[RetrievalHit] = []
        for candidate in candidates:
            path = candidate.hit.path or candidate.hit.chunk_id
            if per_file.get(path, 0) >= MAX_PER_FILE:
                continue
            per_file[path] = per_file.get(path, 0) + 1
            candidate.hit.channels = sorted(candidate.channels)
            candidate.hit.score = round(candidate.rrf, 6)
            results.append(candidate.hit)
            if len(results) >= limit:
                break
        return results


__all__ = ["Candidate", "HybridRetriever"]
