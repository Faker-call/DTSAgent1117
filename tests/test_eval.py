from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pmagent.contracts import RetrievalHit
from pmagent.eval import (
    EvalCase,
    EvalRunner,
    calibration_buckets,
    category_distribution,
    cohen_kappa,
    compare_reports,
    conclusion_accuracy,
    false_high_confidence,
    from_tickets,
    holdout_gate,
    load_cases,
    missing_summary,
    percentile,
    recall_at_k,
    save_cases,
    topk_hit,
)
from pmagent.memory import CaseFeedback, MemoryStore
from pmagent.store import ArtifactStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tickets_import_and_missing_annotations() -> None:
    tickets = from_tickets(REPO_ROOT / "data/tickets/synthetic_tickets.json")
    assert len(tickets) >= 60
    summary = missing_summary(tickets)
    assert summary.get("expected_answer_contains", 0) > 0


def test_seed_set_is_stratified() -> None:
    cases = load_cases(REPO_ROOT / "data/eval/seed.jsonl")
    assert len(cases) >= 200
    distribution = category_distribution(cases)
    assert set(distribution) >= {"usage", "concept", "defect", "localize", "security"}


def test_missing_summary_counts() -> None:
    cases = [
        EvalCase(id="a", category="usage", query="q"),
        EvalCase(id="b", category="defect", query="q"),
    ]
    summary = missing_summary(cases)
    assert summary["expected_answer_contains"] == 1
    assert summary["expected_files"] == 1


def test_metrics_basic() -> None:
    assert topk_hit(["a", "b"], ["b"], 3)
    assert not topk_hit(["a", "b"], ["c"], 3)
    assert recall_at_k(["a", "b"], ["a", "c"], 10) == 0.5
    assert conclusion_accuracy([("样式", "设置样式")]) == 1.0
    assert false_high_confidence([(0.8, False), (0.5, False)]) == 0.5
    assert percentile([1, 2, 3, 4], 95) == 4


def test_calibration_and_kappa() -> None:
    rows, ece = calibration_buckets([(0.9, True), (0.9, True), (0.2, False)], buckets=5)
    assert len(rows) == 5
    assert 0 <= ece <= 1
    a = {"1": "x", "2": "y", "3": "x"}
    assert cohen_kappa(a, a) == 1.0
    assert cohen_kappa(a, {"1": "x", "2": "x", "3": "x"}) < 1.0


def test_runner_retrieval_with_fake_retriever() -> None:
    class FakeRetriever:
        def search(self, query, **kwargs):  # noqa: ANN001
            return [
                RetrievalHit(
                    chunk_id="1",
                    kind="code",
                    path="src/a.cc",
                    line_start=1,
                    symbol="pm::A",
                    score=0.9,
                    snippet="body",
                )
            ]

    runner = EvalRunner(retriever=FakeRetriever())  # type: ignore[arg-type]
    cases = [
        EvalCase(id="c1", category="defect", query="q", expected_files=["src/a.cc"]),
        EvalCase(id="c2", category="usage", query="q", expected_answer_contains="结论"),
    ]
    report = runner.run(cases, mode="retrieval")
    assert report["sample_size"] == 2
    assert report["metrics"]["top1"] == 1.0
    assert report["metrics"]["recall_at_10"] == 1.0


def test_compare_reports_marks_degraded() -> None:
    baseline = {"metrics": {"top1": 0.5}, "cases": [{"id": "c1", "hit": True}]}
    current = {"metrics": {"top1": 0.25}, "cases": [{"id": "c1", "hit": False}]}
    diff = compare_reports(baseline, current)
    assert diff["degraded_cases"] == ["c1"]
    assert diff["deltas"]["top1"]["delta"] == -0.25


