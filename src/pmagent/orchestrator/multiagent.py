"""多 Agent 编排：子 Agent 运行时、中心派发、并行扇出、证据合并与评审。"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from pmagent.authz.policy import AGENT_TOOLS
from pmagent.contracts import EvidenceItem, IntentDecision, SubAgentResult, TaskBrief
from pmagent.knowledge.retrieval import HybridRetriever
from pmagent.memory import EvidenceLedger

MAX_PARALLEL = 4


@dataclass(frozen=True)
class AgentSpec:
    role: str
    allowed_tools: frozenset[str]
    deadline_ms: int = 60_000
    budget_tokens: int = 40_000


AGENT_SPECS: dict[str, AgentSpec] = {
    "knowledge": AgentSpec("knowledge", frozenset(AGENT_TOOLS["knowledge"]), 30_000, 40_000),
    "diagnosis": AgentSpec("diagnosis", frozenset(AGENT_TOOLS["diagnosis"]), 90_000, 120_000),
    "retrieval": AgentSpec("retrieval", frozenset(AGENT_TOOLS["retrieval"]), 40_000, 50_000),
    "patch": AgentSpec("patch", frozenset(AGENT_TOOLS["patch"]), 60_000, 80_000),
    "critic": AgentSpec("critic", frozenset(AGENT_TOOLS["critic"]), 20_000, 20_000),
}

AgentFn = Callable[[TaskBrief], SubAgentResult]


@dataclass
class SubAgentTrace:
    role: str
    task_id: str
    status: str
    duration_ms: int
    evidence_count: int = 0


class SubAgentRuntime:
    """为每个子 Agent 加载角色配置、校验白名单、隔离预算与截止时间。"""

    def __init__(self, trace: Any | None = None) -> None:
        self._agents: dict[str, AgentFn] = {}
        self.trace = trace
        self.history: list[SubAgentTrace] = []

    def register(self, role: str, fn: AgentFn) -> None:
        if role not in AGENT_SPECS:
            raise ValueError(f"未知角色：{role}")
        self._agents[role] = fn

    def run(self, agent_role: str, brief: TaskBrief) -> SubAgentResult:
        spec = AGENT_SPECS.get(agent_role)
        if spec is None:
            return self._failed(brief, f"未知角色 {agent_role}")
        if brief.agent_role != agent_role:
            return self._failed(brief, "简报与角色不一致")
        if not set(brief.allowed_tools) <= spec.allowed_tools:
            illegal = sorted(set(brief.allowed_tools) - spec.allowed_tools)
            return self._failed(brief, f"工具不在白名单：{illegal}")
        fn = self._agents.get(agent_role)
        if fn is None:
            return self._failed(brief, f"角色 {agent_role} 未注册")

        deadline_ms = min(brief.deadline_ms or spec.deadline_ms, spec.deadline_ms)
        started = time.monotonic()
        if self.trace is not None and hasattr(self.trace, "record"):
            self.trace.record("subagent_started", agent_role=agent_role, task_id=brief.task_id)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn, brief)
            try:
                result = future.result(timeout=max(0.001, deadline_ms / 1000))
            except TimeoutError:
                future.cancel()
                duration = int((time.monotonic() - started) * 1000)
                self._history(agent_role, brief, "failed", duration, 0)
                return SubAgentResult(
                    task_id=brief.task_id,
                    agent_role=agent_role,
                    status="failed",
                    stopped_reason="timeout",
                )
            except Exception as exc:  # noqa: BLE001 - 子 Agent 异常不外溢
                duration = int((time.monotonic() - started) * 1000)
                self._history(agent_role, brief, "failed", duration, 0)
                return SubAgentResult(
                    task_id=brief.task_id,
                    agent_role=agent_role,
                    status="failed",
                    stopped_reason=f"{type(exc).__name__}: {exc}",
                )

        duration = int((time.monotonic() - started) * 1000)
        if not isinstance(result, SubAgentResult):
            return self._failed(brief, "返回值不是 SubAgentResult")
        if result.task_id != brief.task_id:
            return self._failed(brief, "返回值 task_id 不一致")
        result.cost.setdefault("wall_time_ms", float(duration))
        self._history(agent_role, brief, result.status, duration, len(result.evidence_ids))
        if self.trace is not None and hasattr(self.trace, "record"):
            self.trace.record(
                "subagent_finished",
                agent_role=agent_role,
                task_id=brief.task_id,
                status=result.status,
                duration_ms=duration,
            )
        return result

    def _failed(self, brief: TaskBrief, reason: str) -> SubAgentResult:
        return SubAgentResult(
            task_id=brief.task_id,
            agent_role=brief.agent_role,
            status="failed",
            stopped_reason=reason,
        )

    def _history(
        self, role: str, brief: TaskBrief, status: str, duration: int, evidence: int
    ) -> None:
        self.history.append(
            SubAgentTrace(role, brief.task_id, status, duration, evidence)
        )


@dataclass
class DispatchMetrics:
    dispatched: int = 0
    branches: int = 0
    short_circuited: bool = False
    serial_ms: int = 0
    parallel_ms: int = 0
    duplicates: int = 0
    per_agent_evidence: dict[str, int] = field(default_factory=dict)

    @property
    def speedup(self) -> float:
        if self.parallel_ms <= 0:
            return 1.0
        return round(self.serial_ms / self.parallel_ms, 2)


@dataclass
class DispatchResult:
    mode: str
    route: str
    results: list[SubAgentResult] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    metrics: DispatchMetrics = field(default_factory=DispatchMetrics)

    @property
    def degraded(self) -> bool:
        return any(result.status != "ok" for result in self.results)


class Coordinator:
    def __init__(
        self,
        runtime: SubAgentRuntime,
        *,
        ledger: EvidenceLedger | None = None,
        critic: Critic | None = None,
    ) -> None:
        self.runtime = runtime
        self.ledger = ledger if ledger is not None else EvidenceLedger()
        self.critic = critic

    def route(self, intent: IntentDecision) -> str:
        return "knowledge" if intent.primary in {"usage", "concept"} else "diagnosis"

    def dispatch(
        self,
        intent: IntentDecision,
        query: str,
        *,
        problem_ref: str,
        evidence_sufficient: bool = False,
        scopes: list[str] | None = None,
        deadline_ms: int = 60_000,
    ) -> DispatchResult:
        route = self.route(intent)
        metrics = DispatchMetrics()
        started = time.monotonic()

        if route == "knowledge" and evidence_sufficient:
            metrics.short_circuited = True
            metrics.dispatched = 1
            brief = self._brief("knowledge", query, problem_ref, intent, deadline_ms)
            result = self.runtime.run("knowledge", brief)
            metrics.branches = 1
            metrics.serial_ms = int(result.cost.get("wall_time_ms", 0))
            metrics.parallel_ms = metrics.serial_ms
            return self._collect("single", route, [result], metrics)

        metrics.dispatched = 1
        branches = scopes or (
            ["code", "docs", "tickets", "cases"] if route == "diagnosis" else ["docs"]
        )
        briefs = [
            self._brief("retrieval", query, problem_ref, intent, deadline_ms, scope=scope)
            for scope in branches
        ]
        results = self.fanout(briefs, deadline_ms=deadline_ms)
        metrics.branches = len(results)
        metrics.serial_ms = int(sum(result.cost.get("wall_time_ms", 0) for result in results))
        metrics.parallel_ms = int((time.monotonic() - started) * 1000)

        if route == "diagnosis":
            diagnosis_brief = self._brief(
                "diagnosis", query, problem_ref, intent, deadline_ms
            )
            results.append(self.runtime.run("diagnosis", diagnosis_brief))
            metrics.serial_ms += int(results[-1].cost.get("wall_time_ms", 0))

        return self._collect("multi", route, results, metrics)

    def fanout(self, briefs: list[TaskBrief], *, deadline_ms: int) -> list[SubAgentResult]:
        if not briefs:
            return []
        results: list[SubAgentResult] = []
        remaining = deadline_ms
        with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(briefs))) as pool:
            futures = [
                pool.submit(self.runtime.run, brief.agent_role, brief) for brief in briefs
            ]
            for brief, future in zip(briefs, futures, strict=True):
                try:
                    results.append(future.result(timeout=max(0.001, remaining / 1000)))
                except TimeoutError:
                    results.append(
                        SubAgentResult(
                            task_id=brief.task_id,
                            agent_role=brief.agent_role,
                            status="failed",
                            stopped_reason="timeout",
                        )
                    )
        return results

    # -- 内部 -------------------------------------------------------------
    @staticmethod
    def _brief(
        role: str,
        query: str,
        problem_ref: str,
        intent: IntentDecision,
        deadline_ms: int,
        *,
        scope: str | None = None,
    ) -> TaskBrief:
        payload = {"query": query, "scope": scope or "default"}
        return TaskBrief(
            task_id=f"{role}-{scope or 'default'}-{int(time.time() * 1000) % 100000}",
            parent_run_id=problem_ref,
            agent_role=role,  # type: ignore[arg-type]
            intent=payload,
            problem_context_ref=problem_ref,
            allowed_tools=sorted(AGENT_SPECS[role].allowed_tools),
            budget={"tokens": float(AGENT_SPECS[role].budget_tokens)},
            deadline_ms=deadline_ms,
        )

    def _collect(
        self, mode: str, route: str, results: list[SubAgentResult], metrics: DispatchMetrics
    ) -> DispatchResult:
        seen: set[str] = set()
        ordered: list[str] = []
        findings: list[dict] = []
        for result in results:
            metrics.per_agent_evidence[result.agent_role] = metrics.per_agent_evidence.get(
                result.agent_role, 0
            ) + len(result.evidence_ids)
            for evidence_id in result.evidence_ids:
                if evidence_id in seen:
                    metrics.duplicates += 1
                    continue
                seen.add(evidence_id)
                ordered.append(evidence_id)
            findings.extend(result.findings)
        conflicts = self._conflicts(findings)
        return DispatchResult(
            mode=mode,
            route=route,
            results=results,
            evidence_ids=ordered,
            conflicts=conflicts,
            metrics=metrics,
        )

    @staticmethod
    def _conflicts(findings: list[dict]) -> list[dict]:
        grouped: dict[str, set[str]] = {}
        for finding in findings:
            location = finding.get("location")
            conclusion = finding.get("conclusion")
            if not location or not conclusion:
                continue
            grouped.setdefault(str(location), set()).add(str(conclusion))
        return [
            {"location": location, "conclusions": sorted(conclusions)}
            for location, conclusions in grouped.items()
            if len(conclusions) > 1
        ]


class RetrievalAgent:
    """检索策略 Agent：查询规划 + 多轮补检，只返回命中，不产出结论。"""

    def __init__(
        self,
        retriever: HybridRetriever,
        ledger: EvidenceLedger | None = None,
        *,
        rounds: int = 2,
        min_hits: int = 3,
    ) -> None:
        self.retriever = retriever
        self.ledger = ledger if ledger is not None else EvidenceLedger()
        self.rounds = rounds
        self.min_hits = min_hits

    def __call__(self, brief: TaskBrief) -> SubAgentResult:
        query = str(brief.intent.get("query", ""))
        scope = str(brief.intent.get("scope", "default"))
        result = SubAgentResult(task_id=brief.task_id, agent_role="retrieval")
        hits = self.retriever.search(query, intent="localize", limit=8)
        searched_rounds = 1
        if len(hits) < self.min_hits and self.rounds > 1:
            supplement = f"{query} {scope}"
            hits = self.retriever.search(supplement, intent="localize", limit=8)
            searched_rounds = 2
        if not hits:
            result.status = "degraded"
            result.stopped_reason = "insufficient"
            return result

        for hit in hits:
            item = self.ledger.register(
                kind="code" if hit.kind == "code" else "doc",
                ref={"path": hit.path or hit.chunk_id, "line_start": hit.line_start},
                summary=f"{hit.symbol or hit.path or ''}".strip(),
                body=hit.snippet,
                score=hit.score,
                channels=hit.channels,
            )
            result.evidence_ids.append(item.evidence_id)
            result.findings.append(
                {
                    "kind": hit.kind,
                    "location": hit.path,
                    "score": hit.score,
                    "evidence_id": item.evidence_id,
                    "searched_rounds": searched_rounds,
                }
            )
        result.confidence = min(0.9, 0.4 + 0.05 * len(hits))
        return result


class Critic:
    """对抗评审：只给意见，不改结论、不调写工具。"""

    def __init__(self, gateway: Any | None = None) -> None:
        self.gateway = gateway

    @staticmethod
    def should_trigger(
        *, confidence: float, has_conflict: bool = False, cross_module: bool = False
    ) -> bool:
        if 0.6 <= confidence <= 0.75:
            return True
        return has_conflict or cross_module

    def review(self, claim: str, evidence: list[EvidenceItem]) -> dict[str, Any]:
        if self.gateway is not None:  # pragma: no cover - 需要在线模型
            return self._review_with_model(claim, evidence)
        tokens = {token.lower() for token in claim.split() if len(token) > 1}
        covered = [
            item
            for item in evidence
            if tokens & {token.lower() for token in item.summary.split()}
        ]
        if covered:
            return {
                "verdict": "support",
                "reasons": [f"{len(covered)} 条证据与结论关键词重合"],
                "missing_evidence": [],
            }
        return {
            "verdict": "challenge",
            "reasons": ["证据摘要与结论没有关键词重合"],
            "missing_evidence": ["能直接支撑结论的代码或日志证据"],
        }

    def _review_with_model(  # pragma: no cover
        self, claim: str, evidence: list[EvidenceItem]
    ) -> dict[str, Any]:
        from pydantic import BaseModel

        from pmagent.gateway import complete_json

        class _Review(BaseModel):
            verdict: str = "challenge"
            reasons: list[str] = []
            missing_evidence: list[str] = []

        prompt = f"结论：{claim}\n证据：{[item.summary for item in evidence]}\n评审是否被支持。"
        result = complete_json(
            self.gateway, [{"role": "user", "content": prompt}], _Review, role="verifier"
        )
        return result.value.model_dump()


__all__ = [
    "AGENT_SPECS",
    "AgentSpec",
    "Coordinator",
    "Critic",
    "DispatchMetrics",
    "DispatchResult",
    "RetrievalAgent",
    "SubAgentRuntime",
    "SubAgentTrace",
]
