"""索引存储：SQLite schema、全量/增量构建、manifest、快照与版本守卫。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pmagent.authz.locks import SingleWriterLock
from pmagent.errors import PmagentError
from pmagent.knowledge.index.chunker import CHUNK_POLICY_VERSION, build_chunks
from pmagent.knowledge.index.discovery import discover_files
from pmagent.knowledge.index.models import ParsedFile
from pmagent.knowledge.index.parser import parse_source

SCHEMA_VERSION = "1"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    sha TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'cpp',
    loc INTEGER NOT NULL DEFAULT 0,
    git_commit TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    signature TEXT NOT NULL DEFAULT '',
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    parent TEXT,
    namespace TEXT NOT NULL DEFAULT '',
    class_name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_symbol TEXT NOT NULL,
    to_name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'call',
    line INTEGER NOT NULL DEFAULT 0,
    confidence TEXT NOT NULL DEFAULT 'heuristic'
);
CREATE TABLE IF NOT EXISTS includes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    target TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    symbol_qualified TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'function',
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_estimate INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chunks_vec (
    chunk_id INTEGER PRIMARY KEY,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS commits (
    sha TEXT PRIMARY KEY,
    parent TEXT NOT NULL DEFAULT '',
    author_date TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    files_changed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS index_errors (
    path TEXT PRIMARY KEY,
    error TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_chunks_symbol ON chunks(symbol_qualified);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path);
CREATE INDEX IF NOT EXISTS idx_refs_to ON refs(to_name);
"""


class IndexVersionMismatch(PmagentError):
    """索引与当前运行时版本不兼容时拒绝查询。"""


@dataclass
class EmbeddingMeta:
    model_id: str = "qwen3.7-text-embedding"
    model_version: str = "unknown"
    dim: int = 1024


