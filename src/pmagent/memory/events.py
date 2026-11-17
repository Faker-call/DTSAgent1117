"""事件日志与 checkpoint：append-only 事件 + 原子写状态，支持中断恢复。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pmagent.contracts import RunEvent


@dataclass
class ResumePoint:
    next_stage: str | None
    completed: list[str] = field(default_factory=list)
    state: dict = field(default_factory=dict)


class RunEventLog:
    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.run_dir / "events.jsonl"
        self.state_path = self.run_dir / "state.json"

    # -- 事件 -------------------------------------------------------------
    def append(self, event_type: str, **payload: object) -> RunEvent:
        seq = len(self.read()) + 1
        event = RunEvent(
            run_id=self.run_dir.name,
            seq=seq,
            ts=datetime.now(UTC),
            type=event_type,  # type: ignore[arg-type]
            payload=dict(payload),
        )
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
        return event

    def read(self) -> list[RunEvent]:
        if not self.events_path.exists():
            return []
        events: list[RunEvent] = []
        with self.events_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(RunEvent.model_validate_json(line))
                except ValueError:
                    break
        return events

    # -- checkpoint -------------------------------------------------------
    def checkpoint(self, state: dict) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def load_checkpoint(self) -> dict:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    # -- 恢复 -------------------------------------------------------------
    def resume(self, stages: list[str]) -> ResumePoint:
        events = self.read()
        completed: list[str] = []
        for event in events:
            if event.type == "stage_finished" and event.payload.get("status") == "ok":
                stage = event.payload.get("stage")
                if isinstance(stage, str) and stage not in completed:
                    completed.append(stage)
        next_stage = next((stage for stage in stages if stage not in completed), None)
        return ResumePoint(next_stage=next_stage, completed=completed, state=self.load_checkpoint())

    def interrupted(self) -> bool:
        events = self.read()
        return bool(events) and events[-1].type == "interrupted"
