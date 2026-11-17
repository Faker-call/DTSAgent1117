"""记忆存储：L0 工作记忆、L1 会话、L2 运行、L3 案例。"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pmagent.contracts import Case, SessionState

DEFAULT_STATE_DIR = Path(".pmagent")
SESSION_RETENTION_DAYS = 90
RUN_RETENTION_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    index_version TEXT,
    state_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    symptom TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    fix_commit TEXT,
    sdk_version TEXT,
    engine_commit_range TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    reviewed_by TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    dedupe_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    reuse_count INTEGER NOT NULL DEFAULT 0,
    recurrence_count INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS negative_patches (
    patch_id TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def resolve_memory_db(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env_path = os.environ.get("PMAGENT_MEMORY_DB")
    if env_path:
        return Path(env_path).expanduser()
    state_dir = Path(os.environ.get("PMAGENT_STATE", str(DEFAULT_STATE_DIR)))
    return state_dir / "memory.db"


def _now() -> datetime:
    return datetime.now(UTC)


class WorkingMemory:
    """L0：仅在进程内存在，阶段结束即丢弃。"""

    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self._data[key] = value

    def get(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


class MemoryStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = resolve_memory_db(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.working = WorkingMemory()
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- L1 会话 ----------------------------------------------------------
    def save_session(self, state: SessionState) -> None:
        state.updated_at = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (id, state_json, updated_at) VALUES (?,?,?)",
            (state.id, state.model_dump_json(), state.updated_at.isoformat()),
        )
        self._conn.commit()

    def load_session(self, session_id: str) -> SessionState | None:
        row = self._conn.execute(
            "SELECT state_json FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return SessionState.model_validate_json(row["state_json"])

    def list_sessions(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, updated_at FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    # -- L2 运行 ----------------------------------------------------------
    def save_run(
        self,
        run_id: str,
        *,
        kind: str,
        status: str,
        index_version: str | None,
        state: dict | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs (run_id, kind, status, started_at, finished_at,"
            " index_version, state_json) VALUES (?,?,?,?,?,?,?)",
            (
                run_id,
                kind,
                status,
                (started_at or _now()).isoformat(),
                finished_at.isoformat() if finished_at else None,
                index_version,
                json.dumps(state or {}, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def load_run(self, run_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["state"] = json.loads(data.pop("state_json") or "{}")
        return data

    def list_runs(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT run_id, kind, status, started_at, finished_at FROM runs"
            " ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- L3 案例 ----------------------------------------------------------
    def add_case(self, case: Case, *, dedupe_key: str) -> Case:
        self._conn.execute(
            "INSERT OR REPLACE INTO cases (case_id, symptom, root_cause, fix_commit,"
            " sdk_version, engine_commit_range, confidence, reviewed_by, created_at,"
            " expires_at, dedupe_key, status, payload_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                case.case_id,
                case.symptom,
                case.root_cause,
                case.fix_commit,
                case.sdk_version,
                case.engine_commit_range,
                case.confidence,
                case.reviewed_by,
                case.created_at.isoformat(),
                case.expires_at.isoformat() if case.expires_at else None,
                dedupe_key,
                "active",
                case.model_dump_json(),
            ),
        )
        self._conn.commit()
        return case

    def find_case_by_dedupe(self, dedupe_key: str) -> Case | None:
        row = self._conn.execute(
            "SELECT payload_json FROM cases WHERE dedupe_key = ? AND status = 'active'",
            (dedupe_key,),
        ).fetchone()
        return Case.model_validate_json(row["payload_json"]) if row else None

    def list_cases(self, *, active_only: bool = True) -> list[Case]:
        query = "SELECT payload_json FROM cases"
        if active_only:
            query += " WHERE status = 'active'"
        query += " ORDER BY created_at DESC"
        rows = self._conn.execute(query).fetchall()
        return [Case.model_validate_json(row["payload_json"]) for row in rows]

    def case_status(self, case_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return row["status"] if row else None

    def set_case_status(self, case_id: str, status: str) -> None:
        self._conn.execute("UPDATE cases SET status = ? WHERE case_id = ?", (status, case_id))
        self._conn.commit()

    def bump_reuse(self, case_id: str) -> None:
        self._conn.execute(
            "UPDATE cases SET reuse_count = reuse_count + 1 WHERE case_id = ?", (case_id,)
        )
        self._conn.commit()

    def bump_recurrence(self, case_id: str) -> int:
        self._conn.execute(
            "UPDATE cases SET recurrence_count = recurrence_count + 1 WHERE case_id = ?",
            (case_id,),
        )
        self._conn.commit()
        return self.recurrence_count(case_id)

    def recurrence_count(self, case_id: str) -> int:
        row = self._conn.execute(
            "SELECT recurrence_count FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def delete_case(self, case_id: str) -> None:
        self._conn.execute("DELETE FROM cases WHERE case_id = ?", (case_id,))
        self._conn.commit()

    # -- 反例 -------------------------------------------------------------
    def add_negative_patch(self, patch_id: str, reason: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO negative_patches (patch_id, reason, created_at)"
            " VALUES (?,?,?)",
            (patch_id, reason, _now().isoformat()),
        )
        self._conn.commit()

    def list_negative_patches(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT patch_id, reason FROM negative_patches ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    # -- 保留策略 ---------------------------------------------------------
    def purge_expired(self, *, session_days: int = SESSION_RETENTION_DAYS,
                      run_days: int = RUN_RETENTION_DAYS) -> dict[str, int]:
        now = _now()
        session_cutoff = (now - timedelta(days=session_days)).isoformat()
        run_cutoff = (now - timedelta(days=run_days)).isoformat()
        sessions = self._conn.execute(
            "DELETE FROM sessions WHERE updated_at < ?", (session_cutoff,)
        ).rowcount
        runs = self._conn.execute(
            "DELETE FROM runs WHERE started_at < ?", (run_cutoff,)
        ).rowcount
        expired = self._conn.execute(
            "UPDATE cases SET status = 'expired' WHERE expires_at IS NOT NULL"
            " AND expires_at < ? AND status = 'active'",
            (now.isoformat(),),
        ).rowcount
        self._conn.commit()
        return {"sessions": sessions, "runs": runs, "cases": expired}
