"""身份与权限（M16）：账号、会话、角色矩阵、策略引擎与单写者锁。"""

from pmagent.authz.locks import LockInfo, ResourceBusy, SingleWriterLock
from pmagent.authz.permissions import (
    MATRIX,
    Permission,
    Role,
    has_permission,
    required_roles,
)
from pmagent.authz.policy import (
    AGENT_TOOLS,
    STAGE_TOOLS,
    AuthzBackend,
    LocalBackend,
    PolicyEngine,
    explain_denial,
)
from pmagent.authz.session import AuthError, SessionManager
from pmagent.authz.store import PasswordHasherService, UserStore, ensure_active

__all__ = [
    "AGENT_TOOLS",
    "AuthError",
    "AuthzBackend",
    "LocalBackend",
    "LockInfo",
    "MATRIX",
    "PasswordHasherService",
    "Permission",
    "PolicyEngine",
    "ResourceBusy",
    "Role",
    "STAGE_TOOLS",
    "SessionManager",
    "SingleWriterLock",
    "UserStore",
    "ensure_active",
    "explain_denial",
    "has_permission",
    "required_roles",
]
