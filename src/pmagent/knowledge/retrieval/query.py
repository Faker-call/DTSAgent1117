"""查询处理：标识符拆词、多查询变体与意图调权。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_:]*")
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

INTENT_WEIGHTS: dict[str, dict[str, float]] = {
    "usage": {"bm25": 0.8, "vector": 1.2, "graph": 0.6, "doc": 1.2},
    "concept": {"bm25": 0.9, "vector": 1.1, "graph": 0.7, "doc": 1.1},
    "defect": {"bm25": 1.1, "vector": 1.0, "graph": 1.2, "doc": 0.9},
    "localize": {"bm25": 1.2, "vector": 0.8, "graph": 1.3, "doc": 0.7},
}
DEFAULT_WEIGHTS = {"bm25": 1.0, "vector": 1.0, "graph": 1.0, "doc": 1.0}


def split_identifier(name: str) -> list[str]:
    """把 camelCase / snake_case / :: 拆成小写词，并保留原串。"""
    pieces: list[str] = []
    for part in re.split(r"::", name):
        for chunk in re.split(r"[_\-]+", part):
            if not chunk:
                continue
            pieces.extend(piece.lower() for piece in CAMEL.split(chunk) if piece)
    tokens = [piece for piece in pieces if len(piece) > 1]
    if name.lower() not in tokens:
        tokens.append(name.lower())
    return tokens


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in IDENTIFIER.findall(text):
        tokens.extend(split_identifier(match))
    for run in CJK_RUN.findall(text):
        tokens.append(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token and token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


@dataclass
class QueryPlan:
    raw: str
    tokens: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    intent: str = "usage"

    @property
    def weights(self) -> dict[str, float]:
        return INTENT_WEIGHTS.get(self.intent, DEFAULT_WEIGHTS)


def plan_query(raw: str, *, intent: str = "usage") -> QueryPlan:
    tokens = tokenize(raw)
    identifiers = [value for value in IDENTIFIER.findall(raw) if "_" in value or "::" in value]
    identifiers += [value for value in IDENTIFIER.findall(raw) if CAMEL.search(value)]
    variants = [raw]
    spaced = " ".join(tokens)
    if spaced and spaced != raw:
        variants.append(spaced)
    identifier_tokens: list[str] = []
    for value in identifiers:
        identifier_tokens.extend(split_identifier(value))
    symbol_hint = " ".join(dict.fromkeys(identifier_tokens)).strip()
    if symbol_hint and symbol_hint not in variants:
        variants.append(symbol_hint)
    return QueryPlan(
        raw=raw,
        tokens=tokens,
        variants=variants[:3],
        symbols=sorted(set(identifiers)),
        intent=intent,
    )
