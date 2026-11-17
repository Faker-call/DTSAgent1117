"""命令模板化：只允许 `pmagent.yaml` 声明的命令或预置模板，拒绝任意命令。"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pmagent.errors import PermissionDenied

SHELL_META = re.compile(r"[;&|><`$]")

TEMPLATES: dict[str, tuple[str, ...]] = {
    "build": ("cmake --build build", "ninja -C build", "make -j"),
    "run": ("./build/repro", "build/repro.exe"),
}


def validate_command(command: str, *, allowed: Sequence[str]) -> str:
    command = command.strip()
    if not command:
        raise PermissionDenied("命令为空", hint="使用 pmagent.yaml 中声明的 build/run")
    for candidate in allowed:
        candidate = candidate.strip()
        if command == candidate:
            return command
        if command.startswith(candidate + " "):
            suffix = command[len(candidate) :]
            if SHELL_META.search(suffix):
                break
            return command
    if SHELL_META.search(command):
        raise PermissionDenied(
            "命令包含 shell 控制字符", hint="只允许 pmagent.yaml 或模板中的命令，禁止拼接"
        )
    raise PermissionDenied(
        f"命令不在允许列表：{command[:80]}",
        hint="把命令写进 pmagent.yaml 的 build/run，或使用预置模板",
    )


def allowed_commands(
    *, build: str | None = None, run: str | None = None, extra: Sequence[str] = ()
) -> list[str]:
    allowed = list(extra)
    if build:
        allowed.append(build)
    if run:
        allowed.append(run)
    return allowed


__all__ = ["SHELL_META", "TEMPLATES", "allowed_commands", "validate_command"]
