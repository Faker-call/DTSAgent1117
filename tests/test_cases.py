from __future__ import annotations

from pathlib import Path

import pytest

from pmagent.contracts import Verification
from pmagent.errors import PmagentError
from pmagent.memory import CaseFeedback, MemoryStore


@pytest.fixture()
def feedback(tmp_path: Path):  # noqa: ANN201
    store = MemoryStore(tmp_path / "memory.db")
    yield CaseFeedback(store), store
    store.close()


def test_signal_types_and_confidence() -> None:
    signal = CaseFeedback.signal("patch_merged", {"run_id": "r1"})
    assert signal.kind == "patch_merged"
    assert signal.confidence == 0.9
    with pytest.raises(PmagentError):
        CaseFeedback.signal("unknown", {})


def test_dedupe_key_stable() -> None:
    first = CaseFeedback.dedupe_key("缝隙 出现", "src/a.cc", "pm::A")
    second = CaseFeedback.dedupe_key(" 缝隙   出现 ", "src/a.cc", "pm::A")
    assert first == second
    assert first != CaseFeedback.dedupe_key("缝隙", "src/b.cc", "pm::A")


def test_approve_creates_case(feedback) -> None:  # noqa: ANN001
    helper, store = feedback
    candidate = helper.build_candidate(
        symptom="标注错位",
        root_cause="未考虑旋转",
        evidence_refs=["E1", "E2"],
        fix_commit="abc123",
        verification=Verification(steps=["复现"], acceptance=["不再错位"]),
        signals=[helper.signal("review_confirmed", {"run_id": "r1"})],
        root_cause_file="src/layout.cc",
        root_cause_symbol="pm::Layout::prepare",
    )
    case = helper.approve(candidate, "alice", root_cause_file="src/layout.cc")
    assert case.reviewed_by == "alice"
    assert case.expires_at is not None
    assert store.list_cases()[0].case_id == case.case_id


def test_duplicate_candidate_returns_existing(feedback) -> None:  # noqa: ANN001
    helper, _store = feedback
    payload = {
        "symptom": "标注错位",
        "root_cause": "未考虑旋转",
        "evidence_refs": ["E1"],
    }
    first = helper.build_candidate(**payload)
    case_one = helper.approve(first, "alice")
    second = helper.build_candidate(**payload)
    case_two = helper.approve(second, "bob")
    assert case_one.case_id == case_two.case_id


def test_holdout_gate_blocks_bad_candidate(feedback) -> None:  # noqa: ANN001
    helper, store = feedback
    candidate = helper.build_candidate(
        symptom="黑屏", root_cause="资源未释放", evidence_refs=["E1"]
    )
    with pytest.raises(PmagentError):
        helper.approve(
            candidate,
            "alice",
            holdout=[{"symptom": "黑屏"}],
            validator=lambda cand, holdout: False,
        )
    assert store.list_cases() == []


def test_holdout_gate_allows_valid_candidate(feedback) -> None:  # noqa: ANN001
    helper, store = feedback
    candidate = helper.build_candidate(
        symptom="黑屏", root_cause="资源未释放", evidence_refs=["E1"]
    )
    case = helper.approve(
        candidate, "alice", holdout=[{"symptom": "黑屏"}], validator=lambda c, h: True
    )
    assert store.list_cases()[0].case_id == case.case_id


def test_retrieval_prior_and_recurrence_decay(feedback) -> None:  # noqa: ANN001
    helper, _store = feedback
    candidate = helper.build_candidate(
        symptom="tile seam",
        root_cause="unclosed boundary",
        evidence_refs=["E1"],
        root_cause_file="src/tess.cc",
    )
    case = helper.approve(candidate, "alice", root_cause_file="src/tess.cc")

    prior = helper.retrieval_prior("tile seam issue")
    assert prior["src/tess.cc"] > 0

    for _ in range(3):
        helper.recurrence(case.case_id)
    decayed = helper.retrieval_prior("tile seam issue")
    assert decayed["src/tess.cc"] < prior["src/tess.cc"]


def test_expire_removes_from_active(feedback) -> None:  # noqa: ANN001
    helper, store = feedback
    candidate = helper.build_candidate(symptom="s", root_cause="r", evidence_refs=[])
    case = helper.approve(candidate, "alice")
    helper.expire(case.case_id)
    assert store.list_cases() == []


def test_examples_sorted_by_confidence(feedback) -> None:  # noqa: ANN001
    helper, _store = feedback
    low = helper.build_candidate(symptom="a", root_cause="x", evidence_refs=[])
    high = helper.build_candidate(
        symptom="b",
        root_cause="y",
        evidence_refs=[],
        signals=[
            helper.signal("patch_merged", {"run_id": "r"}),
            helper.signal("ticket_closed", {"ticket_id": "t"}),
        ],
    )
    helper.approve(low, "alice")
    helper.approve(high, "alice")
    examples = helper.examples()
    assert examples[0].symptom == "b"


def test_reject_patch_records_negative(feedback) -> None:  # noqa: ANN001
    helper, _store = feedback
    helper.reject_patch("patch-1", "治标不治本")
    negatives = helper.negative_patches()
    assert negatives[0]["patch_id"] == "patch-1"
    assert negatives[0]["reason"] == "治标不治本"


def test_eval_seed_conversion(feedback) -> None:  # noqa: ANN001
    helper, _store = feedback
    candidate = helper.build_candidate(
        symptom="缝隙", root_cause="未闭合", evidence_refs=[], root_cause_file="src/a.cc"
    )
    case = helper.approve(candidate, "alice", root_cause_file="src/a.cc")
    seed = helper.eval_seed(case)
    assert seed["source"] == "case_feedback"
    assert seed["gold"]["file"] == "src/a.cc"


def test_prior_is_prior_not_evidence(feedback) -> None:  # noqa: ANN001
    helper, _store = feedback
    candidate = helper.build_candidate(
        symptom="tile seam", root_cause="unclosed", evidence_refs=["E1"], root_cause_file="src/t.cc"
    )
    helper.approve(candidate, "alice", root_cause_file="src/t.cc")
    prior = helper.retrieval_prior("tile seam")
    # 先验只包含位置键，不包含任何证据类别
    assert set(prior) == {"src/t.cc"}
    assert all(isinstance(weight, float) for weight in prior.values())
