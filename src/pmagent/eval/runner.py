"""离线评测执行器：检索与定位两类模式的指标汇总。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pmagent.agents import AnswerComposer, DiagnosisAgent
from pmagent.eval.dataset import EvalCase
from pmagent.eval.metrics import (
    false_high_confidence,
    percentile,
    recall_at_k,
    topk_hit,
)
from pmagent.evidence import EvidenceNormalizer
from pmagent.knowledge.index import CodeIndex
from pmagent.knowledge.retrieval import HybridRetriever


@dataclass
class EvalRunner:
    code_index: CodeIndex | None = None
    retriever: HybridRetriever | None = None
    agent: DiagnosisAgent | None = None

    def run(
        self, cases: list[EvalCase], *, mode: str = "retrieval", limit: int | None = None
    ) -> dict:
        selected = cases[:limit] if limit else cases
        rows: list[dict] = []
        for case in selected:
            rows.append(self._case(case, mode))
        return self._summarise(selected, rows, mode)

    # -- 单条 -------------------------------------------------------------
    def _case(self, case: EvalCase, mode: str) -> dict:
        started = time.perf_counter()
        files: list[str] = []
        confidence = 0.0
        invalid = False
        skipped = False
        answer = ""
        try:
            if case.expected_answer_contains:
                hits = (
                    self.retriever.search(case.query, intent=case.category, limit=10)
                    if self.retriever
                    else []
                )
                answer = AnswerComposer().compose(case.query, hits).conclusion
                if not hits:
                    skipped = True
            elif mode == "diagnosis":
                if self.agent is None:
                    raise RuntimeError("未配置诊断 Agent")
                context = EvidenceNormalizer().build(case.query).context
                report = self.agent.run(context)
                files = [
                    location.file
                    for hypothesis in report.hypotheses
                    for location in hypothesis.code_locations
                ]
                confidence = report.hypotheses[0].confidence if report.hypotheses else 0.0
            else:
                if self.retriever is None:
                    raise RuntimeError("未配置检索器")
                hits = self.retriever.search(case.query, intent="localize", limit=10)
                files = [hit.path for hit in hits if hit.kind == "code" and hit.path]
                confidence = hits[0].score if hits else 0.0
                invalid = not hits
        except Exception:  # noqa: BLE001 - 评测中单条失败不中断
            invalid = True
        duration = (time.perf_counter() - started) * 1000

        hit = topk_hit(files, case.expected_files, 3) if case.expected_files else (
            bool(case.expected_answer_contains)
            and case.expected_answer_contains in answer
        )
        return {
            "id": case.id,
            "category": case.category,
            "query": case.query,
            "expected_files": case.expected_files,
            "hit": hit,
            "files": files,
            "confidence": confidence,
            "invalid": invalid,
            "skipped": skipped,
            "duration_ms": round(duration, 1),
        }

    # -- 汇总 -------------------------------------------------------------
    @staticmethod
    def _summarise(cases: list[EvalCase], rows: list[dict], mode: str) -> dict:
        pairs = list(zip(rows, cases, strict=True))
        evaluated = [(row, case) for row, case in pairs if case.expected_files]
        doc_rows = [(row, case) for row, case in pairs if case.expected_answer_contains]
        durations = [row["duration_ms"] for row in rows]
        recall = [recall_at_k(row["files"], case.expected_files, 10) for row, case in evaluated]
        top1 = [topk_hit(row["files"], case.expected_files, 1) for row, case in evaluated]
        top3 = [topk_hit(row["files"], case.expected_files, 3) for row, case in evaluated]
        high = [(row["confidence"], row["hit"]) for row, _case in evaluated]
        accuracy = (
            sum(1 for row, _case in doc_rows if row["hit"]) / len(doc_rows)
            if doc_rows
            else 0.0
        )
        return {
            "mode": mode,
            "sample_size": len(cases),
            "evaluated_code": len(evaluated),
            "evaluated_docs": len(doc_rows),
            "invalid": sum(1 for row in rows if row["invalid"]),
            "skipped": sum(1 for row in rows if row["skipped"]),
            "metrics": {
                "recall_at_10": round(sum(recall) / len(recall), 3) if recall else None,
                "top1": round(sum(top1) / len(top1), 3) if top1 else None,
                "top3": round(sum(top3) / len(top3), 3) if top3 else None,
                "conclusion_accuracy": round(accuracy, 3) if doc_rows else None,
                "false_high_confidence": round(false_high_confidence(high), 3)
                if high
                else 0.0,
                "invalid_call_rate": round(
                    sum(1 for row in rows if row["invalid"]) / len(rows), 3
                )
                if rows
                else 0.0,
                "stall_detection_rate": 0.0,
                "avg_effective_calls": 1.0,
                "p95_latency_ms": percentile(durations, 95),
                "cost_cny": 0.0,
            },
            "notes": [
                "stall_detection_rate 未接编排循环，当前恒为 0",
                "cost_cny 未调用付费模型，当前为 0",
            ],
            "cases": rows,
        }


def compare_reports(baseline: dict, current: dict) -> dict:
    base_metrics = baseline.get("metrics", {})
    cur_metrics = current.get("metrics", {})
    deltas = {
        key: {
            "baseline": base_metrics.get(key),
            "current": cur_metrics.get(key),
            "delta": (
                None
                if base_metrics.get(key) is None or cur_metrics.get(key) is None
                else round(cur_metrics[key] - base_metrics[key], 3)
            ),
        }
        for key in set(base_metrics) | set(cur_metrics)
    }
    base_cases = {row["id"]: row for row in baseline.get("cases", [])}
    degraded = [
        row["id"]
        for row in current.get("cases", [])
        if row["id"] in base_cases and base_cases[row["id"]].get("hit") and not row.get("hit")
    ]
    return {"deltas": deltas, "degraded_cases": degraded}


__all__ = ["EvalRunner", "compare_reports"]
