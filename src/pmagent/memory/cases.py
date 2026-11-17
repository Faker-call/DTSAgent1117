"""成功案例回流：信号 → 候选 → 人工确认 → 复用 / 失效 / 反例。"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta

from pmagent.contracts import Case, CaseCandidate, OutcomeSignal, Verification
from pmagent.errors import PmagentError
from pmagent.memory.store import MemoryStore

CASE_TTL_DAYS = 180
PRIOR_WEIGHT = 0.2
SIGNAL_CONFIDENCE = {
    "patch_merged": 0.9,
    "ticket_closed": 0.9,
    "repro_gone": 0.6,
    "review_confirmed": 0.9,
}
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_:]{2,}")


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _tokens(text: str) -> set[str]:
    return {match.lower() for match in TOKEN.findall(text)}


class CaseFeedback:
    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    # -- 信号与候选 -------------------------------------------------------
    @staticmethod
    def signal(kind: str, ref: dict, confidence: float | None = None) -> OutcomeSignal:
        if kind not in SIGNAL_CONFIDENCE:
            raise PmagentError(f"未知成功信号类型：{kind}")
        return OutcomeSignal(
            kind=kind,  # type: ignore[arg-type]
            ref=ref,
            confidence=confidence if confidence is not None else SIGNAL_CONFIDENCE[kind],
        )

    @staticmethod
    def dedupe_key(symptom: str, root_cause_file: str | None, root_cause_symbol: str | None) -> str:
        raw = f"{_normalize(symptom)}|{root_cause_file or ''}|{root_cause_symbol or ''}"
        return hashlib.sha1(raw.encode()).hexdigest()

    def build_candidate(
        self,
        *,
        symptom: str,
        root_cause: str,
        evidence_refs: list[str],
        fix_commit: str | None = None,
        verification: Verification | None = None,
        signals: list[OutcomeSignal] | None = None,
        root_cause_file: str | None = None,
        root_cause_symbol: str | None = None,
    ) -> CaseCandidate:
        return CaseCandidate(
            case_id=uuid.uuid4().hex[:12],
            symptom=symptom,
            evidence_refs=evidence_refs,
            root_cause=root_cause,
            fix_commit=fix_commit,
            verification=verification or Verification(),
            dedupe_key=self.dedupe_key(symptom, root_cause_file, root_cause_symbol),
            source_signals=[signal.kind for signal in (signals or [])],
        )

    # -- 确认入库 ---------------------------------------------------------
    def approve(
        self,
        candidate: CaseCandidate,
        reviewer: str,
        *,
        root_cause_file: str | None = None,
        root_cause_symbol: str | None = None,
        holdout: list[dict] | None = None,
        validator: object | None = None,
    ) -> Case:
        if holdout is not None:
            if validator is None:
                raise PmagentError("提供了留出集但未提供验证器")
            passed = validator(candidate, holdout)  # type: ignore[operator]
            if not passed:
                raise PmagentError(
                    "候选案例未通过留出集验证，不允许进入复用池",
                    hint="补充证据或修正根因后重新提交",
                )

        existing = self.store.find_case_by_dedupe(candidate.dedupe_key)
        if existing is not None:
            return existing

        now = datetime.now(UTC)
        case = Case(
            case_id=candidate.case_id,
            symptom=candidate.symptom,
            root_cause=candidate.root_cause,
            fix_commit=candidate.fix_commit,
            sdk_version=None,
            engine_commit_range=None,
            confidence=min(1.0, 0.5 + 0.15 * len(candidate.source_signals)),
            reviewed_by=reviewer,
            created_at=now,
            expires_at=now + timedelta(days=CASE_TTL_DAYS),
            root_cause_file=root_cause_file,
            root_cause_symbol=root_cause_symbol,
        )
        return self.store.add_case(case, dedupe_key=candidate.dedupe_key)

    # -- 复用 -------------------------------------------------------------
    def retrieval_prior(self, query: str, limit: int = 3) -> dict[str, float]:
        """返回根因位置 → 先验权重；只影响排序，不作为证据。"""
        query_tokens = _tokens(query)
        if not query_tokens:
            return {}
        scored: list[tuple[float, Case]] = []
        for case in self.store.list_cases():
            overlap = len(query_tokens & _tokens(case.symptom))
            if not overlap:
                continue
            recurrence = self.store.recurrence_count(case.case_id)
            weight = PRIOR_WEIGHT * overlap / len(query_tokens) / (1 + recurrence)
            scored.append((weight, case))
        scored.sort(key=lambda item: item[0], reverse=True)
        prior: dict[str, float] = {}
        for weight, case in scored[:limit]:
            if case.root_cause_file:
                prior[case.root_cause_file] = max(prior.get(case.root_cause_file, 0.0), weight)
            if case.root_cause_symbol:
                prior[case.root_cause_symbol] = max(prior.get(case.root_cause_symbol, 0.0), weight)
            self.store.bump_reuse(case.case_id)
        return prior

    def examples(self, limit: int = 3) -> list[Case]:
        cases = sorted(self.store.list_cases(), key=lambda case: case.confidence, reverse=True)
        return cases[:limit]

    def eval_seed(self, case: Case) -> dict:
        """把案例转成评测种子（与人工种子分开统计）。"""
        return {
            "id": f"case-{case.case_id}",
            "category": "case_feedback",
            "input": {"symptom": case.symptom},
            "gold": {
                "root_cause": case.root_cause,
                "file": case.root_cause_file,
                "symbol": case.root_cause_symbol,
            },
            "source": "case_feedback",
        }

    # -- 失效与反例 -------------------------------------------------------
    def expire(self, case_id: str) -> None:
        self.store.set_case_status(case_id, "expired")

    def recurrence(self, case_id: str) -> int:
        count = self.store.bump_recurrence(case_id)
        return count

    def reject_patch(self, patch_id: str, reason: str) -> None:
        self.store.add_negative_patch(patch_id, reason)

    def negative_patches(self) -> list[dict]:
        return self.store.list_negative_patches()


__all__ = ["CASE_TTL_DAYS", "CaseFeedback"]
