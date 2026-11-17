from __future__ import annotations

from pathlib import Path

from pmagent.knowledge.index import build_chunks, discover_files, parse_source

SAMPLE = r"""
#include <vector>
#define TILE_SIZE 256

namespace pm {
namespace render {
class Tessellator {
public:
    void BuildStrip(const std::vector<float>& pts);
    int count_ = 0;
};
void Tessellator::BuildStrip(const std::vector<float>& pts) {
    for (auto p : pts) { Draw(p); }
}
}
}
"""


def test_parse_symbols_and_includes() -> None:
    parsed = parse_source("src/tess.cc", SAMPLE)
    qualified = {s.qualified_name for s in parsed.symbols}
    assert "pm" in qualified
    assert "pm::render" in qualified
    assert "pm::render::Tessellator" in qualified
    assert "pm::render::Tessellator::BuildStrip" in qualified
    assert "TILE_SIZE" in qualified
    assert any(edge.target == "vector" for edge in parsed.includes)


def test_parse_calls() -> None:
    parsed = parse_source("src/tess.cc", SAMPLE)
    calls = [ref for ref in parsed.refs if ref.to_name == "Draw"]
    assert calls
    assert calls[0].from_symbol.endswith("BuildStrip")


def test_parse_kinds() -> None:
    parsed = parse_source("src/tess.cc", SAMPLE)
    kinds = {s.kind for s in parsed.symbols}
    assert {"namespace", "class", "declaration", "function", "macro"} <= kinds


def test_parse_syntax_error_is_recorded() -> None:
    parsed = parse_source("broken.cc", "void Broken( {")
    assert parsed.parse_error is not None


def test_chunk_includes_prefix_and_lines() -> None:
    parsed = parse_source("src/tess.cc", SAMPLE)
    chunks = build_chunks(parsed, SAMPLE)
    function_chunk = next(c for c in chunks if c.kind == "function")
    assert "// file: src/tess.cc" in function_chunk.text
    assert "// symbol: pm::render::Tessellator::BuildStrip" in function_chunk.text
    assert "// signature: Tessellator::BuildStrip" in function_chunk.text
    assert function_chunk.token_estimate > 0


def test_chunk_long_function_splits_with_prefix() -> None:
    body = "\n".join(f"    int v{i} = {i};" for i in range(400))
    source = f"void Big() {{\n{body}\n}}\n"
    parsed = parse_source("big.cc", source)
    chunks = [c for c in build_chunks(parsed, source) if c.kind == "function_part"]
    assert len(chunks) > 1
    assert all("// signature:" in chunk.text for chunk in chunks)


def test_chunk_class_declaration_excludes_member_body() -> None:
    parsed = parse_source("src/tess.cc", SAMPLE)
    chunks = build_chunks(parsed, SAMPLE)
    class_chunk = next(c for c in chunks if c.kind == "class")
    assert "class Tessellator" in class_chunk.text
    assert "Draw(p)" not in class_chunk.text


def test_chunk_macro() -> None:
    parsed = parse_source("src/tess.cc", SAMPLE)
    chunks = build_chunks(parsed, SAMPLE)
    macro = next(c for c in chunks if c.kind == "macro")
    assert "TILE_SIZE" in macro.text


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discovery_filters_extensions_and_vendor(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.cc", "int a;")
    _write(tmp_path, "src/b.hpp", "int b;")
    _write(tmp_path, "docs/readme.md", "# doc")
    _write(tmp_path, "vendor/lib/c.cc", "int c;")
    files, stats = discover_files(tmp_path)
    assert files == ["src/a.cc", "src/b.hpp"]
    assert stats["non_cpp"] == 1
    assert stats["excluded"] == 1


def test_discovery_uses_git_when_available(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    _write(tmp_path, "src/a.cc", "int a;")
    _write(tmp_path, "ignored.cc", "int ignored;")
    (tmp_path / ".gitignore").write_text("ignored.cc\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
    files, _stats = discover_files(tmp_path)
    assert "src/a.cc" in files
    assert "ignored.cc" not in files
