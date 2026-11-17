"""策略引擎：用户权限 × 阶段 × 子 Agent 的工具裁决（M03/M16 落地）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from pmagent.authz.permissions import MATRIX, Permission, Role, has_permission, required_roles
from pmagent.contracts import AuthzDecision, ToolProposal, User

# 阶段 → 允许的工具（design.md M03）
STAGE_TOOLS: dict[str, set[str]] = {
    "normalize": {"code.search_symbol", "code.search_text", "doc.search", "stack.symbolicate"},
    "triage": {"code.search_symbol", "code.search_text", "doc.search", "stack.symbolicate"},
    "candidates": {
        "code.get_slice",
        "code.get_callers",
        "code.get_callees",
        "git.log",
        "git.blame",
        "git.diff",
    },
    "slice": {
        "code.get_slice",
        "code.get_callers",
        "code.get_callees",
        "git.log",
        "git.blame",
        "git.diff",
    },
    "hypothesize": set(),
    "score": set(),
    "patch": {"code.get_slice", "git.diff"},
    "sandbox": {"sandbox.run"},
}

# 子 Agent → 允许的工具（design.md M18）
AGENT_TOOLS: dict[str, set[str]] = {
    "knowledge": {"code.search_symbol", "code.search_text", "doc.search", "code.get_slice"},
    "diagnosis": {
        "code.search_symbol",
        "code.search_text",
        "doc.search",
        "stack.symbolicate",
        "code.get_slice",
        "code.get_callers",
        "code.get_callees",
        "git.log",
        "git.blame",
        "git.diff",
        "sandbox.run",
    },
    "retrieval": {"code.search_symbol", "code.search_text", "doc.search"},
    "patch": {"code.get_slice", "git.diff"},
    "critic": set(),
}

# 工具 → 所需业务权限（None 表示登录即可）
TOOL_PERMISSIONS: dict[str, Permission | None] = {
    "sandbox.run": Permission.SANDBOX_RUN,
}


# 工具 → 参数语义（只允许这些键，路径必须相对且不含上跳）
PARAM_KEYS: dict[str, set[str]] = {
    "code.search_symbol": {"query", "limit"},
    "code.search_text": {"pattern", "limit"},
    "code.get_slice": {"path", "symbol", "line_start", "line_end"},
    "code.get_callers": {"symbol"},
    "code.get_callees": {"symbol"},
    "doc.search": {"query", "limit", "version"},
    "git.log": {"path", "n"},
    "git.blame": {"path", "line"},
    "git.diff": {"path", "commit"},
    "stack.symbolicate": {"addresses"},
    "sandbox.run": {"command", "project"},
}
PATH_KEYS = {"path", "file", "repo", "project"}


class AuthzBackend(Protocol):
    """可替换的授权后端；本地实现见 LocalBackend。"""

    def has_permission(self, user: User, permission: Permission) -> bool: ...


@dataclass
class LocalBackend:
    matrix: dict[Permission, set[Role]] = field(default_factory=lambda: dict(MATRIX))

    def has_permission(self, user: User, permission: Permission) -> bool:
        roles = [Role(r) for r in user.roles]
        return bool(set(roles) & self.matrix.get(permission, set()))


class PolicyEngine:
    def __init__(self, backend: AuthzBackend | None = None, trace: Any | None = None) -> None:
        self.backend = backend or LocalBackend()
        self.trace = trace

    def authorize(
        self,
        user: User,
        action: str,
        *,
        permission: Permission | None = None,
        stage: str | None = None,
        tool: str | None = None,
        resource: dict[str, Any] | None = None,
    ) -> AuthzDecision:
        decision = self._decide(user, action, permission, stage, tool, resource)
        self._emit(decision)
        return decision

    def authorize_tool(
        self, user: User, agent_role: str, proposal: ToolProposal
    ) -> AuthzDecision:
        stage = proposal.stage or "normalize"
        allowed_by_stage = proposal.tool in STAGE_TOOLS.get(stage, set())
        allowed_by_agent = proposal.tool in AGENT_TOOLS.get(agent_role, set())
        if not (allowed_by_stage and allowed_by_agent):
            decision = AuthzDecision(
                actor={"user_id": user.user_id, "roles": user.roles},
                action="tool.call",
                resource={"tool": proposal.tool, "stage": stage, "agent_role": agent_role},
                decision="deny",
                reason=f"工具 {proposal.tool} 不在阶段 {stage} 或角色 {agent_role} 的白名单内",
                stage=stage,
                tool=proposal.tool,
            )
            self._emit(decision)
            return decision

        permission = TOOL_PERMISSIONS.get(proposal.tool)
        decision = self.authorize(
            user,
            f"tool:{proposal.tool}",
            permission=permission,
            stage=stage,
            tool=proposal.tool,
            resource={"agent_role": agent_role},
        )
        return decision

    def validate_parameters(self, proposal: ToolProposal) -> str | None:
        """参数语义校验：未知参数键、路径上跳或绝对路径都拒绝。"""
        allowed = PARAM_KEYS.get(proposal.tool, set())
        unknown = sorted(set(proposal.args) - allowed)
        if unknown:
            return f"工具 {proposal.tool} 收到未知参数：{unknown}"
        for key in PATH_KEYS & set(proposal.args):
            value = str(proposal.args[key])
            if ".." in value:
                return f"参数 {key} 包含路径上跳：{value}"
            if value.startswith(("/", "\\")) or (len(value) > 1 and value[1] == ":"):
                return f"参数 {key} 必须是相对路径：{value}"
        return None

    # -- 内部 -------------------------------------------------------------
    def _decide(
        self,
        user: User,
        action: str,
        permission: Permission | None,
        stage: str | None,
        tool: str | None,
        resource: dict[str, Any] | None,
    ) -> AuthzDecision:
        actor = {"user_id": user.user_id, "roles": list(user.roles)}
        if user.status != "active":
            return AuthzDecision(
                actor=actor,
                action=action,
                resource=resource or {},
                decision="deny",
                reason=f"账号状态为 {user.status}",
                stage=stage,
                tool=tool,
            )
        if permission is None:
            return AuthzDecision(
                actor=actor,
                action=action,
                resource=resource or {},
                decision="allow",
                reason="登录即可",
                stage=stage,
                tool=tool,
            )
        allowed = self.backend.has_permission(user, permission)
        return AuthzDecision(
            actor=actor,
            action=action,
            resource=resource or {},
            decision="allow" if allowed else "deny",
            reason="权限满足" if allowed else f"缺少权限 {permission}",
            stage=stage,
            tool=tool,
        )

    def _emit(self, decision: AuthzDecision) -> None:
        if self.trace is not None and hasattr(self.trace, "record"):
            self.trace.record(
                "authz_decision",
                user_id=decision.actor.get("user_id"),
                roles=decision.actor.get("roles"),
                action=decision.action,
                decision=decision.decision,
                reason=decision.reason,
                stage=decision.stage,
                tool=decision.tool,
                agent_role=decision.resource.get("agent_role"),
            )


def explain_denial(decision: AuthzDecision) -> str:
    """给用户看的拒绝原因，列出所需角色。"""
    permission = None
    if decision.reason.startswith("缺少权限 "):
        permission = decision.reason.split(" ", 1)[1]
    if permission:
        roles = required_roles(permission)
        return f"{decision.reason}（需要角色：{', '.join(roles)}）"
    return decision.reason


__all__ = [
    "AGENT_TOOLS",
    "AuthzBackend",
    "LocalBackend",
    "PARAM_KEYS",
    "PATH_KEYS",
    "PolicyEngine",
    "STAGE_TOOLS",
    "TOOL_PERMISSIONS",
    "explain_denial",
    "has_permission",
]
