from __future__ import annotations

import time

from pmagent.contracts import IntentDecision, RetrievalHit, SubAgentResult, TaskBrief
from pmagent.memory import EvidenceLedger
from pmagent.orchestrator import (
    Coordinator,
    Critic,
    RetrievalAgent,
    SubAgentRuntime,
)


def _brief(role: str, task: str = "t1", tools: list[str] | None = None, deadline: int = 5000) -> TaskBrief:
    return TaskBrief(
        task_id=task,
        parent_run_id="run1",
        agent_role=role,  # type: ignore[arg-type]
        intent={"query": "tile seam", "scope": "code"},
        problem_context_ref="run1",
        allowed_tools=tools if tools is not None else [],
        deadline_ms=deadline,
    )


def _ok(task_id: str = "t1", role: str = "knowledge", evidence: list[str] | None = None):
    return SubAgentResult(
        task_id=task_id,
        agent_role=role,
        status="ok",
        evidence_ids=evidence or [],
    )


# -- 运行时 -----------------------------------------------------------------


def test_runtime_rejects_tool_outside_allowlist() -> None:
    runtime = SubAgentRuntime()
    runtime.register("knowledge", lambda brief: _ok())
    result = runtime.run("knowledge", _brief("knowledge", tools=["sandbox.run"]))
    assert result.status == "failed"
    assert "白名单" in (result.stopped_reason or "")


def test_runtime_unknown_role() -> None:
    runtime = SubAgentRuntime()
    result = runtime.run("ghost", _brief("knowledge"))
    assert result.status == "failed"


def test_runtime_role_mismatch() -> None:
    runtime = SubAgentRuntime()
    runtime.register("knowledge", lambda brief: _ok())
    result = runtime.run("knowledge", _brief("diagnosis"))
    assert result.status == "failed"
    assert "不一致" in (result.stopped_reason or "")


def test_runtime_timeout() -> None:
    runtime = SubAgentRuntime()

    def slow(brief: TaskBrief) -> SubAgentResult:
        time.sleep(0.2)
        return _ok(brief.task_id)

    runtime.register("knowledge", slow)
    result = runtime.run("knowledge", _brief("knowledge", deadline=50))
    assert result.status == "failed"
    assert result.stopped_reason == "timeout"
    assert runtime.history[-1].status == "failed"


def test_runtime_rejects_non_result() -> None:
    runtime = SubAgentRuntime()
    runtime.register("knowledge", lambda brief: {"ok": True})  # type: ignore[return-value]
    result = runtime.run("knowledge", _brief("knowledge"))
    assert result.status == "failed"
    assert "SubAgentResult" in (result.stopped_reason or "")


def test_runtime_records_history_and_cost() -> None:
    runtime = SubAgentRuntime()
    runtime.register("knowledge", lambda brief: _ok(evidence=["E1"]))
    result = runtime.run("knowledge", _brief("knowledge"))
    assert result.status == "ok"
    assert result.cost["wall_time_ms"] >= 0
    assert runtime.history[-1].evidence_count == 1


# -- 中心派发 ---------------------------------------------------------------


def test_coordinator_short_circuits_usage() -> None:
    runtime = SubAgentRuntime()
    runtime.register("knowledge", lambda brief: _ok(brief.task_id, evidence=["E1"]))
    coordinator = Coordinator(runtime)
    intent = IntentDecision(primary="usage", evidence_gate={"passed": True})
    result = coordinator.dispatch(intent, "how to", problem_ref="p1", evidence_sufficient=True)
    assert result.mode == "single"
    assert result.metrics.short_circuited
    assert result.metrics.dispatched == 1
    assert result.evidence_ids == ["E1"]


def test_coordinator_fans_out_for_defect() -> None:
    runtime = SubAgentRuntime()

    def retrieval(brief: TaskBrief) -> SubAgentResult:
        scope = brief.intent.get("scope")
        return _ok(brief.task_id, "retrieval", [f"E-{scope}"])

    runtime.register("retrieval", retrieval)
    runtime.register("diagnosis", lambda brief: _ok(brief.task_id, "diagnosis", ["E-diagnosis"]))
    coordinator = Coordinator(runtime)
    intent = IntentDecision(primary="defect", evidence_gate={"passed": True})
    result = coordinator.dispatch(intent, "crash", problem_ref="p1")
    assert result.mode == "multi"
    assert result.metrics.branches == 4
    assert len(result.results) == 5
    assert len(result.evidence_ids) == 5
    assert result.metrics.per_agent_evidence["retrieval"] == 4


def test_coordinator_dedupes_evidence() -> None:
    runtime = SubAgentRuntime()
    runtime.register("retrieval", lambda brief: _ok(brief.task_id, "retrieval", ["E1"]))
    runtime.register("diagnosis", lambda brief: _ok(brief.task_id, "diagnosis", ["E1", "E2"]))
    coordinator = Coordinator(runtime)
    result = coordinator.dispatch(
        IntentDecision(primary="defect", evidence_gate={"passed": True}),
        "crash",
        problem_ref="p1",
    )
    assert result.evidence_ids == ["E1", "E2"]
    assert result.metrics.duplicates == 4


