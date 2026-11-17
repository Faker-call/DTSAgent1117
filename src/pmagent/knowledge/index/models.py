"""索引数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FileRecord:
    path: str
    sha: str = ""
    lang: str = "cpp"
    loc: int = 0
    commit: str = ""


@dataclass
class Symbol:
    name: str
    qualified_name: str
    kind: str  # function | declaration | class | macro | namespace
    signature: str
    line_start: int
    line_end: int
    parent: str | None = None
    namespace: str = ""
    class_name: str = ""


@dataclass
class RefEdge:
    from_symbol: str
    to_name: str
    kind: str = "call"
    line: int = 0
    confidence: str = "heuristic"


@dataclass
class IncludeEdge:
    source_path: str
    target: str


@dataclass
class ParsedFile:
    path: str
    symbols: list[Symbol] = field(default_factory=list)
    refs: list[RefEdge] = field(default_factory=list)
    includes: list[IncludeEdge] = field(default_factory=list)
    parse_error: str | None = None


@dataclass
class Chunk:
    symbol_qualified: str
    path: str
    line_start: int
    line_end: int
    text: str
    token_estimate: int
    kind: str = "function"
