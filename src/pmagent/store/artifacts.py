"""本地状态与交付物目录约定。

.pmagent/   本地运行状态（不进版本库）：索引、会话、运行记录、锁、追踪
artifacts/  交付物：定位报告、补丁工件
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_STATE_ROOT = Path(".pmagent")
DEFAULT_ARTIFACTS_ROOT = Path("artifacts")


class ArtifactStore:
    def __init__(
        self,
        state_root: Path | str = DEFAULT_STATE_ROOT,
        artifacts_root: Path | str = DEFAULT_ARTIFACTS_ROOT,
    ) -> None:
        self.state_root = Path(state_root)
        self.artifacts_root = Path(artifacts_root)

    # -- 本地状态 ---------------------------------------------------------
    @property
    def sessions_dir(self) -> Path:
        return self.state_root / "sessions"

    @property
    def runs_dir(self) -> Path:
        return self.state_root / "runs"

    @property
    def locks_dir(self) -> Path:
        return self.state_root / "locks"

    @property
    def traces_dir(self) -> Path:
        return self.state_root / "traces"

    def session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def trace_path(self, run_id: str) -> Path:
        return self.traces_dir / f"{run_id}.jsonl"

    def lock_path(self, name: str) -> Path:
        return self.locks_dir / f"{name}.lock"

    # -- 交付物 -----------------------------------------------------------
    @property
    def reports_dir(self) -> Path:
        return self.artifacts_root / "reports"

    @property
    def patches_dir(self) -> Path:
        return self.artifacts_root / "patches"

    def report_path(self, run_id: str, fmt: str = "md") -> Path:
        return self.reports_dir / f"{run_id}.{fmt}"

    def patch_dir(self, run_id: str) -> Path:
        return self.patches_dir / run_id

    def ensure_layout(self) -> ArtifactStore:
        for path in (
            self.sessions_dir,
            self.runs_dir,
            self.locks_dir,
            self.traces_dir,
            self.reports_dir,
            self.patches_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self
