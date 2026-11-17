"""审计日志：JSONL 追加，供执行器与门禁记录决策。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def jsonl_audit(path: str | Path):  # noqa: ANN201
    target = Path(path)

    def record(event: str, **fields: Any) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **{key: value for key, value in fields.items()},
        }
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    return record


__all__ = ["jsonl_audit"]
