from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_ask_session_records_slots(tmp_path: Path) -> None:
    _login(tmp_path)
    first = _run(
        tmp_path, "ask", "--session", "s1", "--sdk-version", "6.0.1", "--platform", "harmony"
    )
    assert first.returncode == 0, first.stderr
    assert "6.0.1" in first.stdout

    second = _run(tmp_path, "ask", "--session", "s1")
    assert second.returncode == 0
    assert "6.0.1" in second.stdout
    assert "harmony" in second.stdout


def test_ask_new_session_has_no_slots(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(tmp_path, "ask", "--session", "fresh")
    assert result.returncode == 0
    assert "未知" in result.stdout


def test_ask_with_question_needs_corpus(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(
        tmp_path,
        "ask",
        "如何设置自定义地图样式",
        "--session",
        "s1",
        "--non-interactive",
    )
    assert result.returncode == 3
    assert "未配置知识库" in result.stderr


def test_ask_requires_login(tmp_path: Path) -> None:
    result = _run(tmp_path, "ask", "--session", "s1")
    assert result.returncode == 6


def test_runs_show_missing_run(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(tmp_path, "runs", "show", "no-such-run")
    assert result.returncode == 1
    assert "运行不存在" in result.stderr


def test_runs_list_empty(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(tmp_path, "runs", "list")
    assert result.returncode == 0
    assert "暂无运行记录" in result.stdout


def test_review_requires_flag(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(tmp_path, "review", "--run-id", "r1")
    assert result.returncode == 2


def test_memory_list_empty(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(tmp_path, "memory", "list")
    assert result.returncode == 0
    assert "暂无案例" in result.stdout
