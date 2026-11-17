from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from pmagent.knowledge.index import IndexBuilder

REPO_ROOT = Path(__file__).resolve().parents[1]

LOG_TEXT = """\
2026-11-17 10:00:00.120 INFO  tile load start
2026-11-17 10:00:00.220 ERROR decode failed code=E1024
"""

STACK_TEXT = "#0 0x0000000000a1b2c3 pm::DecodeTile at tile.cc:3\n"

SOURCE = """
namespace pm {
int DecodeTile(int n) { return n + 1; }
}
"""


def _index(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "tile.cc").write_text(SOURCE, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True, check=True)
    index_root = tmp_path / "index"
    IndexBuilder(index_root).build(repo)
    return repo, index_root


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


def test_diagnose_requires_login(tmp_path: Path) -> None:
    result = _run(tmp_path, "diagnose", "地图空白")
    assert result.returncode == 6


def test_diagnose_insufficient_evidence(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(tmp_path, "diagnose", "地图空白")
    assert result.returncode == 3
    assert "证据不足" in result.stderr


def test_diagnose_with_log_is_sufficient(tmp_path: Path) -> None:
    _login(tmp_path)
    repo, index_root = _index(tmp_path)
    log = tmp_path / "engine.log"
    log.write_text(LOG_TEXT, encoding="utf-8")
    stack = tmp_path / "stack.txt"
    stack.write_text(STACK_TEXT, encoding="utf-8")
    result = _run(
        tmp_path,
        "diagnose",
        "瓦片解码失败",
        "--log",
        str(log),
        "--stack",
        str(stack),
        "--version",
        "6.0.1",
        "--platform",
        "harmony",
        "--repo",
        str(repo),
        "--index-root",
        str(index_root),
    )
    assert result.returncode == 0, result.stderr
    assert "定位报告" in result.stdout


def test_diagnose_without_index_fails(tmp_path: Path) -> None:
    _login(tmp_path)
    log = tmp_path / "engine.log"
    log.write_text(LOG_TEXT, encoding="utf-8")
    result = _run(
        tmp_path,
        "diagnose",
        "瓦片解码失败",
        "--log",
        str(log),
        "--version",
        "6.0.1",
        "--index-root",
        str(tmp_path / "missing-index"),
    )
    assert result.returncode == 4
    assert "索引尚未构建" in result.stderr


def test_diagnose_json_output(tmp_path: Path) -> None:
    _login(tmp_path)
    repo, index_root = _index(tmp_path)
    log = tmp_path / "engine.log"
    log.write_text(LOG_TEXT, encoding="utf-8")
    stack = tmp_path / "stack.txt"
    stack.write_text(STACK_TEXT, encoding="utf-8")
    result = _run(
        tmp_path,
        "diagnose",
        "瓦片解码失败",
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
        "--json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["hypotheses"]
    assert payload["triage"]["stage"]