@dataclass
class Manifest:
    schema_version: str
    chunk_policy_version: str
    embedding_model_id: str
    embedding_model_version: str
    embedding_dim: int
    commit: str
    built_at: str
    file_count: int = 0
    symbol_count: int = 0
    chunk_count: int = 0
    compile_commands_available: bool = False
    errors: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Manifest:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def resolve_commit(repo_root: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    return _git(repo_root, "rev-parse", "HEAD") or "no-git"


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


class IndexBuilder:
    def __init__(
        self,
        index_root: str | Path,
        *,
        keep_snapshots: int = 3,
        embedding: EmbeddingMeta | None = None,
        excludes: tuple[str, ...] | None = None,
    ) -> None:
        self.index_root = Path(index_root)
        self.keep_snapshots = max(1, keep_snapshots)
        self.embedding = embedding or EmbeddingMeta()
        self.excludes = excludes

    # -- 全量 -------------------------------------------------------------
    def build(self, repo_root: str | Path, commit: str | None = None) -> Manifest:
        """串行化构建：同一索引目录同时只允许一个构建者。"""
        self.index_root.mkdir(parents=True, exist_ok=True)
        lock = SingleWriterLock(self.index_root / ".build.lock")
        lock.acquire(timeout=0)
        try:
            return self._build_impl(repo_root, commit)
        finally:
            lock.release()

    def _build_impl(self, repo_root: str | Path, commit: str | None = None) -> Manifest:
        repo_root = Path(repo_root)
        commit = resolve_commit(repo_root, commit)
        snapshot = self.snapshot_dir(commit)
        if snapshot.exists():
            shutil.rmtree(snapshot)
        snapshot.mkdir(parents=True, exist_ok=True)
        db_path = snapshot / "index.db"

        if self.excludes is None:
            files, stats = discover_files(repo_root)
        else:
            files, stats = discover_files(repo_root, excludes=self.excludes)

        parsed_files: list[ParsedFile] = []
        errors: dict[str, str] = {}
        for rel in files:
            path = repo_root / rel
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                errors[rel] = f"读取失败：{exc}"
                continue
            parsed = parse_source(rel, text)
            if parsed.parse_error:
                errors[rel] = parsed.parse_error
            parsed_files.append(parsed)

        manifest = self._write_snapshot(
            db_path, repo_root, parsed_files, commit, errors=errors
        )
        manifest.extra["discovery"] = {str(key): value for key, value in stats.items()}
        manifest.save(snapshot / "manifest.json")
        (self.index_root / "current").write_text(commit[:12], encoding="utf-8")
        self._prune()
        return manifest

    # -- 增量 -------------------------------------------------------------
    def update(self, repo_root: str | Path, commit: str | None = None) -> Manifest:
        repo_root = Path(repo_root)
        current = self.current_manifest()
        if current is None:
            return self.build(repo_root, commit)
        new_commit = resolve_commit(repo_root, commit)
        if new_commit == current.commit:
            return current

        changed = _changed_files(repo_root, current.commit, new_commit)
        if changed is None:
            return self.build(repo_root, commit)

        old_snapshot = self.snapshot_dir(current.commit)
        new_snapshot = self.snapshot_dir(new_commit)
        if new_snapshot.exists():
            shutil.rmtree(new_snapshot)
        shutil.copytree(old_snapshot, new_snapshot)
        db_path = new_snapshot / "index.db"

        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(SCHEMA_SQL)
            _ensure_fts(conn)
            expanded = sorted(set(changed) | set(_dependents(conn, changed)))
            for rel in expanded:
                _delete_file_rows(conn, rel)
                path = repo_root / rel
                if not path.exists():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                parsed = parse_source(rel, text)
                _insert_file(conn, repo_root, parsed, text, new_commit)
                conn.execute(
                    "INSERT OR REPLACE INTO index_errors (path, error) VALUES (?,?)",
                    (rel, parsed.parse_error or ""),
                )
                if not parsed.parse_error:
                    conn.execute("DELETE FROM index_errors WHERE path = ?", (rel,))
            _insert_commits(conn, repo_root)
            counts = _counts(conn)
            conn.commit()
        finally:
            conn.close()

        manifest = Manifest(
            schema_version=SCHEMA_VERSION,
            chunk_policy_version=CHUNK_POLICY_VERSION,
            embedding_model_id=self.embedding.model_id,
            embedding_model_version=self.embedding.model_version,
            embedding_dim=self.embedding.dim,
            commit=new_commit,
            built_at=datetime.now(UTC).isoformat(),
            file_count=counts["files"],
            symbol_count=counts["symbols"],
            chunk_count=counts["chunks"],
            compile_commands_available=_has_compile_commands(repo_root),
            errors=counts["errors"],
        )
        manifest.extra = dict(current.extra)
        manifest.extra["incremental_from"] = current.commit
        manifest.extra["changed_files"] = len(expanded)
        manifest.save(new_snapshot / "manifest.json")
        (self.index_root / "current").write_text(new_commit[:12], encoding="utf-8")
        self._prune()
        return manifest

    # -- 快照管理 ---------------------------------------------------------
    def snapshot_dir(self, commit: str) -> Path:
        return self.index_root / commit[:12]

    def current_commit(self) -> str | None:
        pointer = self.index_root / "current"
        if not pointer.exists():
            return None
        name = pointer.read_text(encoding="utf-8").strip()
        return name or None

    def current_manifest(self) -> Manifest | None:
        commit = self.current_commit()
        if not commit:
            return None
        manifest_path = self.index_root / commit / "manifest.json"
        if not manifest_path.exists():
            return None
        return Manifest.load(manifest_path)

    def _prune(self) -> None:
        snapshots = sorted(
            [p for p in self.index_root.iterdir() if p.is_dir() and (p / "manifest.json").exists()],
            key=lambda p: (p / "manifest.json").stat().st_mtime,
            reverse=True,
        )
        current = self.current_commit()
        for path in snapshots[self.keep_snapshots :]:
            if path.name == current:
                continue
            shutil.rmtree(path, ignore_errors=True)

    # -- 写入 -------------------------------------------------------------
    def _write_snapshot(
        self,
        db_path: Path,
        repo_root: Path,
        parsed_files: list[ParsedFile],
        commit: str,
        *,
        errors: dict[str, str] | None = None,
    ) -> Manifest:
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(SCHEMA_SQL)
            _ensure_fts(conn)
            for parsed in parsed_files:
                path = repo_root / parsed.path
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                _insert_file(conn, repo_root, parsed, text, commit)
            _insert_commits(conn, repo_root)
            for rel, error in (errors or {}).items():
                if error:
                    conn.execute(
                        "INSERT OR REPLACE INTO index_errors (path, error) VALUES (?,?)",
                        (rel, error),
                    )
            counts = _counts(conn)
            conn.commit()
        finally:
            conn.close()

        return Manifest(
            schema_version=SCHEMA_VERSION,
            chunk_policy_version=CHUNK_POLICY_VERSION,
            embedding_model_id=self.embedding.model_id,
            embedding_model_version=self.embedding.model_version,
            embedding_dim=self.embedding.dim,
            commit=commit,
            built_at=datetime.now(UTC).isoformat(),
            file_count=counts["files"],
            symbol_count=counts["symbols"],
            chunk_count=counts["chunks"],
            compile_commands_available=_has_compile_commands(repo_root),
            errors=counts["errors"],
        )


def _ensure_fts(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts"
            " USING fts5(chunk_id UNINDEXED, text)"
        )
        return True
    except sqlite3.OperationalError:  # pragma: no cover - FTS5 不可用
        return False


def _has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE name = 'chunks_fts'"
    ).fetchone()
    return row is not None


