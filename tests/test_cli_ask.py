from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pmagent.contracts import DocRecord
from pmagent.knowledge.corpus import DocCorpus

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTION = "如何设置自定义地图样式"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PMAGENT_DB"] = str(tmp_path / "state.db")
    env["PMAGENT_SESSION"] = str(tmp_path / "session.json")
    env["PMAGENT_MEMORY_DB"] = str(tmp_path / "memory.db")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PMAGENT_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-m", "pmagent", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _login(tmp_path: Path) -> None:
    assert _run(tmp_path, "auth", "init", "--username", "admin", "--password", "secret123").returncode == 0
    assert _run(tmp_path, "auth", "login", "--username", "admin", "--password", "secret123").returncode == 0


def _corpus(tmp_path: Path) -> Path:
    path = tmp_path / "docs.db"
    corpus = DocCorpus(path)
    corpus.add_documents(
        [
            DocRecord(
                id="style-guide",
                title="样式指南",
                url="https://example.com/style",
                version="6.0.1",
                text=(
                    "# 样式指南\n\n"
                    f"{QUESTION}：调用 setCustomMapStyle 并传入样式 JSON 或样式 ID。\n\n"
                    "## 示例\n\n```json\n{\"version\": 8}\n```\n"
                ),
            )
        ]
    )
    corpus.close()
    return path


def test_ask_answers_from_corpus(tmp_path: Path) -> None:
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
    )
    assert result.returncode == 0, result.stderr
    assert "结论" in result.stdout
    assert "setCustomMapStyle" in result.stdout


def test_ask_json_has_citations(tmp_path: Path) -> None:
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
    assert payload["intent"]["primary"] == "usage"
    assert payload["route"] == "qa"
    assert payload["citations"]
    assert payload["citations"][0]["ref"].startswith("https://example.com")


def test_ask_offline_uses_retrieval_only(tmp_path: Path) -> None:
    _login(tmp_path)
    corpus = _corpus(tmp_path)
    result = _run(
        tmp_path,
        "ask",
        QUESTION,
        "--corpus",
        str(corpus),
        "--offline",
    )
    assert result.returncode == 0, result.stderr
    assert "离线" in result.stdout


def test_ask_defect_routes_to_diagnose(tmp_path: Path) -> None:
    _login(tmp_path)
    stack = "#0 0x0000000000a1b2c3 pm::BuildStrip at /src/tess.cc:42"
    result = _run(tmp_path, "ask", f"崩溃了 {stack}")
    assert result.returncode == 3
    assert "diagnose" in result.stderr
