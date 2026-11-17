"""角色与权限矩阵（资源 × 动作）。

矩阵与 design.md「M16 AuthZ」一致；额外的业务动作（问答、定位、补丁建议）
在文档 docs/rbac.md 中说明。
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    MAINTAINER = "maintainer"
    DEVELOPER = "developer"
    VIEWER = "viewer"


class Permission(StrEnum):
    INDEX_READ = "index.read"
    INDEX_BUILD = "index.build"
    CASE_CONFIRM = "case.confirm"
    PATCH_APPLY = "patch.apply"
    PATCH_SUGGEST = "patch.suggest"
    EVAL_RUN = "eval.run"
    SANDBOX_RUN = "sandbox.run"
    USER_MANAGE = "user.manage"
    SECRET_OWN = "secret.own"
    QUERY_ASK = "query.ask"
    DIAGNOSE_RUN = "diagnose.run"


# 权限 → 允许的角色集合
MATRIX: dict[Permission, set[Role]] = {
    Permission.INDEX_READ: {Role.ADMIN, Role.MAINTAINER, Role.DEVELOPER, Role.VIEWER},
    Permission.INDEX_BUILD: {Role.ADMIN, Role.MAINTAINER},
    Permission.CASE_CONFIRM: {Role.ADMIN, Role.MAINTAINER},
    Permission.PATCH_APPLY: {Role.ADMIN, Role.MAINTAINER},
    Permission.PATCH_SUGGEST: {Role.ADMIN, Role.MAINTAINER, Role.DEVELOPER},
    Permission.EVAL_RUN: {Role.ADMIN, Role.MAINTAINER, Role.DEVELOPER},
    Permission.SANDBOX_RUN: {Role.ADMIN, Role.MAINTAINER, Role.DEVELOPER},
    Permission.USER_MANAGE: {Role.ADMIN},
    Permission.SECRET_OWN: {Role.ADMIN, Role.MAINTAINER, Role.DEVELOPER, Role.VIEWER},
    Permission.QUERY_ASK: {Role.ADMIN, Role.MAINTAINER, Role.DEVELOPER, Role.VIEWER},
    Permission.DIAGNOSE_RUN: {Role.ADMIN, Role.MAINTAINER, Role.DEVELOPER},
}


def roles_with(permission: Permission) -> set[Role]:
    return set(MATRIX.get(permission, set()))


def has_permission(roles: list[Role] | list[str], permission: Permission | str) -> bool:
    permission = Permission(permission)
    normalized = {Role(r) for r in roles}
    return bool(normalized & MATRIX.get(permission, set()))


def required_roles(permission: Permission | str) -> list[str]:
    return sorted(r.value for r in roles_with(Permission(permission)))
