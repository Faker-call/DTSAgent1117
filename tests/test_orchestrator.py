from __future__ import annotations

import pytest

from pmagent.errors import BudgetExceeded
from pmagent.orchestrator import Budget, Pipeline, Stage, StageStatus


def test_pipeline_runs_all_stages() -> None:
    order: list[str] = []

    def make(name: str):
        def _fn(ctx: dict) -> str:
            order.append(name)
            return name

        return _fn

    pipeline = Pipeline([Stage("a", make("a")), Stage("b", make("b"))])
    result = pipeline.run({})
    assert result.status is StageStatus.OK
    assert order == ["a", "b"]
    assert [s.status for s in result.stages] == [StageStatus.OK, StageStatus.OK]


def test_flaky_stage_retries_then_succeeds() -> None:
    attempts = {"n": 0}

    def flaky(ctx: dict) -> str:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    pipeline = Pipeline([Stage("flaky", flaky, max_retries=2)])
    result = pipeline.run({})
    assert result.status is StageStatus.OK
    assert result.stages[0].attempts == 2


def test_required_stage_failure_stops_pipeline() -> None:
    reached = {"b": False}

    def always_fail(ctx: dict) -> None:
        raise RuntimeError("nope")

    def b(ctx: dict) -> str:
        reached["b"] = True
        return "b"

    pipeline = Pipeline([Stage("a", always_fail, max_retries=1), Stage("b", b)])
    result = pipeline.run({})
    assert result.status is StageStatus.FAILED
    assert reached["b"] is False
    assert result.stages[-1].attempts == 2


def test_optional_stage_failure_degrades() -> None:
    def always_fail(ctx: dict) -> None:
        raise RuntimeError("nope")

    pipeline = Pipeline(
        [Stage("optional", always_fail, max_retries=0, optional=True), Stage("b", lambda c: "b")]
    )
    result = pipeline.run({})
    assert result.status is StageStatus.DEGRADED
    assert result.degraded_reasons
    assert "optional" in result.degraded_reasons[0]
    assert [s.status for s in result.stages] == [StageStatus.DEGRADED, StageStatus.OK]


def test_postcondition_failure_triggers_retry() -> None:
    attempts = {"n": 0}

    def fn(ctx: dict) -> int:
        attempts["n"] += 1
        return attempts["n"]

    pipeline = Pipeline([Stage("p", fn, max_retries=1, postcondition=lambda v: v >= 2)])
    result = pipeline.run({})
    assert result.status is StageStatus.OK
    assert attempts["n"] == 2


def test_budget_exceeded_propagates() -> None:
    def burn(ctx: dict) -> None:
        ctx["budget"].consume(10_000)

    pipeline = Pipeline([Stage("burn", burn)])
    budget = Budget(max_tokens=100)
    with pytest.raises(BudgetExceeded):
        pipeline.run({"budget": budget}, budget=budget)


def test_wall_clock_budget() -> None:
    budget = Budget(max_wall_seconds=0)
    with pytest.raises(BudgetExceeded):
        budget.check()
