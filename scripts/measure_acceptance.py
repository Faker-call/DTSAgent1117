"""验收测量：ask / diagnose 时延与成本（无模型调用）。

用法：python scripts/measure_acceptance.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pmagent.contracts import DocRecord
from pmagent.eval.metrics import percentile
from pmagent.knowledge.corpus import DocCorpus
from pmagent.knowledge.index import IndexBuilder

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTION = "如何设置自定义地图样式"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
        env["PMAGENT_DB"] = str(tmp_path / "state.db")
        env["PMAGENT_SESSION"] = str(tmp_path / "session.json")
        env["PMAGENT_MEMORY_DB"] = str(tmp_path / "memory.db")
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("DEEPSEEK_API_KEY", None)

        def run(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [sys.executable, "-m", "pmagent", *args],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )

        run("auth", "init", "--username", "admin", "--password", "secret123")
        run("auth", "login", "--username", "admin", "--password", "secret123")

        corpus_path = tmp_path / "docs.db"
        corpus = DocCorpus(corpus_path)
        corpus.add_documents(
            [
                DocRecord(
                    id="s",
                    title="样式",
                    text=f"# 样式\n\n{QUESTION}：调用 setCustomMapStyle。\n",
                )
            ]
        )
        corpus.close()

        ask_times: list[float] = []
        for _ in range(5):
            started = time.perf_counter()
            result = run("ask", QUESTION, "--corpus", str(corpus_path), "--sdk-version", "6.0.1")
            ask_times.append((time.perf_counter() - started) * 1000)
            assert result.returncode == 0, result.stderr

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
        (repo / "a.cc").write_text(
            "namespace pm {\nint Alpha(int n) { return n + 1; }\n}\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "i"],
            capture_output=True,
            check=True,
        )
        index_root = tmp_path / "index"
        IndexBuilder(index_root).build(repo)
        stack = tmp_path / "stack.txt"
        stack.write_text("#0 0x0000000000a1b2c3 pm::Alpha at a.cc:2\n", encoding="utf-8")
        log = tmp_path / "log.txt"
        log.write_text("ERROR decode failed E1024\n", encoding="utf-8")

        diag_times: list[float] = []
        for _ in range(3):
            started = time.perf_counter()
            result = run(
                "diagnose",
                "放大后断裂",
                "--log",
                str(log),
                "--stack",
                str(stack),
                "--version",
                "6.0.1",
                "--repo",
                str(repo),
                "--index-root",
                str(index_root),
            )
            diag_times.append((time.perf_counter() - started) * 1000)
            assert result.returncode == 0, result.stderr

        print(
            json.dumps(
                {
                    "ask_runs": len(ask_times),
                    "ask_p50_ms": round(percentile(ask_times, 50)),
                    "ask_p95_ms": round(percentile(ask_times, 95)),
                    "diagnose_runs": len(diag_times),
                    "diagnose_p50_ms": round(percentile(diag_times, 50)),
                    "diagnose_p95_ms": round(percentile(diag_times, 95)),
                    "model_calls": 0,
                    "cost_cny": 0.0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
