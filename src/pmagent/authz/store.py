"""本地账号存储：SQLite + argon2id 哈希 + 状态机。"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from pmagent.authz.permissions import Role
from pmagent.contracts import User
from pmagent.errors import ConfigError, PermissionDenied

DEFAULT_DB_PATH = Path.home() / ".pmagent" / "state.db"
LOCKOUT_THRESHOLD = 5
LOCKOUT_SECONDS = 300


def _now() -> datetime:
    return datetime.now(UTC)


def resolve_db_path(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env_path = os.environ.get("PMAGENT_DB")
    if env_path:
        return Path(env_path).expanduser()
    return DEFAULT_DB_PATH


class PasswordHasherService:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False
        except Exception:  # noqa: BLE001 - 哈希损坏视为校验失败
            return False


class UserStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = resolve_db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    display_name TEXT,
                    roles TEXT NOT NULL,
                    status TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    client TEXT
                )
                """
            )

    # -- 用户 -------------------------------------------------------------
    def create_user(
        self,
        username: str,
        password_hash: str,
        roles: list[Role | str],
        *,
        display_name: str | None = None,
        status: str = "active",
    ) -> User:
        user_id = uuid.uuid4().hex[:12]
        now = _now().isoformat()
        role_values = [Role(r).value for r in roles]
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO users (user_id, username, display_name, roles, status,"
                    " password_hash, created_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        user_id,
                        username,
                        display_name,
                        ",".join(role_values),
                        status,
                        password_hash,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ConfigError(f"用户名已存在：{username}") from exc
        return User(
            user_id=user_id,
            username=username,
            display_name=display_name,
            roles=role_values,
            status=status,
            password_hash=password_hash,
            created_at=_now(),
        )

    def get_by_username(self, username: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_id(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> list[User]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [self._row_to_user(row) for row in rows]

    def count_admins(self) -> int:
        return sum(1 for user in self.list_users() if Role.ADMIN.value in user.roles)

    def set_roles(self, username: str, roles: list[Role | str]) -> None:
        role_values = [Role(r).value for r in roles]
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET roles = ? WHERE username = ?", (",".join(role_values), username)
            )
        if cursor.rowcount == 0:
            raise ConfigError(f"用户不存在：{username}")

    def set_status(self, username: str, status: str) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET status = ? WHERE username = ?", (status, username)
            )
        if cursor.rowcount == 0:
            raise ConfigError(f"用户不存在：{username}")

    def update_last_login(self, username: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ?, failed_attempts = 0, locked_until = NULL"
                " WHERE username = ?",
                (_now().isoformat(), username),
            )

    def register_failed_attempt(self, username: str) -> bool:
        """记录一次失败；返回是否已锁定。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT failed_attempts FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row is None:
                return False
            attempts = int(row["failed_attempts"]) + 1
            locked_until = None
            if attempts >= LOCKOUT_THRESHOLD:
                locked_until = (_now().timestamp() + LOCKOUT_SECONDS)
                locked_until = datetime.fromtimestamp(
                    locked_until, tz=UTC
                ).isoformat()
            conn.execute(
                "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE username = ?",
                (attempts, locked_until, username),
            )
        return locked_until is not None

    def is_locked(self, username: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT locked_until FROM users WHERE username = ?", (username,)
            ).fetchone()
        if row is None or not row["locked_until"]:
            return False
        return datetime.fromisoformat(row["locked_until"]) > _now()

    # -- 会话 -------------------------------------------------------------
    def create_session(
        self, token_hash: str, user_id: str, issued_at: datetime, expires_at: datetime
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token_hash, user_id, issued_at, expires_at, client)"
                " VALUES (?,?,?,?,?)",
                (token_hash, user_id, issued_at.isoformat(), expires_at.isoformat(), None),
            )

    def get_session(self, token_hash: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def purge_expired_sessions(self, now: datetime) -> int:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        return cursor.rowcount

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            roles=[r for r in (row["roles"] or "").split(",") if r],
            status=row["status"],
            password_hash=row["password_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login_at=datetime.fromisoformat(row["last_login_at"])
            if row["last_login_at"]
            else None,
        )


def ensure_active(user: User | None) -> User:
    if user is None:
        raise PermissionDenied("未登录", hint="先执行 pmagent auth login")
    if user.status != "active":
        raise PermissionDenied(f"账号状态为 {user.status}，无法操作", hint="联系管理员启用账号")
    return user
