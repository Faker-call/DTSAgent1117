from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from pmagent.errors import PmagentError
from pmagent.knowledge.index import (
    CodeIndex,
    EmbeddingMeta,
    IndexBuilder,
    IndexVersionMismatch,
    pack_index,
    unpack_index,
)
from pmagent.knowledge.index.store import SCHEMA_VERSION

A_CC = """
namespace pm {
int Add(int a, int b) { return a + b; }
}
"""

B_CC = """
namespace pm {
int Sub(int a, int b) { return a - b; }
}
"""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "a.cc").write_text(A_CC, encoding="utf-8")
    (repo / "b.cc").write_text(B_CC, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _symbol_names(snapshot: Path) -> list[str]:
    conn = sqlite3.connect(snapshot / "index.db")
    try:
        rows = conn.execute("SELECT qualified_name FROM symbols ORDER BY qualified_name").fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def test_build_creates_manifest_and_symbols(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = tmp_path / "index"
    manifest = IndexBuilder(root).build(repo)
    assert manifest.file_count == 2
    assert manifest.symbol_count >= 2
    assert manifest.chunk_count >= 2
    assert manifest.schema_version == SCHEMA_VERSION
    assert manifest.compile_commands_available is False

    index = CodeIndex.open(root)
    try:
        assert index.find_symbols("pm::Add")
        assert index.chunks_for("pm::Add")
        assert index.counts()["files"] == 2
    finally:
        index.close()


def test_version_guard_rejects_mismatch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = tmp_path / "index"
    IndexBuilder(root).build(repo)
    with pytest.raises(IndexVersionMismatch):
        CodeIndex.open(root, expected_chunk_policy="999")
    with pytest.raises(IndexVersionMismatch):
        CodeIndex.open(
            root,
            expected_embedding=EmbeddingMeta(model_id="other-model", dim=1024),
        )


def test_incremental_matches_full_rebuild(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = tmp_path / "index"
    builder = IndexBuilder(root)
    builder.build(repo)

    (repo / "a.cc").write_text(A_CC + "\nnamespace pm { int Mul(int a, int b) { return a * b; } }\n")
    new_commit = _commit(repo, "add Mul")
    incremental = builder.update(repo)

    fresh_root = tmp_path / "index_full"
    full = IndexBuilder(fresh_root).build(repo)

    assert incremental.commit == new_commit
    assert incremental.symbol_count == full.symbol_count
    assert incremental.chunk_count == full.chunk_count
    assert _symbol_names(root / new_commit[:12]) == _symbol_names(fresh_root / new_commit[:12])
    assert "pm::Mul" in _symbol_names(root / new_commit[:12])


def test_incremental_handles_deleted_file(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = tmp_path / "index"
    builder = IndexBuilder(root)
    builder.build(repo)
    (repo / "b.cc").unlink()
    new_commit = _commit(repo, "remove b")
    manifest = builder.update(repo)
    assert manifest.file_count == 1
    assert "pm::Sub" not in _symbol_names(root / new_commit[:12])


def test_prune_keeps_recent_snapshots(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = tmp_path / "index"
    builder = IndexBuilder(root, keep_snapshots=2)
    for index in range(4):
        (repo / "a.cc").write_text(A_CC + f"\n// rev {index}\n", encoding="utf-8")
        _commit(repo, f"rev {index}")
        builder.build(repo)
    snapshots = [p for p in root.iterdir() if p.is_dir()]
    assert len(snapshots) <= 2


def test_pack_and_unpack_roundtrip(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = tmp_path / "index"
    manifest = IndexBuilder(root).build(repo)

    archive, digest = pack_index(root, manifest.commit, tmp_path / "packages")
    assert archive.exists()
    assert len(digest) == 64

    restored = tmp_path / "restored"
    commit = unpack_index(archive, restored)
    assert commit == manifest.commit[:12]
    index = CodeIndex.open(restored)
    try:
        assert index.find_symbols("pm::Add")
    finally:
        index.close()


def test_unpack_rejects_tampered_archive(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = tmp_path / "index"
    manifest = IndexBuilder(root).build(repo)
    archive, _digest = pack_index(root, manifest.commit, tmp_path / "packages")

    tampered = tmp_path / "tampered.tar.gz"
    tampered.write_bytes(archive.read_bytes() + b"x")
    with pytest.raises(PmagentError):
        unpack_index(tampered, tmp_path / "restored", expected_sha256="0" * 64)


def test_discovery_stats_recorded(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / "vendor").mkdir()
    (repo / "vendor" / "x.cc").write_text("int x;", encoding="utf-8")
    _commit(repo, "add vendor")
    manifest = IndexBuilder(tmp_path / "index").build(repo)
    assert manifest.extra["discovery"]["excluded"] == 1
    assert manifest.file_count == 2


def test_incremental_includes_reverse_dependencies(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "inc.h").write_text("int Helper();\n", encoding="utf-8")
    (repo / "a.cc").write_text('#include "inc.h"\nint Use() { return Helper(); }\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    root = tmp_path / "index"
    builder = IndexBuilder(root)
    builder.build(repo)

    (repo / "inc.h").write_text("int Helper();\nint Helper2();\n", encoding="utf-8")
    new_commit = _commit(repo, "header change")
    manifest = builder.update(repo)

    assert manifest.extra["changed_files"] >= 2
    names = _symbol_names(root / new_commit[:12])
    assert "Helper2" in names


def test_chunks_fts_populated_when_available(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    root = tmp_path / "index"
    manifest = IndexBuilder(root).build(repo)
    conn = sqlite3.connect(root / manifest.commit[:12] / "index.db")
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE name='chunks_fts'"
        ).fetchone()
        if exists is None:
            pytest.skip("当前 SQLite 未启用 FTS5")
        count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        assert count > 0
        hit = conn.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH 'Add' LIMIT 1"
        ).fetchone()
        assert hit is not None
    finally:
        conn.close()
