"""混沌测试：模型超时、磁盘满、索引损坏、锁被占用时的降级行为。"""

from __future__ import annotations

from pathlib import Path

import pytest

from pmagent.authz import ResourceBusy, SingleWriterLock
from pmagent.errors import PmagentError
from pmagent.knowledge.index import CodeIndex, IndexVersionMismatch
from pmagent.orchestrator import Budget, Pipeline, Stage, StageStatus
from pmagent.safety import Guardrails


def test_model_timeout_degrades_pipeline() -> None:
    def model(ctx):  # noqa: ANN001
        raise TimeoutError("model timeout")

    def after(ctx):  # noqa: ANN001
        ctx["after"] = True

    result = Pipeline(
        [
            Stage("model", model, max_retries=0, optional=True),
            Stage("after", after),
        ]
    ).run({}, budget=Budget(max_wall_seconds=30))

    assert result.status is StageStatus.DEGRADED
    assert any("model" in reason for reason in result.degraded_reasons)
    assert result.stages[1].status is StageStatus.OK


def test_fatal_stage_stops_pipeline() -> None:
    def model(ctx):  # noqa: ANN001
        raise TimeoutError("model timeout")

    def after(ctx):  # noqa: ANN001
        ctx["after"] = True

    result = Pipeline(
        [
            Stage("model", model, max_retries=0),
            Stage("after", after),
        ]
    ).run({}, budget=Budget(max_wall_seconds=30))

    assert result.status is StageStatus.FAILED
    assert len(result.stages) == 1


def test_disk_full_surfaces_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guardrails = Guardrails(tmp_path)

    def broken_write(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", broken_write)
    with pytest.raises(OSError) as excinfo:
        guardrails.write("artifacts/report.md", "x")
    assert "No space" in str(excinfo.value)


def test_corrupted_index_pointer_is_clean_error(tmp_path: Path) -> None:
    root = tmp_path / "index"
    root.mkdir()
    (root / "current").write_text("deadbeefcafe", encoding="utf-8")
    with pytest.raises(IndexVersionMismatch) as excinfo:
        CodeIndex.open(root)
    assert "快照缺失" in str(excinfo.value)


def test_lock_occupied_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "writer.lock"
    holder = SingleWriterLock(path)
    holder.acquire()
    try:
        contender = SingleWriterLock(path, poll_interval=0.01)
        with pytest.raises(ResourceBusy):
            contender.acquire(timeout=0.2)
    finally:
        holder.release()
    assert isinstance(ResourceBusy("x"), PmagentError)
