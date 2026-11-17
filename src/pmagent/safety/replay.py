"""运行记录导出与回放：从工件与事件日志重建结论并校验一致性。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from pmagent.contracts import LocalizationReport
from pmagent.errors import PmagentError
from pmagent.memory import RunEventLog
from pmagent.store import ArtifactStore


@dataclass
class ReplayResult:
    run_id: str
    report: LocalizationReport
    digest: str
    consistent: bool = True
    issues: list[str] = field(default_factory=list)


class ReplayEngine:
    def __init__(self, store: ArtifactStore | None = None) -> None:
        self.store = store or ArtifactStore()

    def export(self, run_id: str) -> dict:
        report_path = self.store.report_path(run_id, "json")
        if not report_path.exists():
            raise PmagentError(
                f"运行 {run_id} 没有报告工件", hint="先执行 pmagent diagnose 生成报告"
            )
        report = LocalizationReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        events = self._events(run_id)
        return {
            "run_id": run_id,
            "digest": self._digest(report),
            "report": json.loads(report.model_dump_json()),
            "events": [event.model_dump(mode="json") for event in events],
        }

    def replay(self, run_id: str) -> ReplayResult:
        exported = self.export(run_id)
        report = LocalizationReport.model_validate(exported["report"])
        digest = self._digest(report)
        issues: list[str] = []
        if digest != exported["digest"]:
            issues.append("回放摘要与导出摘要不一致")
        if not exported["events"]:
            issues.append("无运行事件，仅按报告工件回放")
        else:
            issues.extend(self._check_events(exported["events"]))
        return ReplayResult(
            run_id=run_id,
            report=report,
            digest=digest,
            consistent=not issues,
            issues=issues,
        )

    def _events(self, run_id: str):  # noqa: ANN202
        run_dir = self.store.run_dir(run_id)
        if not run_dir.exists():
            return []
        try:
            return RunEventLog(run_dir).read()
        except (OSError, ValueError):
            return []

    @staticmethod
    def _check_events(events: list[dict]) -> list[str]:
        issues: list[str] = []
        seqs = [event.get("seq", 0) for event in events]
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            issues.append("事件序号不是严格递增")
        started = {
            event["payload"].get("stage")
            for event in events
            if event.get("type") == "stage_started"
        }
        finished = {
            event["payload"].get("stage")
            for event in events
            if event.get("type") == "stage_finished"
        }
        missing = started - finished
        if missing:
            issues.append(f"阶段未收尾：{sorted(item for item in missing if item)}")
        return issues

    @staticmethod
    def _digest(report: LocalizationReport) -> str:
        canonical = json.dumps(
            json.loads(report.model_dump_json()), sort_keys=True, ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


__all__ = ["ReplayEngine", "ReplayResult"]
