from __future__ import annotations

import sqlite3
from pathlib import Path

from pmagent.agents import AnswerComposer, CitationValidator
from pmagent.contracts import DocRecord, RetrievalHit
from pmagent.knowledge.corpus import DocCorpus


def _hit(chunk_id: str, kind: str = "doc", path: str = "https://doc", symbol: str = "样式") -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        kind=kind,  # type: ignore[arg-type]
        path=path,
        symbol=symbol,
        snippet="如何设置自定义地图样式：使用 setCustomMapStyle。",
        channels=["bm25"],
    )


def test_compose_with_citations() -> None:
    answer = AnswerComposer().compose("如何设置样式", [_hit("doc-1"), _hit("doc-2", symbol="图层")])
    assert len(answer.citations) == 2
    assert answer.citations[0].citation_id == "C1"
    assert any("[C1]" in line for line in answer.basis)
    assert answer.confidence > 0.4


def test_compose_without_hits() -> None:
    answer = AnswerComposer().compose("如何设置样式", [])
    assert "没有检索到" in answer.conclusion
    assert answer.confidence <= 0.2
    assert answer.notes


def test_validator_accepts_existing_doc_chunk(tmp_path: Path) -> None:
    corpus = DocCorpus(tmp_path / "docs.db")
    try:
        corpus.add_documents(
            [DocRecord(id="d1", title="样式", text="# 样式\n\n如何设置自定义地图样式")]
        )
        chunk_id = f"doc-{corpus._conn.execute('SELECT MIN(id) FROM doc_chunks').fetchone()[0]}"
        answer = AnswerComposer().compose("样式", [_hit(chunk_id)])
        result = CitationValidator(doc_corpus=corpus).validate(answer)
        assert result.valid
        assert not result.removed
    finally:
        corpus.close()


def test_validator_removes_nonexistent_citation(tmp_path: Path) -> None:
    corpus = DocCorpus(tmp_path / "docs.db")
    try:
        corpus.add_documents([DocRecord(id="d1", title="样式", text="# 样式\n内容")])
        answer = AnswerComposer().compose("样式", [_hit("doc-99999")])
        result = CitationValidator(doc_corpus=corpus).validate(answer)
        assert not result.valid
        assert result.removed == ["C1"]
        assert any("未核实" in line for line in answer.basis)
    finally:
        corpus.close()


def test_validator_without_source_marks_unverifiable() -> None:
    answer = AnswerComposer().compose("样式", [_hit("doc-1")])
    result = CitationValidator().validate(answer)
    assert result.valid
    assert any("无法校验" in issue for issue in result.issues)


def test_validator_model_support_check(tmp_path: Path) -> None:
    corpus = DocCorpus(tmp_path / "docs.db")
    try:
        corpus.add_documents(
            [DocRecord(id="d1", title="样式", text="# 样式\n\n如何设置自定义地图样式")]
        )
        chunk_id = f"doc-{corpus._conn.execute('SELECT MIN(id) FROM doc_chunks').fetchone()[0]}"
        answer = AnswerComposer().compose("样式", [_hit(chunk_id)])
        validator = CitationValidator(
            doc_corpus=corpus, support_check=lambda question, citation: False
        )
        result = validator.validate(answer, question="样式")
        assert not result.valid
        assert result.removed == ["C1"]
        assert any("模型判定" in issue for issue in result.issues)
    finally:
        corpus.close()


def test_validator_checks_code_index(tmp_path: Path) -> None:
    db = tmp_path / "index.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO chunks (id, text) VALUES (1, 'int Add();')")
    conn.commit()

    class FakeIndex:
        def __init__(self, connection):  # noqa: ANN001
            self.conn = connection

    index = FakeIndex(conn)
    answer = AnswerComposer().compose(
        "Add", [_hit("1", kind="code", path="a.cc", symbol="pm::Add")]
    )
    result = CitationValidator(code_index=index).validate(answer)
    assert result.valid
    conn.close()
