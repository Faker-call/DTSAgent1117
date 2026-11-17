"""唯一执行器：所有工具调用经此执行，前后各记一条审计事件。"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pmagent.authz.policy import PolicyEngine, explain_denial
from pmagent.contracts import ToolProposal, User
from pmagent.errors import PermissionDenied
from pmagent.safety.commands import validate_command
from pmagent.safety.guardrails import Guardrails

DEFAULT_LIMITS: dict[str, int] = {
    "sandbox.run": 2,
    "code.get_slice": 20,
    "git.diff": 10,
}


@dataclass
class ToolSpec:
    fn: Callable[..., Any]
    write: bool = False
    path_arg: str = "path"
    commands: tuple[str, ...] | None = None


@dataclass
class Executor:
    policy: PolicyEngine
    guardrails: Guardrails
    tools: dict[str, ToolSpec]
    user: User
    audit: Callable[..., None] | None = None
    limits: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    calls: dict[str, int] = field(default_factory=dict)

    def execute(self, proposal: ToolProposal, *, stage: str, agent_role: str = "diagnosis") -> Any:
        spec = self.tools.get(proposal.tool)
        if spec is None:
            self._record("tool_denied", tool=proposal.tool, reason="未注册的工具")
            raise PermissionDenied(
                f"工具未注册：{proposal.tool}", hint="只有执行器注册过的工具可被调用"
            )

        decision = self.policy.authorize_tool(self.user, agent_role, proposal)
        if decision.decision != "allow":
            self._record("tool_denied", tool=proposal.tool, reason=decision.reason)
            raise PermissionDenied(explain_denial(decision))

        param_error = self.policy.validate_parameters(proposal)
        if param_error:
            self._record("tool_denied", tool=proposal.tool, reason=param_error)
            raise PermissionDenied(param_error)

        limit = self.limits.get(proposal.tool)
        if limit is not None and self.calls.get(proposal.tool, 0) >= limit:
            self._record("tool_denied", tool=proposal.tool, reason="超过调用次数上限")
            raise PermissionDenied(f"{proposal.tool} 超过调用次数上限 {limit}")

        if spec.write:
            self.guardrails.ensure_writable(proposal.args.get(spec.path_arg, ""))

        if spec.commands is not None:
            command = str(proposal.args.get("command", ""))
            validate_command(command, allowed=list(spec.commands))

        self.calls[proposal.tool] = self.calls.get(proposal.tool, 0) + 1
        started = time.perf_counter()
        self._record(
            "tool_call_started",
            tool=proposal.tool,
            stage=stage,
            agent_role=agent_role,
            args=_safe_args(proposal.args),
        )
        try:
            result = spec.fn(**proposal.args)
        except Exception as exc:
            self._record(
                "tool_call_failed", tool=proposal.tool, error=f"{type(exc).__name__}: {exc}"
            )
            raise
        self._record(
            "tool_call_finished",
            tool=proposal.tool,
            duration_ms=int((time.perf_counter() - started) * 1000),
            ok=True,
        )
        return result

    def _record(self, event: str, **fields: Any) -> None:
        if self.audit is not None:
            self.audit(event, **fields)


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in args.items():
        text = str(value)
        safe[key] = text[:120]
    return safe


__all__ = ["DEFAULT_LIMITS", "Executor", "ToolSpec"]