def test_coordinator_detects_conflicts() -> None:
    runtime = SubAgentRuntime()
    runtime.register(
        "retrieval",
        lambda brief: SubAgentResult(
            task_id=brief.task_id,
            agent_role="retrieval",
            findings=[
                {"location": "a.cc:10", "conclusion": "空指针"},
                {"location": "a.cc:10", "conclusion": "越界"},
            ],
        ),
    )
    runtime.register("diagnosis", lambda brief: _ok(brief.task_id, "diagnosis"))
    coordinator = Coordinator(runtime)
    result = coordinator.dispatch(
        IntentDecision(primary="defect"), "crash", problem_ref="p1", scopes=["code"]
    )
    assert result.conflicts
    assert result.conflicts[0]["location"] == "a.cc:10"


def test_coordinator_isolates_failures() -> None:
    runtime = SubAgentRuntime()

    def broken(brief: TaskBrief) -> SubAgentResult:
        raise RuntimeError("boom")

    runtime.register("retrieval", broken)
    runtime.register("diagnosis", lambda brief: _ok(brief.task_id, "diagnosis", ["E1"]))
    coordinator = Coordinator(runtime)
    result = coordinator.dispatch(
        IntentDecision(primary="defect"), "crash", problem_ref="p1", scopes=["code"]
    )
    assert result.degraded
    assert result.evidence_ids == ["E1"]
    assert any(item.status == "failed" for item in result.results)


def test_parallel_faster_than_serial() -> None:
    runtime = SubAgentRuntime()

    def slow(brief: TaskBrief) -> SubAgentResult:
        time.sleep(0.15)
        return _ok(brief.task_id, "retrieval", [f"E-{brief.task_id}"])

    runtime.register("retrieval", slow)
    runtime.register("diagnosis", lambda brief: _ok(brief.task_id, "diagnosis"))
    coordinator = Coordinator(runtime)
    result = coordinator.dispatch(
        IntentDecision(primary="defect"), "crash", problem_ref="p1"
    )
    assert result.metrics.speedup > 2.0


# -- 检索 Agent 与证据账本 ---------------------------------------------------


class FakeRetriever:
    def __init__(self, rounds: list[list[RetrievalHit]]) -> None:
        self.rounds = rounds
        self.calls = 0

    def search(self, query, **kwargs):  # noqa: ANN001
        index = min(self.calls, len(self.rounds) - 1)
        self.calls += 1
        return self.rounds[index]


def _hit(index: int, kind: str = "code") -> RetrievalHit:
    return RetrievalHit(
        chunk_id=f"c{index}",
        kind=kind,  # type: ignore[arg-type]
        path="a.cc",
        line_start=index,
        symbol="pm::Alpha",
        score=0.5,
        snippet="body",
    )


def test_retrieval_agent_multi_round() -> None:
    retriever = FakeRetriever([[], [_hit(1), _hit(2), _hit(3)]])
    ledger = EvidenceLedger()
    agent = RetrievalAgent(retriever, ledger, rounds=2, min_hits=3)  # type: ignore[arg-type]
    result = agent(_brief("retrieval"))
    assert result.status == "ok"
    assert len(result.evidence_ids) == 3
    assert result.findings[0]["searched_rounds"] == 2
    assert len(ledger) == 3


def test_retrieval_agent_insufficient() -> None:
    retriever = FakeRetriever([[], []])
    agent = RetrievalAgent(retriever, EvidenceLedger(), rounds=2)  # type: ignore[arg-type]
    result = agent(_brief("retrieval"))
    assert result.status == "degraded"
    assert result.stopped_reason == "insufficient"


def test_ledger_ids_are_globally_unique() -> None:
    ledger = EvidenceLedger()
    retriever = FakeRetriever([[_hit(1), _hit(2)]])
    first = RetrievalAgent(retriever, ledger, rounds=1)  # type: ignore[arg-type]
    second = RetrievalAgent(retriever, ledger, rounds=1)  # type: ignore[arg-type]
    a = first(_brief("retrieval", "t1"))
    b = second(_brief("retrieval", "t2"))
    assert len(set(a.evidence_ids + b.evidence_ids)) == 4


# -- 评审 -------------------------------------------------------------------


def test_critic_trigger_rules() -> None:
    assert Critic.should_trigger(confidence=0.7)
    assert not Critic.should_trigger(confidence=0.5)
    assert Critic.should_trigger(confidence=0.9, has_conflict=True)
    assert Critic.should_trigger(confidence=0.9, cross_module=True)


def test_critic_support_and_challenge() -> None:
    ledger = EvidenceLedger()
    item = ledger.register(kind="code", ref={"path": "a.cc"}, summary="Alpha boundary")
    critic = Critic()
    support = critic.review("Alpha boundary bug", [item])
    assert support["verdict"] == "support"
    challenge = critic.review("完全无关的结论", [item])
    assert challenge["verdict"] == "challenge"
    assert challenge["missing_evidence"]
