"""复现沙箱：优先容器隔离，无容器时退化为受限子进程并标注降级。"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_TIMEOUT = 120
DEFAULT_MEMORY_MB = 2048
DEFAULT_CPUS = 2
ENV_WHITELIST = ("PATH", "HOME", "TMP", "TEMP", "SYSTEMROOT", "LANG", "LC_ALL", "PYTHONPATH")


@dataclass
class ReproConfig:
    build: str | None = None
    run: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class CommandResult:
    command: str
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0


@dataclass
class SandboxResult:
    status: str  # success | failed | timeout | blocked
    isolation: str  # container | process
    warnings: list[str] = field(default_factory=list)
    build: CommandResult | None = None
    run: CommandResult | None = None
    environment_diff: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "isolation": self.isolation,
            "warnings": self.warnings,
            "build": self.build.__dict__ if self.build else None,
            "run": self.run.__dict__ if self.run else None,
            "environment_diff": self.environment_diff,
        }


def load_repro_config(project_dir: str | Path) -> ReproConfig:
    project = Path(project_dir)
    for name in ("pmagent.yaml", "pmagent.yml"):
        path = project / name
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"pmagent.yaml 解析失败：{exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("pmagent.yaml 顶层必须是对象")
            return ReproConfig(
                build=data.get("build"),
                run=data.get("run"),
                timeout=int(data.get("timeout", DEFAULT_TIMEOUT)),
                env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            )
    return ReproConfig()


class ReproSandbox:
    def __init__(
        self,
        *,
        image: str = "pmagent-repro:latest",
        timeout: int = DEFAULT_TIMEOUT,
        memory_mb: int = DEFAULT_MEMORY_MB,
        cpus: int = DEFAULT_CPUS,
        allow_network: bool = False,
        docker: str | None = None,
    ) -> None:
        self.image = image
        self.timeout = timeout
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.allow_network = allow_network
        self.docker = docker if docker is not None else shutil.which("docker")

    def run(
        self,
        project_dir: str | Path,
        *,
        build_cmd: str | None = None,
        run_cmd: str | None = None,
    ) -> SandboxResult:
        project = Path(project_dir)
        if not project.exists():
            return SandboxResult(
                status="blocked", isolation="none", warnings=[f"复现工程不存在：{project}"]
            )
        try:
            config = load_repro_config(project)
        except ValueError as exc:
            return SandboxResult(status="blocked", isolation="none", warnings=[str(exc)])

        build = build_cmd or config.build
        run = run_cmd or config.run
        if not build and not run:
            return SandboxResult(
                status="blocked",
                isolation="none",
                warnings=["未提供构建或运行命令（pmagent.yaml 缺少 build/run）"],
            )
        env = {**{key: os.environ[key] for key in ENV_WHITELIST if key in os.environ}, **config.env}
        timeout = config.timeout or self.timeout
        environment = {
            "host_platform": platform.platform(),
            "docker_available": bool(self.docker),
            "network_allowed": self.allow_network,
        }

        if self.docker:
            return self._run_container(project, build, run, env, timeout, environment)
        return self._run_local(project, build, run, env, timeout, environment)

    # -- 容器 -------------------------------------------------------------
    def _run_container(
        self, project: Path, build: str | None, run: str | None, env: dict[str, str], timeout: int,
        environment: dict[str, Any],
    ) -> SandboxResult:
        warnings = ["容器隔离：非 root、默认禁网、CPU/内存配额生效"]
        build_result = None
        if build:
            build_result = self._docker_exec(project, build, env, timeout)
            if build_result.exit_code != 0:
                return SandboxResult(
                    status="failed",
                    isolation="container",
                    warnings=warnings + ["构建失败"],
                    build=build_result,
                    environment_diff=environment,
                )
        run_result = None
        if run:
            run_result = self._docker_exec(project, run, env, timeout)
        status = "success"
        if run_result is None:
            warnings.append("未配置运行命令")
        elif run_result.exit_code is None:
            status = "timeout"
        elif run_result.exit_code != 0:
            status = "failed"
        return SandboxResult(
            status=status,
            isolation="container",
            warnings=warnings,
            build=build_result,
            run=run_result,
            environment_diff=environment,
        )

    def _docker_exec(
        self, project: Path, command: str, env: dict[str, str], timeout: int
    ) -> CommandResult:
        args = [
            self.docker or "docker",
            "run",
            "--rm",
            f"--memory={self.memory_mb}m",
            f"--cpus={self.cpus}",
            "--user",
            "1000:1000",
            "--workdir",
            "/work",
            "-v",
            f"{project.resolve()}:/work",
        ]
        if not self.allow_network:
            args.append("--network=none")
        for key, value in env.items():
            args += ["-e", f"{key}={value}"]
        args += [self.image, "bash", "-lc", command]
        return self._exec(args, command, timeout, cwd=project)

    # -- 本地降级 ---------------------------------------------------------
    def _run_local(
        self, project: Path, build: str | None, run: str | None, env: dict[str, str], timeout: int,
        environment: dict[str, Any],
    ) -> SandboxResult:
        warnings = [
            "未检测到 Docker，已退化为本地受限子进程：无网络隔离、无内存/CPU 硬限制",
            "宿主环境可能与 SDK 构建环境不同，结论需在目标环境复核",
        ]
        build_result = None
        if build:
            build_result = self._exec([build], build, timeout, cwd=project, shell=True, env=env)
            if build_result.exit_code != 0:
                return SandboxResult(
                    status="failed",
                    isolation="process",
                    warnings=warnings + ["构建失败"],
                    build=build_result,
                    environment_diff=environment,
                )
        run_result = None
        if run:
            run_result = self._exec([run], run, timeout, cwd=project, shell=True, env=env)
        status = "success"
        if run_result is None:
            warnings.append("未配置运行命令")
        elif run_result.exit_code is None:
            status = "timeout"
        elif run_result.exit_code != 0:
            status = "failed"
        return SandboxResult(
            status=status,
            isolation="process",
            warnings=warnings,
            build=build_result,
            run=run_result,
            environment_diff=environment,
        )

    @staticmethod
    def _exec(
        args: list[str],
        command: str,
        timeout: int,
        *,
        cwd: Path,
        shell: bool = False,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command if shell else args,
                cwd=cwd,
                shell=shell,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            return CommandResult(
                command=command,
                exit_code=completed.returncode,
                stdout=completed.stdout[-4000:],
                stderr=completed.stderr[-4000:],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                exit_code=None,
                stderr=f"超时（>{timeout}s）",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )


__all__ = [
    "CommandResult",
    "DEFAULT_TIMEOUT",
    "ReproConfig",
    "ReproSandbox",
    "SandboxResult",
    "load_repro_config",
]
