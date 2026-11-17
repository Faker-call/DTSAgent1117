"""评测数据集：格式、导入、标注缺失检测与分层抽样。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

CATEGORIES = ("usage", "concept", "defect", "localize", "security")
REQUIRED_BY_CATEGORY = {
    "usage": "expected_answer_contains",
    "concept": "expected_answer_contains",
    "defect": "expected_files",
    "localize": "expected_files",
    "security": "expected_behavior",
}


class EvalCase(BaseModel):
    id: str
    category: str
    query: str
    expected_files: list[str] = Field(default_factory=list)
    expected_symbols: list[str] = Field(default_factory=list)
    expected_answer_contains: str | None = None
    expected_confidence: float | None = None
    expected_behavior: str | None = None
    source: str = ""
    notes: str = ""

    def missing(self) -> list[str]:
        missing: list[str] = []
        if self.category not in CATEGORIES:
            missing.append("category")
        required = REQUIRED_BY_CATEGORY.get(self.category)
        if required == "expected_files" and not self.expected_files:
            missing.append("expected_files")
        if required == "expected_answer_contains" and not self.expected_answer_contains:
            missing.append("expected_answer_contains")
        if required == "expected_behavior" and not self.expected_behavior:
            missing.append("expected_behavior")
        return missing


def load_cases(path: str | Path) -> list[EvalCase]:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        cases = [
            EvalCase.model_validate(json.loads(line))
            for line in text.splitlines()
            if line.strip()
        ]
    else:
        payload = json.loads(text)
        if isinstance(payload, dict) and "tickets" in payload:
            return from_tickets(path)
        raw = payload.get("cases", payload) if isinstance(payload, dict) else payload
        cases = [EvalCase.model_validate(item) for item in raw]
    return cases


def save_cases(cases: list[EvalCase], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(case.model_dump_json() for case in cases) + "\n", encoding="utf-8"
    )
    return path


def missing_summary(cases: list[EvalCase]) -> dict[str, int]:
    summary: Counter[str] = Counter()
    for case in cases:
        for field in case.missing():
            summary[field] += 1
    return dict(summary)


def category_distribution(cases: list[EvalCase]) -> dict[str, int]:
    return dict(Counter(case.category for case in cases))


def from_tickets(path: str | Path) -> list[EvalCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for ticket in payload.get("tickets", []):
        category = "usage" if ticket.get("category") == "usage" else "defect"
        cases.append(
            EvalCase(
                id=f"T-{ticket['id']}",
                category=category,
                query=ticket["symptom"],
                expected_files=(
                    [ticket["root_cause_file"]] if ticket.get("root_cause_file") else []
                ),
                expected_symbols=(
                    [ticket["root_cause_symbol"]]
                    if ticket.get("root_cause_symbol")
                    else []
                ),
                source="synthetic-ticket",
                notes=ticket.get("root_cause", ""),
            )
        )
    return cases


HEADING = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)


def from_docs(docs_dir: str | Path, limit: int = 40) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in sorted(Path(docs_dir).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for index, match in enumerate(HEADING.finditer(text), start=1):
            title = match.group(2).strip()
            if len(title) < 3 or title.startswith(("已知", "说明", "实测", "下一步")):
                continue
            cases.append(
                EvalCase(
                    id=f"D-{path.stem}-{index}",
                    category="concept",
                    query=f"什么是{title}",
                    expected_answer_contains=title,
                    source=f"docs/{path.name}",
                )
            )
            if len(cases) >= limit:
                return cases
    return cases


def stratify(cases: list[EvalCase], per_category: int) -> list[EvalCase]:
    selected: list[EvalCase] = []
    for category in CATEGORIES:
        bucket = [case for case in cases if case.category == category][:per_category]
        selected.extend(bucket)
    return selected


__all__ = [
    "CATEGORIES",
    "EvalCase",
    "REQUIRED_BY_CATEGORY",
    "category_distribution",
    "from_docs",
    "from_tickets",
    "load_cases",
    "missing_summary",
    "save_cases",
    "stratify",
]