def _insert_commits(conn: sqlite3.Connection, repo_root: Path, limit: int = 500) -> None:
    output = _git(
        repo_root, "log", f"-n{limit}", "--pretty=format:%H%x09%P%x09%aI%x09%s"
    )
    if not output:
        return
    rows = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        sha, parent, author_date, subject = parts[0], parts[1], parts[2], "\t".join(parts[3:])
        rows.append((sha, parent, author_date, subject, 0))
    conn.executemany(
        "INSERT OR REPLACE INTO commits (sha, parent, author_date, subject, files_changed)"
        " VALUES (?,?,?,?,?)",
        rows,
    )


def _dependents(conn: sqlite3.Connection, changed: list[str]) -> list[str]:
    """被变更头文件直接包含的源文件（一跳反向依赖）。"""
    dependents: set[str] = set()
    for rel in changed:
        name = Path(rel).name
        rows = conn.execute(
            "SELECT DISTINCT source_path FROM includes WHERE target = ? OR target LIKE ?",
            (rel, f"%{name}"),
        ).fetchall()
        dependents.update(row[0] for row in rows)
    return sorted(dependents - set(changed))


def _has_compile_commands(repo_root: Path) -> bool:
    return any(
        (repo_root / candidate).exists()
        for candidate in ("compile_commands.json", "build/compile_commands.json")
    )


def _changed_files(repo_root: Path, old_commit: str, new_commit: str) -> list[str] | None:
    output = _git(repo_root, "diff", "--name-only", f"{old_commit}..{new_commit}")
    if output is None:
        return None
    return [line.strip() for line in output.splitlines() if line.strip()]


def _delete_file_rows(conn: sqlite3.Connection, rel: str) -> None:
    conn.execute(
        "DELETE FROM refs WHERE from_symbol IN"
        " (SELECT qualified_name FROM symbols WHERE file_path = ?)",
        (rel,),
    )
    conn.execute("DELETE FROM files WHERE path = ?", (rel,))
    conn.execute("DELETE FROM symbols WHERE file_path = ?", (rel,))
    conn.execute("DELETE FROM includes WHERE source_path = ?", (rel,))
    if _has_fts(conn):
        conn.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN"
            " (SELECT id FROM chunks WHERE file_path = ?)",
            (rel,),
        )
    conn.execute(
        "DELETE FROM chunks_vec WHERE chunk_id IN (SELECT id FROM chunks WHERE file_path = ?)",
        (rel,),
    )
    conn.execute("DELETE FROM chunks WHERE file_path = ?", (rel,))
    conn.execute("DELETE FROM index_errors WHERE path = ?", (rel,))


