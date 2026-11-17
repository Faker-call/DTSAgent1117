from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pmagent import contracts as c


def _samples() -> list[c.Contract]:
    now = datetime.now(UTC)
    return [
        c.ProblemContext(id="p1"),
        c.Hypothesis(id="h1", statement="瓦片边界未闭合"),
        c.LocalizationReport(problem_context_ref="p1", triage=c.Triage(stage="geometry")),
        c.PatchArtifact(id="a1", localization_ref="h1", diff="--- a\n+++ b\n"),
        c.RetrievalHit(chunk_id="c1", path="src/a.cc", channels=["bm25"]),
        c.DocRecord(id="d1", title="样式参考", url="https://example.com"),
        c.SessionState(id="s1"),
        c.RunEvent(run_id="r1", seq=1, type="stage_started"),
        c.EvidenceItem(evidence_id="e1", kind="code", ref={"path": "src/a.cc", "line": 10}),
        c.Case(case_id="case1", symptom="缝隙", root_cause="未闭合"),
        c.ToolProposal(tool="code.search_symbol", args={"name": "BuildStrip"}),
        c.OutcomeSignal(kind="review_confirmed", ref={"run_id": "r1"}),
        c.CaseCandidate(
            case_id="cand1", symptom="缝隙", root_cause="未闭合", dedupe_key="k1"
        ),
        c.User(user_id="u1", username="alice", roles=["admin"], status="active"),
        c.AuthSession(session_id="sess1", user_id="u1", expires_at=now + timedelta(hours=12)),
        c.AuthzDecision(actor={"user_id": "u1"}, action="index.build", decision="allow"),
        c.ProgressSnapshot(stage="triage", call_index=1),
        c.TaskBrief(
            task_id="t1",
            parent_run_id="r1",
            agent_role="diagnosis",
            problem_context_ref="p1",
        ),
        c.SubAgentResult(task_id="t1", agent_role="diagnosis"),
        c.IntentDecision(primary="defect", confidence=0.8),
    ]


@pytest.mark.parametrize("model", _samples(), ids=lambda m: type(m).__name__)
def test_json_roundtrip(model: c.Contract) -> None:
    payload = model.model_dump_json()
    restored = type(model).model_validate_json(payload)
    assert restored == model


def test_unknown_field_rejected() -> None:
    with pytest.raises(ValidationError):
        c.ProblemContext(id="p1", unexpected="x")


def test_problem_context_nested_roundtrip() -> None:
    ctx = c.ProblemContext(
        id="p2",
        product=c.Product(sdk_version="6.0.1", platform="harmony"),
        symptoms=[c.Symptom(description="标注错位", severity="high")],
        logs=[c.LogEvidence(source_path="a.log", line_count=10)],
        stacks=[c.StackEvidence(source_path="b.txt", frames=[c.StackFrame(index=0)])],
        missing=[c.EvidenceRequest(needed="日志", purpose="确认触发条件")],
    )
    assert c.ProblemContext.model_validate_json(ctx.model_dump_json()) == ctx
