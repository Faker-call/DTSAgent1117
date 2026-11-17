"""索引性能基准：全量构建耗时、吞吐与外推。

用法：python scripts/index_bench.py <repo> <index_root>
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

from pmagent.knowledge.index import IndexBuilder


def main() -> int:
    repo = Path(sys.argv[1] if len(sys.argv) > 1 else "corpus/maplibre-native")
    root = Path(sys.argv[2] if len(sys.argv) > 2 else "corpus/.index")
    started = time.perf_counter()
    manifest = IndexBuilder(root, keep_snapshots=1).build(repo)
    elapsed = time.perf_counter() - started

    db_path = root / manifest.commit[:12] / "index.db"
    conn = sqlite3.connect(db_path)
    try:
        loc = int(conn.execute("SELECT COALESCE(SUM(loc), 0) FROM files").fetchone()[0])
    finally:
        conn.close()

    size_mb = db_path.stat().st_size / (1024 * 1024)
    report = {
        "repo": str(repo),
        "commit": manifest.commit[:12],
        "files": manifest.file_count,
        "symbols": manifest.symbol_count,
        "chunks": manifest.chunk_count,
        "errors": manifest.errors,
        "lines": loc,
        "seconds": round(elapsed, 2),
        "lines_per_second": int(loc / elapsed) if elapsed > 0 else 0,
        "db_mb": round(size_mb, 1),
        "extrapolated_1m_seconds": round(elapsed * 1_000_000 / loc, 1) if loc else None,
        "compile_commands_available": manifest.compile_commands_available,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
