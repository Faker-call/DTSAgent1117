from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pmagent.authz import (
    Permission,
    PolicyEngine,
    ResourceBusy,
    Role,
    SessionManager,
    SingleWriterLock,
    UserStore,
    explain_denial,
    has_permission,
    required_roles,
)
from pmagent.authz.store import PasswordHasherService
from pmagent.contracts import ToolProposal
from pmagent.store import TraceRecorder


@pytest.fixture()
def store(tmp_path) -> UserStore:  # noqa: ANN001
    return UserStore(tmp_path / "state.db")


def _make_user(store: UserStore, username: str, roles: list[Role], status: str = "active"):  # noqa: ANN001
    return store.create_user(
        username, PasswordHasherService().hash("secret123"), roles, status=status
    )


# -- 权限矩阵 --------------------------------------------------------------


def test_matrix_admin_can_build_index() -> None:
    assert has_permission([Role.ADMIN], Permission.INDEX_BUILD)
    assert has_permission([Role.MAINTAINER], Permission.INDEX_BUILD)


def test_matrix_viewer_cannot_build_index() -> None:
    assert not has_permission([Role.VIEWER], Permission.INDEX_BUILD)
    assert required_roles(Permission.INDEX_BUILD) == ["admin", "maintainer"]


def test_matrix_user_manage_only_admin() -> None:
    assert has_permission([Role.ADMIN], Permission.USER_MANAGE)
    for role in (Role.MAINTAINER, Role.DEVELOPER, Role.VIEWER):
        assert not has_permission([role], Permission.USER_MANAGE)


def test_matrix_all_roles_can_ask() -> None:
    for role in Role:
        assert has_permission([role], Permission.QUERY_ASK)


# -- 用户存储 --------------------------------------------------------------


def test_create_and_fetch_user(store: UserStore) -> None:
    user = _make_user(store, "alice", [Role.DEVELOPER])
    assert store.get_by_username("alice").user_id == user.user_id
    assert store.get_by_id(user.user_id).username == "alice"
    assert [u.username for u in store.list_users()] == ["alice"]


def test_duplicate_username_rejected(store: UserStore) -> None:
    _make_user(store, "alice", [Role.DEVELOPER])
    from pmagent.errors import ConfigError

    with pytest.raises(ConfigError):
        _make_user(store, "alice", [Role.VIEWER])


def test_set_roles_and_status(store: UserStore) -> None:
    _make_user(store, "bob", [Role.VIEWER])
    store.set_roles("bob", [Role.DEVELOPER])
    assert store.get_by_username("bob").roles == ["developer"]
    store.set_status("bob", "disabled")
    assert store.get_by_username("bob").status == "disabled"


def test_password_hash_roundtrip() -> None:
    hasher = PasswordHasherService()
    digest = hasher.hash("secret123")
    assert digest != "secret123"
    assert hasher.verify(digest, "secret123")
    assert not hasher.verify(digest, "wrong")


# -- 会话 ------------------------------------------------------------------


def test_login_and_logout(store: UserStore, tmp_path) -> None:  # noqa: ANN001
    _make_user(store, "alice", [Role.ADMIN])
    manager = SessionManager(store, token_path=tmp_path / "session.json")
    token = manager.login("alice", "secret123")
    assert token
    assert manager.current_user().username == "alice"
    manager.logout()
    assert manager.current_user() is None


def test_login_wrong_password(store: UserStore, tmp_path) -> None:  # noqa: ANN001
    _make_user(store, "alice", [Role.ADMIN])
    manager = SessionManager(store, token_path=tmp_path / "session.json")
    from pmagent.authz import AuthError

    with pytest.raises(AuthError):
        manager.login("alice", "wrong")


def test_login_lockout_after_repeated_failures(store: UserStore, tmp_path) -> None:  # noqa: ANN001
    _make_user(store, "alice", [Role.ADMIN])
    manager = SessionManager(store, token_path=tmp_path / "session.json")
    from pmagent.authz import AuthError

    for _ in range(5):
        with pytest.raises(AuthError):
            manager.login("alice", "wrong")
    assert store.is_locked("alice")
    with pytest.raises(AuthError):
        manager.login("alice", "secret123")


def test_disabled_user_cannot_login(store: UserStore, tmp_path) -> None:  # noqa: ANN001
    _make_user(store, "alice", [Role.ADMIN], status="disabled")
    manager = SessionManager(store, token_path=tmp_path / "session.json")
    from pmagent.errors import PermissionDenied

    with pytest.raises(PermissionDenied):
        manager.login("alice", "secret123")


def test_session_expires(store: UserStore, tmp_path) -> None:  # noqa: ANN001
    _make_user(store, "alice", [Role.ADMIN])
    now = [datetime.now(UTC)]
    manager = SessionManager(store, token_path=tmp_path / "session.json", clock=lambda: now[0])
    manager.login("alice", "secret123")
    assert manager.current_user() is not None
    now[0] = now[0] + timedelta(hours=13)
    assert manager.current_user() is None


