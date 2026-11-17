from __future__ import annotations

import sys
from pathlib import Path

import yaml

from pmagent.evidence import ReproSandbox, load_repro_config


def _project(tmp_path: Path, run_cmd: str, name: str = "proj", build_cmd: str | None = None) -> Path:
    project = tmp_path / name
    project.mkdir()
    payload: dict[str, object] = {"timeout": 5, "run": run_cmd}
    if build_cmd:
        payload["build"] = build_cmd
    (project / "pmagent.yaml").write_text(
        yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8"
    )
    return project


def _py(code: str) -> str:
    return f'"{sys.executable}" -c "{code}"'


def test_load_config(tmp_path: Path) -> None:
    project = _project(tmp_path, "echo run", build_cmd="echo build")
    config = load_repro_config(project)
    assert config.build == "echo build"
    assert config.run == "echo run"
    assert config.timeout == 5


def test_missing_config_is_blocked(tmp_path: Path) -> None:
    project = tmp_path / "empty"
    project.mkdir()
    result = ReproSandbox(docker="").run(project)
    assert result.status == "blocked"
    assert "未提供构建或运行命令" in result.warnings[0]


def test_missing_project_is_blocked(tmp_path: Path) -> None:
    result = ReproSandbox(docker="").run(tmp_path / "nope")
    assert result.status == "blocked"


def test_local_success(tmp_path: Path) -> None:
    project = _project(tmp_path, _py("print('ok')"))
    result = ReproSandbox(docker="").run(project)
    assert result.status == "success"
    assert result.isolation == "process"
    assert "ok" in (result.run.stdout if result.run else "")
    assert any("未检测到 Docker" in warning for warning in result.warnings)
    assert result.environment_diff["docker_available"] is False


def test_local_failure_exit_code(tmp_path: Path) -> None:
    project = _project(tmp_path, _py("import sys; sys.exit(3)"))
    result = ReproSandbox(docker="").run(project)
    assert result.status == "failed"
    assert result.run is not None and result.run.exit_code == 3


def test_local_timeout(tmp_path: Path) -> None:
    project = _project(tmp_path, _py("import time; time.sleep(10)"))
    result = ReproSandbox(docker="").run(project)
    assert result.status == "timeout"
    assert result.run is not None and result.run.exit_code is None


def test_build_failure_short_circuits_run(tmp_path: Path) -> None:
    project = _project(tmp_path, _py("print('run')"), build_cmd=_py("import sys; sys.exit(2)"))
    result = ReproSandbox(docker="").run(project)
    assert result.status == "failed"
    assert result.build is not None and result.build.exit_code == 2
    assert result.run is None


def test_success_rate_over_five_projects(tmp_path: Path) -> None:
    projects = [
        _project(tmp_path, _py("print(1)"), "p1"),
        _project(tmp_path, _py("print(2)"), "p2"),
        _project(tmp_path, _py("print(3)"), "p3"),
        _project(tmp_path, _py("import sys; sys.exit(4)"), "p4"),
        _project(tmp_path, _py("import time; time.sleep(10)"), "p5"),
    ]
    sandbox = ReproSandbox(docker="")
    statuses = [sandbox.run(project).status for project in projects]
    executable = sum(1 for status in statuses if status in {"success", "failed"})
    assert round(executable / len(projects), 2) == 0.8
