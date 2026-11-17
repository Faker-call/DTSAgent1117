from __future__ import annotations

from pathlib import Path

from pmagent.store import ArtifactStore, TraceRecorder


def test_trace_recorder_appends_events(tmp_path: Path) -> None:
    path = tmp_path / "traces" / "run1.jsonl"
    with TraceRecorder(path, run_id="run1") as recorder:
        recorder.record("stage_started", stage="triage")
        recorder.record("stage_finished", stage="triage", status="ok")

    events = TraceRecorder.read(path)
    assert [e["seq"] for e in events] == [1, 2]
    assert {e["run_id"] for e in events} == {"run1"}
    assert events[0]["type"] == "stage_started"
    assert events[1]["stage"] == "triage"
    assert all("ts" in e for e in events)


def test_trace_read_missing_file_returns_empty(tmp_path: Path) -> None:
    assert TraceRecorder.read(tmp_path / "nope.jsonl") == []


def test_artifact_store_layout(tmp_path: Path) -> None:
    store = ArtifactStore(state_root=tmp_path / ".pmagent", artifacts_root=tmp_path / "artifacts")
    store.ensure_layout()

    assert store.session_path("s1").parent.exists()
    assert store.run_dir("r1").parent.exists()
    assert store.lock_path("index").parent.exists()
    assert store.trace_path("r1").parent.exists()
    assert store.report_path("r1").parent.exists()
    assert store.patch_dir("r1").parent.exists()

    assert store.session_path("s1").as_posix().endswith("sessions/s1.json")
    assert store.report_path("r1", "json").name == "r1.json"