# -- 策略引擎 --------------------------------------------------------------


def test_policy_allow_and_deny(store: UserStore) -> None:
    admin = _make_user(store, "admin", [Role.ADMIN])
    viewer = _make_user(store, "viewer", [Role.VIEWER])
    policy = PolicyEngine()
    assert policy.authorize(admin, "index.build", permission=Permission.INDEX_BUILD).decision == "allow"
    denied = policy.authorize(viewer, "index.build", permission=Permission.INDEX_BUILD)
    assert denied.decision == "deny"
    assert "需要角色" in explain_denial(denied)


def test_policy_tool_allowlist(store: UserStore) -> None:
    admin = _make_user(store, "admin", [Role.ADMIN])
    policy = PolicyEngine()
    ok = policy.authorize_tool(
        admin, "knowledge", ToolProposal(tool="doc.search", stage="normalize")
    )
    assert ok.decision == "allow"
    bad_stage = policy.authorize_tool(
        admin, "knowledge", ToolProposal(tool="git.diff", stage="normalize")
    )
    assert bad_stage.decision == "deny"
    bad_agent = policy.authorize_tool(
        admin, "critic", ToolProposal(tool="doc.search", stage="normalize")
    )
    assert bad_agent.decision == "deny"


def test_policy_tool_permission(store: UserStore) -> None:
    viewer = _make_user(store, "viewer", [Role.VIEWER])
    policy = PolicyEngine()
    decision = policy.authorize_tool(
        viewer, "diagnosis", ToolProposal(tool="sandbox.run", stage="normalize")
    )
    assert decision.decision == "deny"


def test_policy_records_trace(store: UserStore, tmp_path) -> None:  # noqa: ANN001
    admin = _make_user(store, "admin", [Role.ADMIN])
    path = tmp_path / "trace.jsonl"
    with TraceRecorder(path, run_id="r1") as recorder:
        PolicyEngine(trace=recorder).authorize(
            admin, "index.build", permission=Permission.INDEX_BUILD
        )
    events = TraceRecorder.read(path)
    assert events[0]["type"] == "authz_decision"
    assert events[0]["decision"] == "allow"
    assert events[0]["user_id"] == admin.user_id


def test_policy_trace_includes_agent_role(store: UserStore, tmp_path) -> None:  # noqa: ANN001
    admin = _make_user(store, "admin", [Role.ADMIN])
    path = tmp_path / "trace.jsonl"
    with TraceRecorder(path, run_id="r1") as recorder:
        PolicyEngine(trace=recorder).authorize_tool(
            admin, "diagnosis", ToolProposal(tool="code.get_slice", stage="slice")
        )
    event = TraceRecorder.read(path)[0]
    assert event["agent_role"] == "diagnosis"
    assert event["tool"] == "code.get_slice"


def test_custom_authz_backend_is_pluggable(store: UserStore) -> None:
    viewer = _make_user(store, "viewer", [Role.VIEWER])

    class AllowAll:
        def has_permission(self, user, permission):  # noqa: ANN001
            return True

    policy = PolicyEngine(backend=AllowAll())
    decision = policy.authorize(viewer, "index.build", permission=Permission.INDEX_BUILD)
    assert decision.decision == "allow"


# -- 单写者锁 --------------------------------------------------------------


def test_lock_acquire_release(tmp_path) -> None:  # noqa: ANN001
    lock = SingleWriterLock(tmp_path / "index.lock")
    info = lock.acquire()
    assert info.pid > 0
    assert lock.path.exists()
    lock.release()
    assert not lock.path.exists()


def test_lock_busy_reports_holder(tmp_path) -> None:  # noqa: ANN001
    first = SingleWriterLock(tmp_path / "index.lock")
    first.acquire()
    second = SingleWriterLock(tmp_path / "index.lock")
    with pytest.raises(ResourceBusy) as exc:
        second.acquire(timeout=0)
    assert "pid" in str(exc.value)
    first.release()


def test_lock_stale_is_taken_over(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "index.lock"
    path.write_text(
        '{"pid": 999, "host": "old", "user": "ghost", '
        '"acquired_at": "2020-01-01T00:00:00+00:00", '
        '"heartbeat_at": "2020-01-01T00:00:00+00:00"}',
        encoding="utf-8",
    )
    lock = SingleWriterLock(path, stale_seconds=1)
    info = lock.acquire(timeout=0)
    assert info.pid != 999
    lock.release()


def test_lock_heartbeat_updates_timestamp(tmp_path) -> None:  # noqa: ANN001
    lock = SingleWriterLock(tmp_path / "index.lock")
    first = lock.acquire()
    lock.heartbeat()
    updated = lock.read()
    assert updated is not None
    assert updated.heartbeat_at >= first.heartbeat_at
    lock.release()
