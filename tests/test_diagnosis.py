from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pmagent.agents import (
    CandidateFinder,
    DiagnosisAgent,
    render_markdown,
    stage_label,
    triage,
    verification_steps,
)
from pmagent.contracts import CodeLocation, Hypothesis, ProblemContext
from pmagent.errors import LocalizationFailed
from pmagent.knowledge.index import CodeIndex, IndexBuilder

A_CC = """
namespace pm {
int Alpha(int n) { return n + 1; }
}
"""

B_CC = """
namespace pm {
int StyleTile(int n) { return n * 2; }
}
"""

STACK = "#0 0x0000000000a1b2c3 pm::Alpha at a.cc:3\n"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "a.cc").write_text(A_CC, encoding="utf-8")
    (repo / "b.cc").write_text(B_CC, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True, check=True)
    return repo


@pytest.fixture()
def indexed(tmp_path: Path):  # noqa: ANN201
    repo = _repo(tmp_path)
    index_root = tmp_path / "index"
    manifest = IndexBuilder(index_root).build(repo)
    index = CodeIndex.open(index_root)
    try:
        yield index, repo, manifest
    finally:
        index.close()


def _context(tmp_path: Path, query: str, *, with_stack: bool = True) -> ProblemContext:
    from pmagent.evidence import EvidenceNormalizer

    stack = None
    if with_stack:
        stack_path = tmp_path / "stack.txt"
        stack_path.write_text(STACK, encoding="utf-8")
        stack = str(stack_path)
    return EvidenceNormalizer().build(
        query, stacks=[stack] if stack else None
    ).context


def test_stage_mapping() -> None:
    assert triage("放大后路网断裂").stage == "geometry"
    assert triage("瓦片一直加载不出来").stage == "tile_loading"
    assert triage("内存持续增长").stage == "memory"
    assert triage("完全无关的描述").stage == "unknown"
    assert "geometry" in str(triage("三角化接缝"))


def test_stage_rationale_and_label() -> None:
    result = triage("标注重叠")
    assert "标注" in result.rationale
    assert stage_label(result.stage) == "标注布局与碰撞"


def test_candidate_sources_and_dedupe(tmp_path: Path, indexed) -> None:  # noqa: ANN001
    index, repo, _manifest = indexed
    context = _context(tmp_path, "pm::Alpha")
    finder = CandidateFinder(index, repo_root=repo)
    candidates = finder.find(context)
    assert candidates
    by_symbol = {candidate.symbol: candidate for candidate in candidates}
    alpha = next((c for k, c in by_symbol.items() if "Alpha" in k), None)
    assert alpha is not None
    assert "stack" in alpha.sources
    assert alpha.commits


def test_candidate_retrieval_source(tmp_path: Path, indexed) -> None:  # noqa: ANN001
    index, _repo_path, _manifest = indexed
    context = _context(tmp_path, "StyleTile", with_stack=False)
    candidates = CandidateFinder(index).find(context)
    assert any("retrieval" in candidate.sources for candidate in candidates)


def test_diagnosis_report(tmp_path: Path, indexed) -> None:  # noqa: ANN001
    index, repo, _manifest = indexed
    context = _context(tmp_path, "pm::Alpha")
    report = DiagnosisAgent(index, repo_root=repo).run(context)
    assert report.hypotheses
    assert report.top_root_cause_ref == report.hypotheses[0].id
    assert report.provenance.index_version
    assert report.triage.stage
    for hypothesis in report.hypotheses:
        assert hypothesis.code_locations
        assert hypothesis.confidence <= 0.9
        assert hypothesis.verification_steps


def test_contradicting_evidence_lowers_confidence(tmp_path: Path, indexed) -> None:  # noqa: ANN001
    index, repo, _manifest = indexed
    context = _context(tmp_path, "StyleTile")
    report = DiagnosisAgent(index, repo_root=repo).run(context)
    disputed = [h for h in report.hypotheses if h.contradicting]
    assert disputed
    assert all(h.confidence < 0.75 for h in disputed)
    assert "存在反证" in disputed[0].confidence_basis


def test_high_confidence_requires_two_evidence_classes(indexed) -> None:  # noqa: ANN001
    index, _repo_path, _manifest = indexed
    agent = DiagnosisAgent(index)
    hypothesis = Hypothesis(
        id="H1",
        statement="only code evidence",
        code_locations=[CodeLocation(file="a.cc", line_start=2, line_end=2, symbol="pm::Alpha")],
    )
    scored = agent._score(hypothesis, [])
    assert scored.confidence < 0.75
    assert len(scored.evidence_classes) == 1
    assert "1 类" in scored.confidence_basis


def test_self_check_rejects_bad_location(tmp_path: Path, indexed) -> None:  # noqa: ANN001
    index, repo, _manifest = indexed
    context = _context(tmp_path, "pm::Alpha")
    agent = DiagnosisAgent(index, repo_root=repo)
    report = agent.run(context)
    report.hypotheses[0].code_locations[0].file = "does_not_exist.cc"
    with pytest.raises(LocalizationFailed):
        agent._self_check(report)


def test_verification_steps_per_stage() -> None:
    assert verification_steps("geometry")
    assert verification_steps("unknown")


def test_render_markdown_contains_sections(tmp_path: Path, indexed) -> None:  # noqa: ANN001
    index, repo, _manifest = indexed
    context = _context(tmp_path, "pm::Alpha")
    report = DiagnosisAgent(index, repo_root=repo).run(context)
    markdown = render_markdown(report, context)
    assert "# 定位报告" in markdown
    assert "置信度" in markdown
    assert "验证建议" in markdown
