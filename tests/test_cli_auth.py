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


def _bootstrap(tmp_path: Path) -> None:
    result = _run(tmp_path, "auth", "init", "--username", "admin", "--password", "secret123")
    assert result.returncode == 0, result.stderr


def test_init_once_then_rejected(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    second = _run(tmp_path, "auth", "init", "--username", "admin", "--password", "secret123")
    assert second.returncode == 6
    assert "拒绝重复初始化" in second.stderr


def test_whoami_requires_login(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    result = _run(tmp_path, "auth", "whoami")
    assert result.returncode == 6
    assert "未登录" in result.stderr


def test_login_and_whoami(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    assert _run(tmp_path, "auth", "login", "--username", "admin", "--password", "secret123").returncode == 0
    result = _run(tmp_path, "auth", "whoami")
    assert result.returncode == 0
    assert "admin" in result.stdout


def test_wrong_password_rejected(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    result = _run(tmp_path, "auth", "login", "--username", "admin", "--password", "wrong-pass")
    assert result.returncode == 6


def test_viewer_cannot_build_index(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _run(tmp_path, "auth", "login", "--username", "admin", "--password", "secret123")
    added = _run(
        tmp_path,
        "auth",
        "user",
        "add",
        "--username",
        "bob",
        "--role",
        "viewer",
        "--password",
        "secret123",
    )
    assert added.returncode == 0, added.stderr
    _run(tmp_path, "auth", "logout")
    assert _run(tmp_path, "auth", "login", "--username", "bob", "--password", "secret123").returncode == 0

    denied = _run(tmp_path, "index", "build")
    assert denied.returncode == 6
    assert "index.build" in denied.stderr
    assert "maintainer" in denied.stderr


def test_viewer_can_read_index_status(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _run(tmp_path, "auth", "login", "--username", "admin", "--password", "secret123")
    _run(tmp_path, "auth", "user", "add", "--username", "bob", "--role", "viewer", "--password", "secret123")
    _run(tmp_path, "auth", "logout")
    _run(tmp_path, "auth", "login", "--username", "bob", "--password", "secret123")
    index_root = tmp_path / "index"
    status = _run(tmp_path, "index", "status", "--index-root", str(index_root))
    # 已通过授权，索引尚未构建
    assert status.returncode == 0
    assert "尚未构建" in status.stdout


def test_non_admin_cannot_manage_users(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _run(tmp_path, "auth", "login", "--username", "admin", "--password", "secret123")
    _run(tmp_path, "auth", "user", "add", "--username", "dev", "--role", "developer", "--password", "secret123")
    _run(tmp_path, "auth", "logout")
    _run(tmp_path, "auth", "login", "--username", "dev", "--password", "secret123")

    result = _run(
        tmp_path,
        "auth",
        "user",
        "add",
        "--username",
        "eve",
        "--role",
        "viewer",
        "--password",
        "secret123",
    )
    assert result.returncode == 6
    assert "user.manage" in result.stderr


def test_admin_can_change_roles(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    _run(tmp_path, "auth", "login", "--username", "admin", "--password", "secret123")
    _run(tmp_path, "auth", "user", "add", "--username", "bob", "--role", "viewer", "--password", "secret123")
    changed = _run(tmp_path, "auth", "user", "role", "--username", "bob", "--roles", "maintainer")
    assert changed.returncode == 0
    _run(tmp_path, "auth", "logout")
    _run(tmp_path, "auth", "login", "--username", "bob", "--password", "secret123")
    # 维护者可以构建索引（空目录也会成功建库）
    built = _run(
        tmp_path,
        "index",
        "build",
        "--repo",
        str(tmp_path),
        "--index-root",
        str(tmp_path / "index"),
    )
    assert built.returncode == 0, built.stderr
