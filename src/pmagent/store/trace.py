"""运行追踪：以 JSONL 追加记录每次运行的阶段、模型、工具与耗时。"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


class TraceRecorder:
    """追加写入 JSONL；每条记录自带 run_id、序号与时间戳。"""

    def __init__(self, path: Path, run_id: str | None = None) -> None:
        self.path = Path(path)
        self.run_id = run_id or new_run_id()
        self._seq = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def record(self, event_type: str, **payload: Any) -> dict[str, Any]:
        self._seq += 1
        event = {
            "run_id": self.run_id,
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(),
            "type": event_type,
            **payload,
        }
        self._fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._fh.flush()
        return event

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> TraceRecorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def read(path: Path | str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        target = Path(path)
        if not target.exists():
            return events
        with target.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    @staticmethod
    def iter_events(path: Path | str) -> Iterator[dict[str, Any]]:
        target = Path(path)
        if not target.exists():
            return
        with target.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)
