"""单写者文件锁：带心跳与过期接管，避免进程被杀后锁永久占用。"""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pmagent.errors import PmagentError

DEFAULT_STALE_SECONDS = 300.0


class ResourceBusy(PmagentError):
    """资源被其他进程持有。"""


@dataclass
class LockInfo:
    pid: int
    host: str
    user: str
    acquired_at: str
    heartbeat_at: str

    @classmethod
    def from_json(cls, data: dict) -> LockInfo:
        return cls(
            pid=int(data.get("pid", 0)),
            host=str(data.get("host", "")),
            user=str(data.get("user", "")),
            acquired_at=str(data.get("acquired_at", "")),
            heartbeat_at=str(data.get("heartbeat_at", "")),
        )

    def describe(self) -> str:
        return f"{self.user}@{self.host} (pid {self.pid})"


class SingleWriterLock:
    def __init__(
        self,
        path: str | Path,
        *,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
        poll_interval: float = 0.1,
        clock: object | None = None,
    ) -> None:
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.poll_interval = poll_interval
        self._clock = clock or time.time
        self._fd: int | None = None

    def _now(self) -> float:
        return self._clock()  # type: ignore[operator]

    def acquire(self, timeout: float = 0.0) -> LockInfo:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = self._now() + timeout
        while True:
            info = self._try_create()
            if info is not None:
                return info
            holder = self.read()
            if holder is not None and self._is_stale(holder):
                self._takeover()
                info = self._try_create()
                if info is not None:
                    return info
            if self._now() >= deadline:
                holder_text = holder.describe() if holder else "未知进程"
                raise ResourceBusy(
                    f"资源正被占用：{self.path.name} 持有者 {holder_text}",
                    hint="等待其完成，或确认无进程后删除锁文件",
                )
            time.sleep(self.poll_interval)

    def heartbeat(self) -> None:
        if self._fd is None:
            return
        info = self._read_fd()
        if info is None:
            return
        payload = {
            "pid": info.pid,
            "host": info.host,
            "user": info.user,
            "acquired_at": info.acquired_at,
            "heartbeat_at": datetime.now(UTC).isoformat(),
        }
        self._write_fd(payload)

    def release(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:  # pragma: no cover
            pass

    def read(self) -> LockInfo | None:
        if not self.path.exists():
            return None
        try:
            return LockInfo.from_json(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    # -- 内部 -------------------------------------------------------------
    def _try_create(self) -> LockInfo | None:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return None
        self._fd = fd
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
            "acquired_at": datetime.now(UTC).isoformat(),
            "heartbeat_at": datetime.now(UTC).isoformat(),
        }
        self._write_fd(payload)
        return LockInfo.from_json(payload)

    def _write_fd(self, payload: dict) -> None:
        if self._fd is None:
            return
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.truncate(self._fd, 0)
        os.write(self._fd, json.dumps(payload).encode("utf-8"))

    def _read_fd(self) -> LockInfo | None:
        if self._fd is None:
            return None
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            raw = os.read(self._fd, 4096).decode("utf-8")
            return LockInfo.from_json(json.loads(raw))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _is_stale(self, info: LockInfo) -> bool:
        try:
            heartbeat = datetime.fromisoformat(info.heartbeat_at).timestamp()
        except ValueError:
            return True
        return (self._now() - heartbeat) > self.stale_seconds

    def _takeover(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:  # pragma: no cover - 竞态
            pass

    def __enter__(self) -> SingleWriterLock:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
