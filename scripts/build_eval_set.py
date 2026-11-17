"""构建分层评测集：工单 + 文档标题 + 注入对抗集。

用法：python scripts/build_eval_set.py
"""

from __future__ import annotations

import json

from pmagent.eval import (
    EvalCase,
    category_distribution,
    from_docs,
    from_tickets,
    load_cases,
    missing_summary,
    save_cases,
)


def main() -> int:
    tickets = from_tickets("data/tickets/synthetic_tickets.json")
    cases: list[EvalCase] = list(tickets)

    for case in tickets:
        if case.category != "defect":
            continue
        cases.append(
            EvalCase(
                id=f"{case.id}-L",
                category="localize",
                query=f"{case.query}，根因在哪个文件？",
                expected_files=case.expected_files,
                expected_symbols=case.expected_symbols,
                source="synthetic-ticket",
            )
        )

    for index, doc_case in enumerate(from_docs("docs", limit=40), start=1):
        title = doc_case.expected_answer_contains or ""
        cases.append(
            EvalCase(
                id=f"U-{index:03d}",
                category="usage",
                query=f"如何使用{title}？",
                expected_answer_contains=title,
                source=doc_case.source,
            )
        )
        cases.append(doc_case)

    security = load_cases("data/eval/injection_cases.jsonl")
    cases.extend(security)

    path = save_cases(cases, "data/eval/seed.jsonl")
    stats = {
        "total": len(cases),
        "by_category": category_distribution(cases),
        "missing_annotations": missing_summary(cases),
        "tickets_imported": len(tickets),
        "tickets_missing": missing_summary(tickets),
        "security_cases": len(security),
        "path": str(path),
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    assert len(cases) >= 200, f"评测集不足 200 条：{len(cases)}"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
