"""统一错误类型与退出码。

退出码语义见 docs/cli.md：
0 成功 / 1 内部错误 / 2 参数或配置错误 / 3 证据不足 /
4 定位失败 / 5 预算超限 / 6 权限不足 / 130 用户中断
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    INTERNAL = 1
    USAGE = 2
    INSUFFICIENT_EVIDENCE = 3
    LOCALIZATION_FAILED = 4
    BUDGET_EXCEEDED = 5
    PERMISSION_DENIED = 6
    INTERRUPTED = 130


class PmagentError(Exception):
    """所有可预期错误的基类，携带退出码与可选修复建议。"""

    exit_code: ExitCode = ExitCode.INTERNAL

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.message} ({self.hint})" if self.hint else self.message


class UsageError(PmagentError):
    exit_code = ExitCode.USAGE


class ConfigError(UsageError):
    exit_code = ExitCode.USAGE


class InsufficientEvidence(PmagentError):
    exit_code = ExitCode.INSUFFICIENT_EVIDENCE


class LocalizationFailed(PmagentError):
    exit_code = ExitCode.LOCALIZATION_FAILED


class BudgetExceeded(PmagentError):
    exit_code = ExitCode.BUDGET_EXCEEDED


class PermissionDenied(PmagentError):
    exit_code = ExitCode.PERMISSION_DENIED


class Interrupted(PmagentError):
    exit_code = ExitCode.INTERRUPTED
