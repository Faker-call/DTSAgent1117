from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("PMAGENT_TEST_HOOKS", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_env:
        env.update(extra_env)
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


def test_help_exits_zero() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "config" in result.stdout


def test_version_exits_zero() -> None:
    result = _run("--version")
    assert result.returncode == 0
    assert "0.1.0" in result.stdout


def test_unknown_command_exits_two() -> None:
    result = _run("no-such-command")
    assert result.returncode == 2


def test_config_show_json_exits_zero() -> None:
    result = _run("config", "show", "--json")
    assert result.returncode == 0
    assert "deepseek-flash" in result.stdout


def test_missing_option_exits_two() -> None:
    result = _run("config", "init", "--path")
    assert result.returncode == 2


def test_placeholder_command_exits_one() -> None:
    result = _run("explain")
    assert result.returncode == 1
    assert "尚未实现" in result.stderr


def test_fault_exit_codes() -> None:
    env = {"PMAGENT_TEST_HOOKS": "1"}
    for code in (3, 4, 5, 6, 130):
        result = _run("_raise", "--code", str(code), extra_env=env)
        assert result.returncode == code, (code, result.returncode, result.stderr)


def test_fault_hook_disabled_without_env() -> None:
    result = _run("_raise", "--code", "3")
    assert result.returncode == 2