def _insert_file(
    conn: sqlite3.Connection, repo_root: Path, parsed: ParsedFile, text: str, commit: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO files (path, sha, lang, loc, git_commit) VALUES (?,?,?,?,?)",
        (parsed.path, _sha1(text), "cpp", len(text.splitlines()), commit),
    )
    conn.executemany(
        "INSERT INTO symbols (file_path, name, qualified_name, kind, signature,"
        " line_start, line_end, parent, namespace, class_name)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                parsed.path,
                symbol.name,
                symbol.qualified_name,
                symbol.kind,
                symbol.signature,
                symbol.line_start,
                symbol.line_end,
                symbol.parent,
                symbol.namespace,
                symbol.class_name,
            )
            for symbol in parsed.symbols
        ],
    )
    conn.executemany(
        "INSERT INTO refs (from_symbol, to_name, kind, line, confidence) VALUES (?,?,?,?,?)",
        [
            (ref.from_symbol, ref.to_name, ref.kind, ref.line, ref.confidence)
            for ref in parsed.refs
        ],
    )
    conn.executemany(
        "INSERT INTO includes (source_path, target) VALUES (?,?)",
        [(edge.source_path, edge.target) for edge in parsed.includes],
    )
    chunks = build_chunks(parsed, text)
    fts_enabled = _has_fts(conn)
    for chunk in chunks:
        cursor = conn.execute(
            "INSERT INTO chunks (file_path, symbol_qualified, kind, line_start, line_end,"
            " text, token_estimate) VALUES (?,?,?,?,?,?,?)",
            (
                chunk.path,
                chunk.symbol_qualified,
                chunk.kind,
                chunk.line_start,
                chunk.line_end,
                chunk.text,
                chunk.token_estimate,
            ),
        )
        if fts_enabled:
            conn.execute(
                "INSERT INTO chunks_fts (chunk_id, text) VALUES (?,?)",
                (cursor.lastrowid, chunk.text),
            )


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "files": int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
        "symbols": int(conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]),
        "chunks": int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]),
        "errors": int(
            conn.execute("SELECT COUNT(*) FROM index_errors WHERE error != ''").fetchone()[0]
        ),
    }


class CodeIndex:
    """只读视图；打开时做版本守卫。"""

    def __init__(
        self, snapshot_dir: Path, conn: sqlite3.Connection, manifest: Manifest
    ) -> None:
        self.snapshot_dir = snapshot_dir
        self.conn = conn
        self.manifest = manifest

    @classmethod
    def open(
        cls,
        index_root: str | Path,
        *,
        commit: str | None = None,
        expected_schema: str = SCHEMA_VERSION,
        expected_chunk_policy: str = CHUNK_POLICY_VERSION,
        expected_embedding: EmbeddingMeta | None = None,
    ) -> CodeIndex:
        root = Path(index_root)
        if commit is None:
            pointer = root / "current"
            if not pointer.exists():
                raise IndexVersionMismatch(
                    "索引不存在", hint="先执行 pmagent index build"
                )
            commit = pointer.read_text(encoding="utf-8").strip()
        snapshot = root / commit[:12]
        manifest_path = snapshot / "manifest.json"
        if not manifest_path.exists():
            raise IndexVersionMismatch(
                f"索引快照缺失：{snapshot}", hint="重新执行 pmagent index build"
            )
        manifest = Manifest.load(manifest_path)
        cls._guard(manifest, expected_schema, expected_chunk_policy, expected_embedding)
        conn = sqlite3.connect(f"file:{snapshot / 'index.db'}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return cls(snapshot, conn, manifest)

    @staticmethod
    def _guard(
        manifest: Manifest,
        schema: str,
        chunk_policy: str,
        embedding: EmbeddingMeta | None,
    ) -> None:
        problems: list[str] = []
        if manifest.schema_version != schema:
            problems.append(f"schema {manifest.schema_version} != {schema}")
        if manifest.chunk_policy_version != chunk_policy:
            problems.append(f"chunk_policy {manifest.chunk_policy_version} != {chunk_policy}")
        if embedding is not None:
            if manifest.embedding_model_id != embedding.model_id:
                problems.append(
                    f"embedding {manifest.embedding_model_id} != {embedding.model_id}"
                )
            if manifest.embedding_dim != embedding.dim:
                problems.append(f"embedding_dim {manifest.embedding_dim} != {embedding.dim}")
        if problems:
            raise IndexVersionMismatch(
                "索引与运行时版本不一致：" + "; ".join(problems),
                hint="重新执行 pmagent index build（向量空间不兼容时不可降级使用）",
            )

    def counts(self) -> dict[str, int]:
        return _counts(self.conn)

    def find_symbols(self, name: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM symbols WHERE qualified_name = ? OR name = ?"
            " ORDER BY qualified_name LIMIT 50",
            (name, name),
        ).fetchall()
        return [dict(row) for row in rows]

    def chunks_for(self, qualified_name: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM chunks WHERE symbol_qualified = ? ORDER BY line_start",
            (qualified_name,),
        ).fetchall()
        return [dict(row) for row in rows]

    def errors(self) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT path, error FROM index_errors WHERE error != '' ORDER BY path"
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.conn.close()
