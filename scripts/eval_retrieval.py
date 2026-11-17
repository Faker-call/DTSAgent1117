"""检索评测：符号召回与自然语言召回基线。

用法：python scripts/eval_retrieval.py [index_root] [tickets]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

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
    retriever = HybridRetriever(code_index=index)

    symbol_total = symbol_hit = 0
    nl_total = nl_hit = 0
    by_category: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    channel_counts: dict[str, int] = defaultdict(int)

    try:
        for ticket in tickets:
            if ticket.root_cause_symbol:
                symbol_total += 1
                leaf = ticket.root_cause_symbol.split("::")[-1]
                hits = retriever.search(ticket.root_cause_symbol, intent="localize", limit=10)
                found = any(leaf in (hit.symbol or "") for hit in hits)
                symbol_hit += int(found)

            nl_total += 1
            hits = retriever.search(ticket.symptom, intent="defect", limit=10)
            for hit in hits:
                for channel in hit.channels:
                    channel_counts[channel] += 1
            found_nl = any((hit.path or "") == ticket.root_cause_file for hit in hits)
            nl_hit += int(found_nl)
            by_category[ticket.category][0] += int(found_nl)
            by_category[ticket.category][1] += 1
    finally:
        index.close()

    report = {
        "tickets": len(tickets),
        "symbol_recall_at_10": round(symbol_hit / symbol_total, 3) if symbol_total else None,
        "symbol_samples": symbol_total,
        "natural_language_recall_at_10": round(nl_hit / nl_total, 3) if nl_total else None,
        "nl_samples": nl_total,
        "by_category": {
            category: round(hits / total, 3) if total else None
            for category, (hits, total) in sorted(by_category.items())
        },
        "channel_distribution": dict(sorted(channel_counts.items())),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
