from __future__ import annotations

import array
import sqlite3
import subprocess
from pathlib import Path

import pytest

from pmagent.contracts import DocRecord
from pmagent.knowledge.corpus import DocCorpus
from pmagent.knowledge.index import CodeIndex, IndexBuilder
from pmagent.knowledge.retrieval import (
    HybridRetriever,
    LexicalReranker,
    plan_query,
    split_identifier,
    tokenize,
)

GEOMETRY = """
namespace pm {
int BuildStrip(int n) { return n + 1; }
int Tessellate(int n) { return BuildStrip(n); }
}
"""

STYLE = """
namespace pm {
int ApplyStyle(int n) { return n * 2; }
}
"""

LONG_FUNC = "int Big(int n) {\n" + "\n".join(f"  n += {i};" for i in range(500)) + "\n  return n;\n}\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")
    (repo / "geometry.cc").write_text(GEOMETRY, encoding="utf-8")
    (repo / "style.cc").write_text(STYLE, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    1.0 if "strip" in lowered or "tessell" in lowered else 0.0,
                    1.0 if "style" in lowered else 0.0,
                    0.1,
                ]
            )
        return vectors


def _seed_vectors(index_root: Path, commit: str, embedder: FakeEmbedder) -> None:
    db = index_root / commit[:12] / "index.db"
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT id, text FROM chunks").fetchall()
        vectors = embedder.embed([text for _, text in rows])
        for (chunk_id, _), vector in zip(rows, vectors, strict=True):
            conn.execute(
                "INSERT OR REPLACE INTO chunks_vec (chunk_id, dim, vector) VALUES (?,?,?)",
                (chunk_id, len(vector), array.array("f", vector).tobytes()),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def indexed(tmp_path: Path):  # noqa: ANN201
    repo = _repo(tmp_path)
    root = tmp_path / "index"
    builder = IndexBuilder(root)
    manifest = builder.build(repo)
    _seed_vectors(root, manifest.commit, FakeEmbedder())
    index = CodeIndex.open(root)
    try:
        yield index, manifest
    finally:
        index.close()


def test_identifier_splitting() -> None:
    assert split_identifier("BuildStrip")[:2] == ["build", "strip"]
    tokens = split_identifier("build_strip")
    assert "build" in tokens and "strip" in tokens
    assert set(split_identifier("pm::render::BuildStrip")) >= {"pm", "render", "build", "strip"}


def test_tokenize_keeps_cjk() -> None:
    tokens = tokenize("地图 BuildStrip 断裂")
    assert "buildstrip" in tokens
    assert any("断裂" in token for token in tokens)


def test_plan_query_multi_variants() -> None:
    plan = plan_query("BuildStrip 出现断裂")
    assert len(plan.variants) >= 2
    assert "buildstrip" in plan.tokens


def test_three_channels(indexed) -> None:  # noqa: ANN001
    index, _manifest = indexed
    retriever = HybridRetriever(code_index=index)
    hits = retriever.search("Tessellate", intent="localize")
    assert hits
    symbols = {hit.symbol for hit in hits}
    assert any(symbol and "Tessellate" in symbol for symbol in symbols)
    # 符号图扩展：BuildStrip 是 Tessellate 的被调方
    graph_hits = [hit for hit in hits if "graph" in hit.channels]
    assert any(hit.symbol and "BuildStrip" in hit.symbol for hit in graph_hits)
    assert all(hit.score > 0 for hit in hits)


def test_vector_channel(indexed) -> None:  # noqa: ANN001
    index, _manifest = indexed
    retriever = HybridRetriever(code_index=index)
    hits = retriever.search("strip", intent="defect", embedder=FakeEmbedder(), limit=10)
    assert any("vector" in hit.channels for hit in hits)


def test_adjacent_chunks_merge(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "T")
    (repo / "big.cc").write_text(LONG_FUNC, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    index_root = tmp_path / "index"
    IndexBuilder(index_root).build(repo)
    index = CodeIndex.open(index_root)
    try:
        retriever = HybridRetriever(code_index=index)
        hits = retriever.search("n += 100", intent="localize", limit=10)
        big_hits = [hit for hit in hits if hit.symbol and "Big" in hit.symbol]
        assert len(big_hits) == 1
        assert (big_hits[0].line_end or 0) - (big_hits[0].line_start or 0) > 50
    finally:
        index.close()


def test_diversity_caps_per_file(indexed) -> None:  # noqa: ANN001
    index, _manifest = indexed
    retriever = HybridRetriever(code_index=index)
    hits = retriever.search("pm", intent="localize", limit=10)
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit.path or ""] = counts.get(hit.path or "", 0) + 1
    assert all(count <= 2 for count in counts.values())


def test_doc_version_filter(tmp_path: Path) -> None:
    corpus = DocCorpus(tmp_path / "docs.db")
    try:
        corpus.add_documents(
            [
                DocRecord(id="d1", title="t1", url="https://a", version="6.0.1", text="# t1\n\ntile pipeline"),
                DocRecord(id="d2", title="t2", url="https://b", version="5.5.2", text="# t2\n\ntile pipeline"),
            ]
        )
        retriever = HybridRetriever(doc_corpus=corpus)
        hits = retriever.search("tile", intent="usage", version="6.0.1")
        urls = {hit.path for hit in hits}
        assert "https://a" in urls
        assert "https://b" not in urls
    finally:
        corpus.close()


def test_intent_weighting_changes_order(tmp_path: Path) -> None:
    corpus = DocCorpus(tmp_path / "docs.db")
    try:
        corpus.add_documents(
            [DocRecord(id="d1", title="style guide", url="https://doc", text="# style\n\ntile style guide")]
        )
        repo = _repo(tmp_path)
        index = CodeIndex.open(IndexBuilder(tmp_path / "index").build(repo) and tmp_path / "index")
        try:
            retriever = HybridRetriever(code_index=index, doc_corpus=corpus)
            usage = retriever.search("style tile", intent="usage", limit=5)
            localize = retriever.search("style tile", intent="localize", limit=5)
            assert usage and localize
            assert usage[0].kind == "doc"
            assert localize[0].kind == "code"
        finally:
            index.close()
    finally:
        corpus.close()


def test_lexical_reranker_orders_by_overlap() -> None:
    reranker = LexicalReranker()
    scores = reranker.score("tile strip", ["tile strip pipeline", "unrelated text"])
    assert scores[0] > scores[1]


def test_reranker_affects_final_order(indexed) -> None:  # noqa: ANN001
    index, _manifest = indexed
    retriever = HybridRetriever(code_index=index)
    hits = retriever.search("ApplyStyle", intent="usage")
    assert hits
    assert hits[0].snippet
