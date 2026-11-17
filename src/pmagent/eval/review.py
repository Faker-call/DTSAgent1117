"""人工评审回写：评分写入 reviewed.jsonl，确认案例进入 L3 案例库。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pmagent.contracts import Case
from pmagent.memory import CaseFeedback
from pmagent.store import ArtifactStore

REVIEW_FILE = "reviewed.jsonl"


def review_path(store: ArtifactStore | None = None) -> Path:
    store = store or ArtifactStore()
    return store.artifacts_root / "eval" / REVIEW_FILE


def append_review(entry: dict[str, Any], store: ArtifactStore | None = None) -> Path:
    path = review_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"reviewed_at": datetime.now(UTC).isoformat(), **entry}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def admit_case(
    entry: dict[str, Any],
    feedback: CaseFeedback,
    *,
    reviewer: str = "human",
    holdout: list[dict] | None = None,
    validator=None,  # noqa: ANN001
) -> Case:
    candidate = feedback.build_candidate(
        symptom=entry["symptom"],
        root_cause=entry.get("root_cause", ""),
        evidence_refs=entry.get("evidence_refs", []),
        root_cause_file=entry.get("root_cause_file"),
        root_cause_symbol=entry.get("root_cause_symbol"),
    )
    return feedback.approve(
        candidate,
        reviewer,
        root_cause_file=entry.get("root_cause_file"),
        root_cause_symbol=entry.get("root_cause_symbol"),
        holdout=holdout,
        validator=validator,
    )


__all__ = ["REVIEW_FILE", "admit_case", "append_review", "review_path"]
