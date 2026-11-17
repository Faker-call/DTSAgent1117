"""上下文压缩：四级瀑布 + 事实回归校验；预算账本按阶段预留。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pmagent.errors import BudgetExceeded
from pmagent.memory.ledger import Card

ERROR_CODE = re.compile(r"\b[A-Z]{1,6}[-_]?\d{2,6}\b")
SYMBOL = re.compile(r"\b[A-Za-z_]\w*::[A-Za-z_]\w*\b")
VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

FACT_PATTERNS = (ERROR_CODE, SYMBOL, VERSION, TIMESTAMP)
MIN_COVERAGE = 0.8


def extract_facts(text: str) -> set[str]:
    facts: set[str] = set()
    for pattern in FACT_PATTERNS:
        facts.update(pattern.findall(text))
    return facts


@dataclass
class CompressionResult:
    level: int
    text: str
    kept_ids: list[str]
    coverage: float
    rolled_back: bool = False
    degraded: bool = False


class ContextCompressor:
    """C1 结构化抽取 → C2 结果收缩 → C3 对话摘要 → C4 降级终止。"""

    LEVELS = 4

    def compress(
        self,
        cards: list[Card],
        *,
        facts: set[str] | None = None,
        target_chars: int = 4000,
    ) -> CompressionResult:
        facts = facts if facts is not None else extract_facts("\n".join(c.text for c in cards))
        previous: CompressionResult | None = None
        for level in range(1, self.LEVELS + 1):
            result = self._apply(level, cards, target_chars)
            coverage = self._coverage(facts, result.text)
            result.coverage = coverage
            if coverage < MIN_COVERAGE and previous is not None:
                previous.rolled_back = True
                return previous
            if len(result.text) <= target_chars or level == self.LEVELS:
                return result
            previous = result
        return previous or CompressionResult(0, "", [], 1.0, degraded=True)

    def _apply(self, level: int, cards: list[Card], target_chars: int) -> CompressionResult:
        if level == 1:
            parts = [self._structured(card) for card in cards]
            return CompressionResult(1, "\n".join(parts), [c.evidence_id for c in cards], 1.0)
        if level == 2:
            kept = sorted(cards, key=lambda card: card.score, reverse=True)[
                : max(1, len(cards) // 2)
            ]
            text = "\n".join(self._structured(card) for card in kept)
            return CompressionResult(2, text, [c.evidence_id for c in kept], 1.0)
        if level == 3:
            summary = "；".join(card.text.strip().splitlines()[0][:80] for card in cards[:5])
            return CompressionResult(3, summary[:target_chars], [c.evidence_id for c in cards], 1.0)
        return CompressionResult(4, "", [], 1.0, degraded=True)

    @staticmethod
    def _structured(card: Card) -> str:
        lines = card.text.strip().splitlines()
        if len(lines) <= 3:
            return f"[{card.evidence_id}] " + "\n".join(lines)
        head = "\n".join(lines[:3])
        return f"[{card.evidence_id}] {head}\n... ({len(lines) - 3} 行省略)"

    @staticmethod
    def _coverage(facts: set[str], text: str) -> float:
        if not facts:
            return 1.0
        return len(facts & extract_facts(text)) / len(facts)


@dataclass
class BudgetLedger:
    total: int
    reserves: dict[str, int] = field(default_factory=dict)
    used: dict[str, int] = field(default_factory=dict)

    def allocate(self, stage: str, tokens: int) -> int:
        self.reserves[stage] = max(0, tokens)
        self.used.setdefault(stage, 0)
        return self.reserves[stage]

    def consume(self, stage: str, tokens: int) -> None:
        self.used[stage] = self.used.get(stage, 0) + max(0, tokens)
        if self.consumed > self.total:
            raise BudgetExceeded(
                f"上下文预算超限（{self.consumed}/{self.total}）",
                hint="缩小检索范围或提高 budget.max_tokens",
            )

    @property
    def consumed(self) -> int:
        return sum(self.used.values())

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.consumed)

    def stage_remaining(self, stage: str) -> int:
        return max(0, self.reserves.get(stage, 0) - self.used.get(stage, 0))
