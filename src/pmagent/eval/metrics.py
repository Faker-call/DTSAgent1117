"""评测指标：检索、定位、结论、校准与标注一致性。"""

from __future__ import annotations

from collections import defaultdict


def topk_hit(files: list[str], expected: list[str], k: int = 3) -> bool:
    if not expected:
        return False
    return any(path in expected for path in files[:k])


def recall_at_k(files: list[str], expected: list[str], k: int = 10) -> float:
    if not expected:
        return 0.0
    hits = sum(1 for path in set(expected) if path in files[:k])
    return hits / len(set(expected))


def conclusion_accuracy(pairs: list[tuple[str, str]]) -> float:
    if not pairs:
        return 0.0
    return sum(1 for expected, actual in pairs if expected and expected in actual) / len(pairs)


def false_high_confidence(pairs: list[tuple[float, bool]], threshold: float = 0.75) -> float:
    if not pairs:
        return 0.0
    wrong = sum(1 for confidence, correct in pairs if confidence >= threshold and not correct)
    return wrong / len(pairs)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((p / 100) * (len(ordered) - 1)))))
    return ordered[index]


def calibration_buckets(
    pairs: list[tuple[float, bool]], buckets: int = 5
) -> tuple[list[dict], float]:
    grouped: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for confidence, correct in pairs:
        index = min(buckets - 1, int(confidence * buckets))
        grouped[index].append((confidence, correct))
    rows: list[dict] = []
    ece = 0.0
    total = len(pairs)
    for index in range(buckets):
        items = grouped.get(index, [])
        if not items:
            rows.append(
                {"bucket": f"{index / buckets:.1f}-{(index + 1) / buckets:.1f}", "n": 0}
            )
            continue
        avg_conf = sum(item[0] for item in items) / len(items)
        accuracy = sum(1 for item in items if item[1]) / len(items)
        ece += (len(items) / total) * abs(avg_conf - accuracy) if total else 0
        rows.append(
            {
                "bucket": f"{index / buckets:.1f}-{(index + 1) / buckets:.1f}",
                "n": len(items),
                "confidence": round(avg_conf, 3),
                "accuracy": round(accuracy, 3),
            }
        )
    return rows, round(ece, 3)


def cohen_kappa(annotator_a: dict[str, str], annotator_b: dict[str, str]) -> float:
    keys = sorted(set(annotator_a) & set(annotator_b))
    if not keys:
        return 0.0
    labels = {annotator_a[key] for key in keys} | {annotator_b[key] for key in keys}
    observed = sum(1 for key in keys if annotator_a[key] == annotator_b[key]) / len(keys)
    expected = 0.0
    for label in labels:
        pa = sum(1 for key in keys if annotator_a[key] == label) / len(keys)
        pb = sum(1 for key in keys if annotator_b[key] == label) / len(keys)
        expected += pa * pb
    if expected >= 1.0:
        return 1.0
    return round((observed - expected) / (1 - expected), 3)


__all__ = [
    "calibration_buckets",
    "cohen_kappa",
    "conclusion_accuracy",
    "false_high_confidence",
    "percentile",
    "recall_at_k",
    "topk_hit",
]
