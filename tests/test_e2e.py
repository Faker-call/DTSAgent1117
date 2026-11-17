"""端到端验收：ask / diagnose / patch 三条链路各跑通一条完整样本。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pmagent.agents import PatchAuthor, PatchStore
from pmagent.contracts import CodeLocation, DocRecord, Hypothesis
from pmagent.knowledge.corpus import DocCorpus
from pmagent.knowledge.index import IndexBuilder

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTION = "如何设置自定义地图样式"

A_CC = (
    "namespace pm {\n"
    "int Alpha(int n) { return Beta(n) + 1; }\n"
    "int Beta(int n) { return n; }\n"
    "}\n"
)

GOOD_DIFF = """--- a/a.cc
+++ b/a.cc
@@ -1,4 +1,4 @@
 namespace pm {
-int Alpha(int n) { return Beta(n) + 1; }
+int Alpha(int n) { return Beta(n) + 2; }
 int Beta(int n) { return n; }
 }
"""


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PMAGENT_DB"] = str(tmp_path / "state.db")
    env["PMAGENT_SESSION"] = str(tmp_path / "session.json")
    env["PMAGENT_MEMORY_DB"] = str(tmp_path / "memory.db")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PMAGENT_TOKEN", None)
    env.pop("DEEPSEEK_API_KEY", None)
    return env


def _run(tmp_path: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pmagent", *args],
        cwd=cwd or tmp_path,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _login(tmp_path: Path) -> None:
    assert _run(tmp_path, "auth", "init", "--username", "admin", "--password", "secret123").returncode == 0
    assert _run(tmp_path, "auth", "login", "--username", "admin", "--password", "secret123").returncode == 0


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "a.cc").write_text(A_CC, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True, check=True)
    return repo


def _corpus(tmp_path: Path) -> Path:
    path = tmp_path / "docs.db"
    corpus = DocCorpus(path)
    corpus.add_documents(
        [
            DocRecord(
                id="style",
                title="样式指南",
                url="https://example.com/style",
                version="6.0.1",
                text=f"# 样式指南\n\n{QUESTION}：调用 setCustomMapStyle。\n",
            )
        ]
    )
    corpus.close()
    return path


class FakeGenerator:
    def generate(self, context, hypothesis, count):  # noqa: ANN001
        return [GOOD_DIFF]


def test_e2e_ask_chain(tmp_path: Path) -> None:
    _login(tmp_path)
    corpus = _corpus(tmp_path)
    result = _run(
        tmp_path,
        "ask",
        QUESTION,
        "--corpus",
        str(corpus),
        "--sdk-version",
        "6.0.1",
        "--json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["conclusion"]
    assert payload["citations"]


def test_e2e_diagnose_chain(tmp_path: Path) -> None:
    _login(tmp_path)
    repo = _repo(tmp_path)
    index_root = tmp_path / "index"
    IndexBuilder(index_root).build(repo)
    stack = tmp_path / "stack.txt"
    stack.write_text("#0 0x0000000000a1b2c3 pm::Alpha at a.cc:2\n", encoding="utf-8")
    log = tmp_path / "engine.log"
    log.write_text("2026-11-17 10:00:00 ERROR decode failed E1024\n", encoding="utf-8")

    result = _run(
        tmp_path,
        "diagnose",
        "放大后路网断裂",
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
    assert result.returncode == 0, result.stderr
    reports = list((tmp_path / "artifacts" / "reports").glob("*.json"))
    assert reports
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["hypotheses"]


def test_e2e_patch_chain(tmp_path: Path) -> None:
    _login(tmp_path)
    repo = _repo(tmp_path)
    index_root = tmp_path / "index"
    IndexBuilder(index_root).build(repo)
    from pmagent.knowledge.index import CodeIndex

    index = CodeIndex.open(index_root)
    try:
        hypothesis = Hypothesis(
            id="H1",
            statement="边界未闭合",
            code_locations=[
                CodeLocation(file="a.cc", line_start=2, line_end=3, symbol="pm::Alpha")
            ],
        )
        artifacts = PatchAuthor(
            index, repo_root=repo, generator=FakeGenerator()
        ).propose(hypothesis, run_id="e2e")
    finally:
        index.close()

    store = PatchStore(tmp_path / "artifacts" / "patches")
    store.save(artifacts[0], run_id="e2e")
    applied = _run(
        tmp_path, "patch", "apply", "e2e", artifacts[0].id, "--repo", str(repo), "--yes"
    )
    assert applied.returncode == 0, applied.stderr
    assert "+ 2" in (repo / "a.cc").read_text(encoding="utf-8")

    reverted = _run(
        tmp_path, "patch", "revert", "e2e", artifacts[0].id, "--repo", str(repo)
    )
    assert reverted.returncode == 0, reverted.stderr
    assert "+ 2" not in (repo / "a.cc").read_text(encoding="utf-8")


def test_e2e_patch_generate_without_key_fails_config(tmp_path: Path) -> None:
    _login(tmp_path)
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "problem_context_ref": "p",
                "triage": {"stage": "geometry"},
                "hypotheses": [
                    {
                        "id": "H1",
                        "statement": "x",
                        "code_locations": [
                            {"file": "a.cc", "line_start": 2, "line_end": 2}
                        ],
                    }
                ],
                "top_root_cause_ref": "H1",
            }
        ),
        encoding="utf-8",
    )
    result = _run(tmp_path, "patch", "generate", "--report", str(report))
    assert result.returncode == 2