def test_holdout_gate() -> None:
    class Candidate:
        symptom = "crash"
        root_cause = "x"

    holdout = [{"symptom": "crash", "root_cause_file": "a.cc"}]
    assert holdout_gate(Candidate(), holdout, lambda _s: "a.cc")  # type: ignore[arg-type]
    assert not holdout_gate(Candidate(), holdout, lambda _s: "b.cc")  # type: ignore[arg-type]
    assert not holdout_gate(Candidate(), [], lambda _s: "a.cc")  # type: ignore[arg-type]


def test_review_appends_and_admits_case(tmp_path: Path) -> None:
    from pmagent.eval import admit_case, append_review

    store = ArtifactStore(
        state_root=tmp_path / ".pmagent", artifacts_root=tmp_path / "artifacts"
    )
    path = append_review({"case_id": "c1", "score": 5, "accepted": True}, store)
    assert path.exists()

    memory = MemoryStore(tmp_path / "memory.db")
    try:
        feedback = CaseFeedback(memory)
        case = admit_case(
            {
                "symptom": "瓦片缝隙",
                "root_cause": "三角化未闭合",
                "root_cause_file": "src/a.cc",
                "evidence_refs": ["E1"],
            },
            feedback,
            reviewer="tester",
        )
        assert case.case_id
    finally:
        memory.close()


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    import os

    env = dict(os.environ)
    env["PMAGENT_DB"] = str(tmp_path / "state.db")
    env["PMAGENT_SESSION"] = str(tmp_path / "session.json")
    env["PMAGENT_MEMORY_DB"] = str(tmp_path / "memory.db")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PMAGENT_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-m", "pmagent", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _login(tmp_path: Path) -> None:
    assert (
        _run(
            tmp_path, "auth", "init", "--username", "admin", "--password", "secret123"
        ).returncode
        == 0
    )
    assert (
        _run(
            tmp_path, "auth", "login", "--username", "admin", "--password", "secret123"
        ).returncode
        == 0
    )


def test_cli_eval_run_compare_calibrate(tmp_path: Path) -> None:
    _login(tmp_path)
    dataset = tmp_path / "cases.jsonl"
    save_cases(
        [
            EvalCase(
                id="c1", category="defect", query="瓦片缝隙", expected_files=["src/a.cc"]
            ),
            EvalCase(id="c2", category="defect", query="黑屏", expected_files=["src/b.cc"]),
        ],
        dataset,
    )
    out = tmp_path / "report.json"
    result = _run(tmp_path, "eval", "run", "--dataset", str(dataset), "--out", str(out))
    assert result.returncode == 0, result.stderr
    assert out.exists()

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"metrics": {"top1": 0.0}, "cases": []}), encoding="utf-8"
    )
    compare = _run(
        tmp_path, "eval", "compare", "--baseline", str(baseline), "--current", str(out)
    )
    assert compare.returncode == 0, compare.stderr

    calibrate = _run(tmp_path, "eval", "calibrate", "--report", str(out))
    assert calibrate.returncode == 0, calibrate.stderr
    assert "ECE" in calibrate.stdout


def test_cli_eval_agreement_and_review(tmp_path: Path) -> None:
    _login(tmp_path)
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text('{"id":"1","label":"x"}\n{"id":"2","label":"y"}\n', encoding="utf-8")
    b.write_text('{"id":"1","label":"x"}\n{"id":"2","label":"x"}\n', encoding="utf-8")
    result = _run(tmp_path, "eval", "agreement", "--a", str(a), "--b", str(b))
    assert result.returncode == 0
    assert "kappa" in result.stdout

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {"cases": [{"id": "c1", "query": "瓦片缝隙", "expected_files": ["src/a.cc"]}]}
        ),
        encoding="utf-8",
    )
    review = _run(
        tmp_path,
        "eval",
        "review",
        "--report",
        str(report),
        "--case-id",
        "c1",
        "--score",
        "4",
        "--accept",
        "--root-cause",
        "未闭合",
    )
    assert review.returncode == 0, review.stderr
    assert "已进入案例库" in review.stdout
