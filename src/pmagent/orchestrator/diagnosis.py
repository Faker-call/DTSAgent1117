"""诊断编排：分阶段驱动 + 证据账本 + 进度治理 + 不可跳过校验。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from pmagent.agents.diagnosis import DiagnosisAgent
from pmagent.contracts import (
    IntentDecision,
    LocalizationReport,
    ProblemContext,
    ProgressSnapshot,
    SubAgentResult,
)
from pmagent.errors import LocalizationFailed
from pmagent.memory import BudgetLedger, EvidenceLedger, RunEventLog
from pmagent.orchestrator.multiagent import Coordinator, RetrievalAgent, SubAgentRuntime
from pmagent.safety import (
    GovernorConfig,
    ProgressGovernor,
    ValidationSuite,
    default_suite,
    record_progress,
)


@dataclass
class DiagnosisOutcome:
    report: LocalizationReport
    status: str = "ok"
    reasons: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    snapshots: list[ProgressSnapshot] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        return self.status == "degraded"


class DiagnosisRunner:
    def __init__(
        self,
        agent: DiagnosisAgent,
        *,
        ledger: EvidenceLedger | None = None,
        governor: ProgressGovernor | None = None,
        events: RunEventLog | None = None,
        validators: ValidationSuite | None = None,
        governor_config: GovernorConfig | None = None,
        budget: BudgetLedger | None = None,
    ) -> None:
        self.agent = agent
        self.ledger = ledger if ledger is not None else EvidenceLedger()
        self.governor = governor or ProgressGovernor(governor_config)
        self.events = events
        self.validators = validators
        self.budget = budget or BudgetLedger(total=120_000)

    def run(self, context: ProblemContext, *, limit: int = 3) -> DiagnosisOutcome:
        started = time.perf_counter()
        reasons: list[str] = []
        snapshots: list[ProgressSnapshot] = []
        evidence_ids: list[str] = []

        self._stage("candidates", "started")
        stage, candidates, slices = self.agent.prepare(context)
        self.budget.allocate("candidates", 40_000)
        self.budget.consume(
            "candidates", sum(len(item.body) for item in slices) // 4
        )
        for index, candidate in enumerate(candidates):
            item = self.ledger.register(
                kind="code",
                ref={
                    "path": candidate.file,
                    "line_start": candidate.line_start,
                    "line_end": candidate.line_end,
                },
                summary=candidate.symbol,
                body=slices[index].body if index < len(slices) else candidate.text,
                symbol=candidate.symbol,
                score=candidate.retrieval_score,
                channels=sorted(candidate.sources),
            )
            evidence_ids.append(item.evidence_id)
        first = ProgressSnapshot(
            stage="candidates",
            call_index=1,
            new_evidence_ids=evidence_ids,
            new_locations=sorted({candidate.file for candidate in candidates}),
            new_evidence_classes=sorted(
                {source for candidate in candidates for source in candidate.sources}
            ),
            candidate_delta={"added": len(candidates), "removed": 0},
        )
        decision = self.governor.observe(
            first,
            stage_calls=1,
            total_calls=1,
            elapsed_s=time.perf_counter() - started,
        )
        self._record(first)
        snapshots.append(first)
        if decision.action == "stop":
            self._stage("candidates", "finished")
            raise LocalizationFailed(
                f"进度治理中止：{decision.reason}", hint="缩小范围或补充证据后重试"
            )
        if decision.action in {"switch", "converge"}:
            reasons.append(decision.reason)
        self._stage("candidates", "finished")

        self._stage("hypothesize", "started")
        hypotheses = self.agent.hypothesize(context, stage.stage, slices)  # type: ignore[attr-defined]
        self.budget.allocate("hypothesize", 60_000)
        self.budget.consume(
            "hypothesize",
            sum(
                len(hypothesis.statement)
                + sum(len(ref.ref) for ref in hypothesis.supporting)
                for hypothesis in hypotheses
            )
            // 4,
        )
        second = ProgressSnapshot(
            stage="hypothesize",
            call_index=2,
            new_evidence_ids=[],
            new_evidence_classes=sorted(
                {kind for hypothesis in hypotheses for kind in hypothesis.evidence_classes}
            ),
            hypothesis_jaccard=self._jaccard(hypotheses),
            candidate_delta={"added": len(hypotheses), "removed": 0},
        )
        decision = self.governor.observe(
            second,
            stage_calls=2,
            total_calls=2,
            elapsed_s=time.perf_counter() - started,
        )
        self._record(second)
        snapshots.append(second)
        if decision.action == "stop":
            self._stage("hypothesize", "finished")
            raise LocalizationFailed(f"进度治理中止：{decision.reason}")
        if decision.action in {"switch", "converge"}:
            reasons.append(decision.reason)
            hypotheses = hypotheses[:1]
        self._stage("hypothesize", "finished")

        ranked = self.agent.rank(hypotheses, slices, limit=limit)
        report = self.agent.build_report(context, stage, ranked, started)

        suite = self.validators or default_suite(code_index=self.agent.code_index)
        outcomes = suite.run(report, context)
        failed = [item for item in outcomes if item.status == "failed"]
        if failed:
            details = "；".join(f"{item.name}: {item.detail}" for item in failed)
            raise LocalizationFailed(
                f"报告未通过不可跳过校验：{details}",
                hint="检查行号、文件与置信度后重试",
            )

        status = "degraded" if reasons else "ok"
        return DiagnosisOutcome(
            report=report,
            status=status,
            reasons=reasons,
            evidence_ids=evidence_ids,
            snapshots=snapshots,
        )

    # -- 内部 -------------------------------------------------------------
    def _record(self, snapshot: ProgressSnapshot) -> None:
        if self.events is not None:
            record_progress(self.events, snapshot)

    def _stage(self, name: str, status: str) -> None:
        if self.events is not None:
            self.events.append(f"stage_{status}", stage=name, status=status)

    @staticmethod
    def _jaccard(hypotheses) -> float:  # noqa: ANN001
        if not hypotheses:
            return 0.0
        first = set(hypotheses[0].symbols)
        others = [set(hypothesis.symbols) for hypothesis in hypotheses[1:]]
        if not others:
            return 1.0
        intersection = len(first & others[0])
        union = len(first | others[0]) or 1
        return round(intersection / union, 3)


@dataclass
class MultiAgentDiagnosis:
    """多 Agent 入口：先并行扇出检索分支，再由单 Agent 收敛成定位报告。"""

    runner: DiagnosisRunner
    retriever: object
    scopes: list[str] = field(default_factory=lambda: ["code", "symbol"])
    deadline_ms: int = 30_000

    def run(self, context: ProblemContext, *, limit: int = 3) -> DiagnosisOutcome:
        runtime = SubAgentRuntime()
        runtime.register(
            "retrieval", RetrievalAgent(self.retriever, self.runner.ledger)
        )
        runtime.register("diagnosis", _noop_diagnosis)
        coordinator = Coordinator(runtime, ledger=self.runner.ledger)
        dispatch = coordinator.dispatch(
            IntentDecision(primary="defect"),
            context.raw_query,
            problem_ref=context.id,
            scopes=self.scopes,
            deadline_ms=self.deadline_ms,
        )
        outcome = self.runner.run(context, limit=limit)
        outcome.evidence_ids = list(
            dict.fromkeys([*dispatch.evidence_ids, *outcome.evidence_ids])
        )
        outcome.reasons.append(
            f"多 Agent：分支 {dispatch.metrics.branches}，"
            f"加速 {dispatch.metrics.speedup}×，去重 {dispatch.metrics.duplicates}"
        )
        return outcome


def _noop_diagnosis(brief) -> SubAgentResult:  # noqa: ANN001
    return SubAgentResult(task_id=brief.task_id, agent_role="diagnosis", status="ok")


__all__ = ["DiagnosisOutcome", "DiagnosisRunner", "MultiAgentDiagnosis"]
