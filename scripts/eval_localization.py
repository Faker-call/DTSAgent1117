"""故障定位评测：Top-1/Top-3 文件命中与虚假高置信率。

用法：python scripts/eval_localization.py [index_root] [tickets]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pmagent.agents import DiagnosisAgent
from pmagent.evidence import EvidenceNormalizer
from pmagent.knowledge.corpus import load_tickets
from pmagent.knowledge.index import CodeIndex
from pmagent.knowledge.retrieval import HybridRetriever


def main() -> int:
    index_root = Path(sys.argv[1] if len(sys.argv) > 1 else "corpus/.index")
    tickets_path = Path(
        sys.argv[2] if len(sys.argv) > 2 else "data/tickets/synthetic_tickets.json"
    )
    tickets, _skipped = load_tickets(tickets_path)
    index = CodeIndex.open(index_root)
    agent = DiagnosisAgent(index, retriever=HybridRetriever(code_index=index))

    top1 = top3 = 0
    false_high = 0
    evaluated = 0
    durations: list[int] = []
    try:
        for ticket in tickets:
            context = EvidenceNormalizer().build(
                ticket.symptom,
                product=None,
            ).context
            try:
                report = agent.run(context)
            except Exception:  # noqa: BLE001 - 无候选视为未命中
                evaluated += 1
                continue
            evaluated += 1
            durations.append(report.provenance.duration_ms)
            files = [
                location.file
                for hypothesis in report.hypotheses
                for location in hypothesis.code_locations
            ]
            if files and files[0] == ticket.root_cause_file:
                top1 += 1
            if ticket.root_cause_file in files[:3]:
                top3 += 1
            if report.hypotheses[0].confidence >= 0.75 and (
                not files or files[0] != ticket.root_cause_file
            ):
                false_high += 1
    finally:
        index.close()

    report = {
        "tickets": len(tickets),
        "evaluated": evaluated,
        "top1_file_hit": round(top1 / evaluated, 3) if evaluated else None,
        "top3_file_hit": round(top3 / evaluated, 3) if evaluated else None,
        "false_high_confidence_rate": round(false_high / evaluated, 3) if evaluated else None,
        "median_duration_ms": sorted(durations)[len(durations) // 2] if durations else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
