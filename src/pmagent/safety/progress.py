"""进度治理：快照、停滞/振荡/循环判定、分级处置与硬上限。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pmagent.contracts import ProgressSnapshot

Action = Literal["continue", "switch", "converge", "stop"]


@dataclass
class GovernorConfig:
    stall_rounds: int = 2
    thrash_rounds: int = 2
    loop_window: int = 3
    max_per_stage: int = 8
    max_per_run: int = 30
    wall_clock_s: float = 120.0


@dataclass
class GovernorDecision:
    action: Action
    reason: str
    verdict: str = "progress"


class ProgressGovernor:
    def __init__(self, config: GovernorConfig | None = None) -> None:
        self.config = config or GovernorConfig()
        self._stall_streak = 0
        self._thrash_streak = 0
        self._loop_streak = 0
        self._switch_count = 0
        self._last_delta = 0

    def observe(
        self,
        snapshot: ProgressSnapshot,
        *,
        stage_calls: int,
        total_calls: int,
        elapsed_s: float,
    ) -> GovernorDecision:
        config = self.config
        if stage_calls > config.max_per_stage:
            return self._decide(
                snapshot,
                "stop",
                f"阶段调用 {stage_calls} 次超过上限 {config.max_per_stage}",
                "stall",
            )
        if total_calls > config.max_per_run:
            return self._decide(
                snapshot,
                "stop",
                f"单次运行 {total_calls} 次超过上限 {config.max_per_run}",
                "stall",
            )
        if elapsed_s > config.wall_clock_s:
            return self._decide(
                snapshot,
                "stop",
                f"墙钟 {elapsed_s:.0f}s 超过 {config.wall_clock_s:.0f}s",
                "stall",
            )

        if snapshot.hypothesis_jaccard >= 0.9:
            self._loop_streak += 1
        else:
            self._loop_streak = 0
        if self._loop_streak >= config.loop_window or snapshot.duplicate_calls >= 3:
            return self._decide(
                snapshot, "converge", "重复提案被拒绝：连续提出相同假设，只输出报告与缺口", "loop"
            )

        delta = snapshot.candidate_delta.get("added", 0) - snapshot.candidate_delta.get(
            "removed", 0
        )
        if delta and self._last_delta and (delta > 0) != (self._last_delta > 0):
            self._thrash_streak += 1
        elif delta:
            self._thrash_streak = 0
        if delta:
            self._last_delta = delta
        if self._thrash_streak >= config.thrash_rounds:
            return self._decide(
                snapshot,
                self._escalate("switch"),
                "候选集反复增减（振荡），换路或强制收敛",
                "thrash",
            )

        no_gain = not (
            snapshot.new_evidence_ids
            or snapshot.new_locations
            or snapshot.new_evidence_classes
        )
        self._stall_streak = self._stall_streak + 1 if no_gain else 0
        if self._stall_streak >= config.stall_rounds:
            return self._decide(
                snapshot, self._escalate("switch"), "连续无新增证据/位置/类别（停滞）", "stall"
            )

        snapshot.verdict = "progress"
        snapshot.action = "none"
        return GovernorDecision(action="continue", reason="有新增进展", verdict="progress")

    def _escalate(self, action: Action) -> Action:
        """分级处置：第一次换路，第二次强制收敛。"""
        if self._switch_count == 0:
            self._switch_count += 1
            return "switch"
        return "converge"

    @staticmethod
    def _decide(
        snapshot: ProgressSnapshot, action: Action, reason: str, verdict: str
    ) -> GovernorDecision:
        snapshot.verdict = verdict  # type: ignore[assignment]
        snapshot.action = "taken" if action != "continue" else "none"
        snapshot.reason = reason
        return GovernorDecision(action=action, reason=reason, verdict=verdict)


def record_progress(log, snapshot: ProgressSnapshot) -> None:  # noqa: ANN001
    """把快照写进运行事件，供 `runs show` 展示与回溯。"""
    log.append("progress", **snapshot.model_dump(mode="json"))


__all__ = ["GovernorConfig", "GovernorDecision", "ProgressGovernor", "record_progress"]
