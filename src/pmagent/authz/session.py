"""会话令牌：12 小时有效期，支持环境变量注入（CI 场景）。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pmagent.authz.store import UserStore, ensure_active
from pmagent.contracts import User
from pmagent.errors import PermissionDenied

SESSION_TTL = timedelta(hours=12)
TOKEN_ENV = "PMAGENT_TOKEN"
SESSION_ENV = "PMAGENT_SESSION"
DEFAULT_SESSION_PATH = Path.home() / ".pmagent" / "session.json"


class AuthError(PermissionDenied):
    """认证失败（与权限不足共用退出码 6）。"""


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_session_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env_path = os.environ.get(SESSION_ENV)
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_SESSION_PATH


class SessionManager:
    def __init__(
        self,
        store: UserStore,
        *,
        token_path: str | Path | None = None,
        environ: dict[str, str] | None = None,
        clock: object | None = None,
    ) -> None:
        self.store = store
        self.token_path = resolve_session_path(token_path)
        self._environ = environ
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return self._clock()  # type: ignore[operator]

    # -- 登录态 -----------------------------------------------------------
    def login(self, username: str, password: str) -> str:
        user = self.store.get_by_username(username)
        if user is None:
            raise AuthError("用户名或密码错误")
        if self.store.is_locked(username):
            raise AuthError("账号因多次登录失败被临时锁定", hint="稍后重试或联系管理员")

        from pmagent.authz.store import PasswordHasherService

        if not PasswordHasherService().verify(user.password_hash, password):
            locked = self.store.register_failed_attempt(username)
            hint = "账号已临时锁定" if locked else "请检查用户名与密码"
            raise AuthError("用户名或密码错误", hint=hint)
        ensure_active(user)

        token = secrets.token_urlsafe(32)
        now = self._now()
        self.store.create_session(_hash_token(token), user.user_id, now, now + SESSION_TTL)
        self.store.update_last_login(username)
        self._write_token(token)
        return token

    def logout(self) -> None:
        token = self.token()
        if token:
            self.store.delete_session(_hash_token(token))
        if self.token_path.exists():
            self.token_path.unlink()

    def token(self) -> str | None:
        environ = dict(os.environ if self._environ is None else self._environ)
        env_token = environ.get(TOKEN_ENV)
        if env_token:
            return env_token
        if not self.token_path.exists():
            return None
        try:
            data = json.loads(self.token_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data.get("token")

    def current_user(self) -> User | None:
        token = self.token()
        if not token:
            return None
        session = self.store.get_session(_hash_token(token))
        if session is None:
            return None
        if datetime.fromisoformat(session["expires_at"]) <= self._now():
            self.store.delete_session(_hash_token(token))
            return None
        return self.store.get_by_id(session["user_id"])

    def require_user(self) -> User:
        user = self.current_user()
        if user is None:
            raise PermissionDenied(
                "未登录或登录已过期", hint="先执行 pmagent auth login"
            )
        return ensure_active(user)

    # -- 内部 -------------------------------------------------------------
    def _write_token(self, token: str) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(
            json.dumps({"token": token, "issued_at": self._now().isoformat()}),
            encoding="utf-8",
        )
        try:
            os.chmod(self.token_path, 0o600)
        except OSError:  # pragma: no cover - Windows 上可能不支持
            pass
