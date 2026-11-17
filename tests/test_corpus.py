from __future__ import annotations

import json
from pathlib import Path

import pytest

from pmagent.contracts import DocRecord
from pmagent.knowledge.corpus import (
    DocCorpus,
    HtmlAdapter,
    MarkdownAdapter,
    load_tickets,
    split_markdown,
)
from pmagent.knowledge.corpus.adapters import tickets_to_docs

REPO_ROOT = Path(__file__).resolve().parents[1]

MARKDOWN = """# 样式参考

整体介绍。

## 自定义样式

通过样式 ID 或 JSON 应用自定义样式。

```json
{"version": 8, "layers": []}
```

| 字段 | 说明 |
|---|---|
| version | 样式版本 |
| layers | 图层列表 |

### 注意事项

样式发布后立即生效。
"""


def test_markdown_adapter_extracts_title(tmp_path: Path) -> None:
    path = tmp_path / "style.md"
    path.write_text(MARKDOWN, encoding="utf-8")
    records = MarkdownAdapter().load(path)
    assert len(records) == 1
    assert records[0].title == "样式参考"
    assert "自定义样式" in records[0].text


def test_html_adapter_strips_tags(tmp_path: Path) -> None:
    path = tmp_path / "doc.html"
    path.write_text(
        "<html><head><title>地图样式</title></head>"
        "<body><h1>样式</h1><p>内容一</p><script>x=1</script></body></html>",
        encoding="utf-8",
    )
    record = HtmlAdapter().load(path)[0]
    assert record.title == "地图样式"
    assert "# 样式" in record.text
    assert "内容一" in record.text
    assert "x=1" not in record.text


def test_split_keeps_breadcrumb_code_and_table() -> None:
    record = DocRecord(id="d1", title="样式参考", text=MARKDOWN)
    chunks = split_markdown(record)
    kinds = {chunk.kind for chunk in chunks}
    assert kinds == {"text", "code", "table"}

    code = next(chunk for chunk in chunks if chunk.kind == "code")
    assert code.code_lang == "json"
    assert '"version": 8' in code.text
    assert code.breadcrumb == "样式参考 > 自定义样式"

    table = next(chunk for chunk in chunks if chunk.kind == "table")
    assert "| version | 样式版本 |" in table.text

    nested = [c for c in chunks if c.kind == "text" and "注意事项" in c.breadcrumb]
    assert nested and nested[0].breadcrumb == "样式参考 > 自定义样式 > 注意事项"


def test_split_long_prose_into_paragraphs() -> None:
    paragraph = "这是很长的一段说明。" * 300
    text = "# 标题\n\n" + paragraph + "\n\n" + paragraph
    chunks = split_markdown(DocRecord(id="d2", title="t", text=text))
    assert len(chunks) >= 2
    assert all(len(chunk.text) <= 3200 for chunk in chunks)


def test_load_tickets_skips_dirty_rows(tmp_path: Path) -> None:
    payload = {
        "tickets": [
            {"id": "T1", "symptom": "地图空白", "root_cause": "样式未加载"},
            {"id": "T2", "symptom": "标注错位", "root_cause": "布局未考虑旋转"},
            {"id": "T3", "root_cause": "缺少症状"},
            {"id": "T4", "symptom": "崩溃", "root_cause": "空指针", "affected_versions": 12},
        ]
    }
    path = tmp_path / "tickets.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tickets, skipped = load_tickets(path)
    assert [ticket.ticket_id for ticket in tickets] == ["T1", "T2"]
    assert len(skipped) == 2
    assert "symptom" in skipped[0]["reason"]
    assert "affected_versions" in skipped[1]["reason"]


class FakeEmbedder:
    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append(
                [
                    1.0 if "tile" in lowered else 0.0,
                    1.0 if "style" in lowered else 0.0,
                    0.1,
                ]
            )
        return vectors


def _doc(doc_id: str, text: str, version: str | None, url: str | None = None) -> DocRecord:
    return DocRecord(
        id=doc_id,
        title=f"文档 {doc_id}",
        url=url,
        version=version,
        text=f"# 文档 {doc_id}\n\n{text}\n",
    )


def test_corpus_keyword_search_returns_url(tmp_path: Path) -> None:
    corpus = DocCorpus(tmp_path / "docs.db")
    try:
        corpus.add_documents([_doc("d1", "tile loading is slow", "6.0.1", "https://example.com/d1")])
        hits = corpus.search("tile")
        assert hits
        assert hits[0].url == "https://example.com/d1"
        assert "bm25" in hits[0].channels
    finally:
        corpus.close()


def test_corpus_version_filter_and_unknown_weight(tmp_path: Path) -> None:
    corpus = DocCorpus(tmp_path / "docs.db")
    try:
        corpus.add_documents(
            [
                _doc("exact", "tile pipeline details", "6.0.1"),
                _doc("other", "tile pipeline details", "5.5.2"),
                _doc("unknown", "tile pipeline details", None),
            ]
        )
        hits = corpus.search("tile", version="6.0.1")
        ids = [hit.doc_id for hit in hits]
        assert "exact" in ids
        assert "other" not in ids
        assert "unknown" in ids
        assert ids.index("exact") < ids.index("unknown")
    finally:
        corpus.close()


def test_corpus_vector_search_channel(tmp_path: Path) -> None:
    corpus = DocCorpus(tmp_path / "docs.db")
    try:
        embedder = FakeEmbedder()
        corpus.add_documents(
            [_doc("d1", "tile loading pipeline", "6.0.1")],
            embedder=embedder,
        )
        assert corpus.counts()["vectors"] > 0
        hits = corpus.search("tile", embedder=embedder)
        assert hits
        assert "vector" in hits[0].channels
        assert "bm25" in hits[0].channels
    finally:
        corpus.close()


def test_tickets_become_docs(tmp_path: Path) -> None:
    payload = [{"id": "T1", "symptom": "空白", "root_cause": "样式未加载", "sdk_version": "6.0.1"}]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tickets, skipped = load_tickets(path)
    assert not skipped
    docs = tickets_to_docs(tickets)
    assert docs[0].source_type == "ticket"
    assert docs[0].version == "6.0.1"
    assert "症状" in docs[0].text


def test_synthetic_ticket_corpus_is_complete() -> None:
    path = REPO_ROOT / "data" / "tickets" / "synthetic_tickets.json"
    assert path.exists(), "先运行 python scripts/gen_tickets.py"
    data = json.loads(path.read_text(encoding="utf-8"))
    tickets = data["tickets"]
    assert len(tickets) >= 60

    categories = {ticket["category"] for ticket in tickets}
    assert categories == {"usage", "visual", "crash", "perf"}

    for ticket in tickets:
        for field in ("symptom", "root_cause", "root_cause_file", "root_cause_symbol"):
            assert ticket[field], f"{ticket['id']} 缺少 {field}"
        assert ticket["affected_versions"]

    cleaned, skipped = load_tickets(path)
    assert len(cleaned) == len(tickets)
    assert skipped == []


if __name__ == "__main__":  # pragma: no cover
    pytest.main()
