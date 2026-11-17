"""跨模块数据契约。

所有模块间传递的数据都使用这里的 pydantic 模型，序列化为 JSON。
约定：字段可增不改；新增字段必须带默认值，保证旧数据可反序列化。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class Contract(BaseModel):
    """契约基类：禁止未知字段，便于尽早暴露拼写错误。"""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# 问题上下文
# --------------------------------------------------------------------------


class Product(Contract):
    sdk_version: str | None = None
    engine_commit: str | None = None
    platform: str | None = None
    render_backend: str | None = None
    device: str | None = None
    os: str | None = None


class Symptom(Contract):
    category: str = "unknown"
    description: str
    expected: str | None = None
    actual: str | None = None
    severity: Literal["low", "medium", "high", "blocker"] = "medium"


class LogEvidence(Contract):
    source_path: str
    line_count: int = 0
    templates: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    time_range: tuple[str, str] | None = None


class StackFrame(Contract):
    index: int
    address: str | None = None
    function: str | None = None
    file: str | None = None
    line: int | None = None


class StackEvidence(Contract):
    source_path: str
    frames: list[StackFrame] = Field(default_factory=list)
    symbolication_status: Literal["symbolicated", "partial", "unsupported"] = "unsupported"


class CrashEvidence(Contract):
    dump_path: str
    signal: str | None = None
    fault_addr: str | None = None
    fault_thread: str | None = None


class VisualEvidence(Contract):
    source_path: str
    kind: Literal["image", "video"] = "image"
    features: list[str] = Field(default_factory=list)
    regions: list[dict[str, Any]] = Field(default_factory=list)


class ReproEvidence(Contract):
    root_path: str
    build_cmd: str | None = None
    run_cmd: str | None = None
    sandbox_result: dict[str, Any] | None = None


class TicketRef(Contract):
    ticket_id: str
    similarity: float = 0.0
    resolution_summary: str | None = None


class EvidenceRequest(Contract):
    needed: str
    purpose: str


class RedactionRecord(Contract):
    rules_applied: list[str] = Field(default_factory=list)
    local_mapping_ref: str | None = None


class ProblemContext(Contract):
    id: str
    created_at: datetime = Field(default_factory=utcnow)
    source: Literal["cli", "api", "batch"] = "cli"
    raw_query: str = ""
    product: Product = Field(default_factory=Product)
    symptoms: list[Symptom] = Field(default_factory=list)
    logs: list[LogEvidence] = Field(default_factory=list)
    stacks: list[StackEvidence] = Field(default_factory=list)
    crash: CrashEvidence | None = None
    visuals: list[VisualEvidence] = Field(default_factory=list)
    repro: ReproEvidence | None = None
    tickets: list[TicketRef] = Field(default_factory=list)
    missing: list[EvidenceRequest] = Field(default_factory=list)
    redaction: RedactionRecord = Field(default_factory=RedactionRecord)


# --------------------------------------------------------------------------
# 定位与补丁
# --------------------------------------------------------------------------


class CodeLocation(Contract):
    file: str
    line_start: int
    line_end: int
    symbol: str | None = None


class EvidenceRef(Contract):
    evidence_type: Literal[
        "stack_frame", "log_template", "visual_region", "code_symbol", "commit", "ticket", "doc"
    ]
    ref: str
    weight: float = 1.0
    explanation: str | None = None


class CallChainLink(Contract):
    caller: str
    callee: str
    site: str | None = None


class SuspectCommit(Contract):
    sha: str
    subject: str = ""
    author_date: str | None = None
    relevance: float = 0.0


class Hypothesis(Contract):
    id: str
    statement: str
    pipeline_stage: str = "unknown"
    symbols: list[str] = Field(default_factory=list)
    code_locations: list[CodeLocation] = Field(default_factory=list)
    call_chain: list[CallChainLink] = Field(default_factory=list)
    suspect_commits: list[SuspectCommit] = Field(default_factory=list)
    supporting: list[EvidenceRef] = Field(default_factory=list)
    contradicting: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = 0.0
    confidence_basis: str = ""
    evidence_classes: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)
    status: Literal["proposed", "verified", "rejected"] = "proposed"


class Triage(Contract):
    stage: str
    rationale: str = ""


class Uncertainty(Contract):
    level: Literal["low", "medium", "high"] = "medium"
    note: str = ""


class Provenance(Contract):
    models: list[str] = Field(default_factory=list)
    index_version: str | None = None
    prompt_versions: list[str] = Field(default_factory=list)
    duration_ms: int = 0


class LocalizationReport(Contract):
    problem_context_ref: str
    triage: Triage
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    top_root_cause_ref: str | None = None
    uncertainty: Uncertainty = Field(default_factory=Uncertainty)
    next_actions: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)


class PatchRisk(Contract):
    abi: bool = False
    thread_safety: bool = False
    backends: list[str] = Field(default_factory=list)
    perf: bool = False
    note: str = ""


class PatchCheckStatus(Contract):
    status: Literal["passed", "failed", "skipped"] = "skipped"
    detail: str = ""


class PatchChecks(Contract):
    format: PatchCheckStatus = Field(default_factory=PatchCheckStatus)
    compile: PatchCheckStatus = Field(default_factory=PatchCheckStatus)
    unit_tests: PatchCheckStatus = Field(default_factory=PatchCheckStatus)


class Verification(Contract):
    steps: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)


class PatchArtifact(Contract):
    id: str
    localization_ref: str
    base_commit: str | None = None
    diff: str = ""
    files_changed: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    risk: PatchRisk = Field(default_factory=PatchRisk)
    checks: PatchChecks = Field(default_factory=PatchChecks)
    verification: Verification = Field(default_factory=Verification)
    alternatives: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 检索与知识
# --------------------------------------------------------------------------


class RetrievalHit(Contract):
    chunk_id: str
    kind: Literal["code", "doc", "ticket", "case"] = "code"
    path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    symbol: str | None = None
    score: float = 0.0
    channels: list[Literal["bm25", "vector", "graph"]] = Field(default_factory=list)
    snippet: str = ""


class DocRecord(Contract):
    id: str
    source_type: Literal["doc", "api_ref", "example", "ticket"] = "doc"
    title: str
    url: str | None = None
    version: str | None = None
    updated_at: datetime | None = None
    text: str = ""
    code_blocks: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# 记忆与运行
# --------------------------------------------------------------------------


class ProductSlots(Contract):
    sdk_version: str | None = None
    platform: str | None = None
    render_backend: str | None = None


class UserPrefs(Contract):
    format: Literal["text", "markdown", "json"] = "text"


class SessionState(Contract):
    id: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    slots: ProductSlots = Field(default_factory=ProductSlots)
    intent: str | None = None
    excluded_hypotheses: list[str] = Field(default_factory=list)
    confirmed_facts: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    turn_summary: str = ""
    user_prefs: UserPrefs = Field(default_factory=UserPrefs)


class RunEvent(Contract):
    run_id: str
    seq: int
    ts: datetime = Field(default_factory=utcnow)
    type: Literal[
        "stage_started",
        "stage_finished",
        "tool_called",
        "model_called",
        "artifact_written",
        "budget_updated",
        "interrupted",
        "progress",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceItem(Contract):
    evidence_id: str
    kind: Literal["code", "log", "doc", "ticket", "stack", "visual", "commit"]
    ref: dict[str, Any]
    symbol: str | None = None
    channels: list[str] = Field(default_factory=list)
    score: float = 0.0
    summary: str = ""
    rehydrate_hint: str | None = None


class Case(Contract):
    case_id: str
    symptom: str
    root_cause: str
    fix_commit: str | None = None
    sdk_version: str | None = None
    engine_commit_range: str | None = None
    confidence: float = 0.0
    reviewed_by: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None
    root_cause_file: str | None = None
    root_cause_symbol: str | None = None


class OutcomeSignal(Contract):
    kind: Literal["patch_merged", "ticket_closed", "repro_gone", "review_confirmed"]
    ref: dict[str, Any]
    observed_at: datetime = Field(default_factory=utcnow)
    confidence: float = 0.5


class CaseCandidate(Contract):
    case_id: str
    symptom: str
    evidence_refs: list[str] = Field(default_factory=list)
    root_cause: str
    fix_commit: str | None = None
    verification: Verification = Field(default_factory=Verification)
    dedupe_key: str
    source_signals: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# 执行权与多 Agent
# --------------------------------------------------------------------------


class ToolProposal(Contract):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    expected_evidence: list[str] = Field(default_factory=list)
    stage: str = ""


class TaskBrief(Contract):
    task_id: str
    parent_run_id: str
    agent_role: Literal["knowledge", "diagnosis", "retrieval", "patch", "critic"]
    intent: dict[str, Any] = Field(default_factory=dict)
    problem_context_ref: str
    allowed_tools: list[str] = Field(default_factory=list)
    budget: dict[str, float] = Field(default_factory=dict)
    deadline_ms: int = 60_000
    output_schema: str = "SubAgentResult"
    shared_evidence_scope: str = "run"


class SubAgentResult(Contract):
    task_id: str
    agent_role: str
    status: Literal["ok", "degraded", "failed"] = "ok"
    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    cost: dict[str, float] = Field(default_factory=dict)
    trace_ref: str | None = None
    stopped_reason: str | None = None


class ProgressSnapshot(Contract):
    stage: str
    call_index: int
    new_evidence_ids: list[str] = Field(default_factory=list)
    new_locations: list[str] = Field(default_factory=list)
    new_evidence_classes: list[str] = Field(default_factory=list)
    candidate_delta: dict[str, int] = Field(default_factory=lambda: {"added": 0, "removed": 0})
    hypothesis_jaccard: float = 0.0
    duplicate_calls: int = 0
    repeated_results: int = 0
    verdict: Literal["progress", "stall", "thrash", "loop"] = "progress"
    action: Literal["taken", "none"] = "none"
    reason: str = ""


class IntentDecision(Contract):
    primary: Literal["usage", "concept", "defect", "localize"] = "usage"
    secondary: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    channel: Literal["rule", "model", "fallback"] = "rule"
    matched_rule_ids: list[str] = Field(default_factory=list)
    evidence_gate: dict[str, Any] = Field(default_factory=lambda: {"passed": False, "missing": []})
    clarification: str | None = None
    note: str | None = None


# --------------------------------------------------------------------------
# 身份与权限
# --------------------------------------------------------------------------


class User(Contract):
    user_id: str
    username: str
    display_name: str | None = None
    roles: list[Literal["admin", "maintainer", "developer", "viewer"]] = Field(
        default_factory=list
    )
    status: Literal["active", "disabled", "pending"] = "pending"
    password_hash: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    last_login_at: datetime | None = None


class AuthSession(Contract):
    session_id: str
    user_id: str
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    client: dict[str, str] = Field(default_factory=dict)


class AuthzDecision(Contract):
    actor: dict[str, Any]
    action: str
    resource: dict[str, Any] = Field(default_factory=dict)
    decision: Literal["allow", "deny"] = "deny"
    reason: str = ""
    stage: str | None = None
    tool: str | None = None
    decided_at: datetime = Field(default_factory=utcnow)


__all__ = [
    "AuthSession",
    "AuthzDecision",
    "Case",
    "CaseCandidate",
    "CodeLocation",
    "Contract",
    "CrashEvidence",
    "DocRecord",
    "EvidenceItem",
    "EvidenceRef",
    "EvidenceRequest",
    "Hypothesis",
    "IntentDecision",
    "LocalizationReport",
    "LogEvidence",
    "OutcomeSignal",
    "PatchArtifact",
    "ProblemContext",
    "Product",
    "ProgressSnapshot",
    "Provenance",
    "RedactionRecord",
    "ReproEvidence",
    "RetrievalHit",
    "RunEvent",
    "SessionState",
    "StackEvidence",
    "SubAgentResult",
    "SuspectCommit",
    "Symptom",
    "TaskBrief",
    "TicketRef",
    "Triage",
    "Uncertainty",
    "User",
    "Verification",
    "VisualEvidence",
    "StackFrame",
    "CallChainLink",
    "PatchRisk",
    "PatchChecks",
    "PatchCheckStatus",
    "UserPrefs",
    "ProductSlots",
    "ToolProposal",
    "EvidenceRef",
]
