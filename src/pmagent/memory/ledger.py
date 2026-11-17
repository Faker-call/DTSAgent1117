"""证据账本：上下文中只放索引卡片，正文按需取回。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pmagent.contracts import EvidenceItem


@dataclass
class LedgerEntry:
    item: EvidenceItem
    body: str = ""
    reloader: Callable[[], str] | None = None


class EvidenceLedger:
    def __init__(self) -> None:
        self._entries: dict[str, LedgerEntry] = {}
        self._counter = 0

    def register(
        self,
        *,
        kind: str,
        ref: dict,
        summary: str = "",
        body: str = "",
        symbol: str | None = None,
        score: float = 0.0,
        channels: list[str] | None = None,
        rehydrate_hint: str | None = None,
        reloader: Callable[[], str] | None = None,
    ) -> EvidenceItem:
        self._counter += 1
        evidence_id = f"E{self._counter}"
        item = EvidenceItem(
            evidence_id=evidence_id,
            kind=kind,  # type: ignore[arg-type]
            ref=ref,
            symbol=symbol,
            channels=channels or [],
            score=score,
            summary=summary or body.strip().splitlines()[0][:120] if (summary or body) else "",
            rehydrate_hint=rehydrate_hint,
        )
        self._entries[evidence_id] = LedgerEntry(item=item, body=body, reloader=reloader)
        return item

    def card(self, evidence_id: str) -> str:
        entry = self._entries[evidence_id]
        item = entry.item
        location = (
            item.ref.get("path") or item.ref.get("url") or item.ref.get("ticket_id") or ""
        )
        if item.ref.get("line_start"):
            start = item.ref["line_start"]
            end = item.ref.get("line_end", start)
            location = f"{location}:{start}-{end}"
        channels = ",".join(item.channels) if item.channels else "-"
        return (
            f"[{item.evidence_id}] {item.kind} {location} "
            f"score={item.score:.2f} channels={channels} :: {item.summary}"
        )

    def cards(self, evidence_ids: list[str] | None = None) -> list[str]:
        ids = evidence_ids or list(self._entries)
        return [self.card(evidence_id) for evidence_id in ids if evidence_id in self._entries]

    def body(self, evidence_id: str) -> str:
        entry = self._entries[evidence_id]
        if entry.body:
            return entry.body
        if entry.reloader is not None:
            return entry.reloader()
        return ""

    def rehydrate(self, evidence_id: str) -> str:
        return self.body(evidence_id)

    def items(self) -> list[EvidenceItem]:
        return [entry.item for entry in self._entries.values()]

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class Card:
    """压缩时使用的证据卡片视图。"""

    evidence_id: str
    text: str
    score: float = 0.0
    kind: str = "code"
    channels: list[str] = field(default_factory=list)
