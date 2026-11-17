"""阶段化流水线引擎（M02 Coordinator 的骨架部分）。

设计要点：
- 阶段推进与重试由代码控制，模型无权宣布阶段完成；
- 失败分为两种：可降级（optional）与致命。可降级阶段失败后流水线继续，
  整体标记为 DEGRADED；致命阶段失败立即终止。
- 预算在每个阶段前后检查，超限抛出 BudgetExceeded。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pmagent.errors import BudgetExceeded, PmagentError

Context = dict[str, Any]
StageFn = Callable[[Context], Any]
Postcondition = Callable[[Any], bool]


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class Budget:
    max_tokens: int = 400_000
    max_wall_seconds: float = 120.0
    tokens_used: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.tokens_used)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_wall_seconds - self.elapsed_seconds)

    def consume(self, tokens: int = 0) -> None:
        self.tokens_used += max(0, tokens)
        self.check()

    def check(self) -> None:
        if self.tokens_used > self.max_tokens:
            raise BudgetExceeded(
                f"token 预算用尽（{self.tokens_used}/{self.max_tokens}）",
                hint="提高 budget.max_tokens 或缩小检索范围",
            )
        if self.max_wall_seconds >= 0 and self.elapsed_seconds >= self.max_wall_seconds:
            raise BudgetExceeded(
                f"运行超时（{self.elapsed_seconds:.1f}s ≥ {self.max_wall_seconds:.1f}s）",
                hint="提高 budget.max_wall_seconds 或使用 --resume 续跑",
            )


@dataclass
class Stage:
    name: str
    fn: StageFn
    max_retries: int = 2
    optional: bool = False
    postcondition: Postcondition | None = None

    def validate(self, result: Any) -> bool:
        return True if self.postcondition is None else bool(self.postcondition(result))


@dataclass
class StageResult:
    name: str
    status: StageStatus
    attempts: int
    duration_ms: int
    error: str | None = None


@dataclass
class PipelineResult:
    status: StageStatus
    stages: list[StageResult] = field(default_factory=list)
    degraded_reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is StageStatus.OK

    @property
    def degraded(self) -> bool:
        return self.status is StageStatus.DEGRADED


class Pipeline:
    def __init__(self, stages: list[Stage], *, name: str = "pipeline") -> None:
        self.stages = list(stages)
        self.name = name

    def run(
        self,
        context: Context | None = None,
        *,
        budget: Budget | None = None,
        trace: Any | None = None,
    ) -> PipelineResult:
        ctx: Context = context if context is not None else {}
        budget = budget or Budget()
        result = PipelineResult(status=StageStatus.OK)

        for stage in self.stages:
            budget.check()
            stage_result = self._run_stage(stage, ctx, budget, trace)
            result.stages.append(stage_result)

            if stage_result.status is StageStatus.DEGRADED:
                result.degraded_reasons.append(f"{stage.name}: {stage_result.error}")
                result.status = StageStatus.DEGRADED
            elif stage_result.status is StageStatus.FAILED:
                result.status = StageStatus.FAILED
                return result

        return result

    def _run_stage(
        self,
        stage: Stage,
        ctx: Context,
        budget: Budget,
        trace: Any | None,
    ) -> StageResult:
        started = time.monotonic()
        last_error = "未知错误"
        attempts = 0

        for attempt in range(1, stage.max_retries + 2):
            attempts = attempt
            self._trace(trace, "stage_started", stage=stage.name, attempt=attempt)
            try:
                output = stage.fn(ctx)
                if not stage.validate(output):
                    raise PmagentError(f"阶段 {stage.name} 未满足完成条件")
                duration = int((time.monotonic() - started) * 1000)
                self._trace(
                    trace, "stage_finished", stage=stage.name, status="ok", attempt=attempt
                )
                return StageResult(stage.name, StageStatus.OK, attempts, duration)
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - 阶段内任何异常都进入重试
                last_error = str(exc) or exc.__class__.__name__
                self._trace(
                    trace,
                    "stage_finished",
                    stage=stage.name,
                    status="error",
                    attempt=attempt,
                    error=last_error,
                )

        duration = int((time.monotonic() - started) * 1000)
        status = StageStatus.DEGRADED if stage.optional else StageStatus.FAILED
        return StageResult(stage.name, status, attempts, duration, last_error)

    @staticmethod
    def _trace(trace: Any | None, event: str, **payload: Any) -> None:
        if trace is not None and hasattr(trace, "record"):
            trace.record(event, **payload)
