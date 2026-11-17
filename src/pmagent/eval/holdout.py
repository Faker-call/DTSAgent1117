"""回流案例的留出集验证门禁。"""

from __future__ import annotations

from collections.abc import Callable

from pmagent.contracts import CaseCandidate


def holdout_gate(
    candidate: CaseCandidate,
    holdout: list[dict],
    predictor: Callable[[str], str | None],
) -> bool:
    """留出集上必须全部命中，才允许进入复用池。"""
    if not holdout:
        return False
    for item in holdout:
        symptom = item.get("symptom", "")
        expected = item.get("root_cause_file")
        if not symptom or not expected:
            return False
        if predictor(symptom) != expected:
            return False
    return True


__all__ = ["holdout_gate"]
