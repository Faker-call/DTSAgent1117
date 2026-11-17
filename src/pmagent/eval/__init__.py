"""评测层：数据集、指标、执行器、人工评审与留出门禁（M15）。"""

from pmagent.eval.dataset import (
    CATEGORIES,
    EvalCase,
    category_distribution,
    from_docs,
    from_tickets,
    load_cases,
    missing_summary,
    save_cases,
    stratify,
)
from pmagent.eval.holdout import holdout_gate
from pmagent.eval.metrics import (
    calibration_buckets,
    cohen_kappa,
    conclusion_accuracy,
    false_high_confidence,
    percentile,
    recall_at_k,
    topk_hit,
)
from pmagent.eval.review import admit_case, append_review, review_path
from pmagent.eval.runner import EvalRunner, compare_reports

__all__ = [
    "CATEGORIES",
    "EvalCase",
    "EvalRunner",
    "admit_case",
    "append_review",
    "calibration_buckets",
    "category_distribution",
    "cohen_kappa",
    "compare_reports",
    "conclusion_accuracy",
    "false_high_confidence",
    "from_docs",
    "from_tickets",
    "holdout_gate",
    "load_cases",
    "missing_summary",
    "percentile",
    "recall_at_k",
    "review_path",
    "save_cases",
    "stratify",
    "topk_hit",
]
