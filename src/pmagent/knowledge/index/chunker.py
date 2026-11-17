"""AST 感知切块：函数整块、超长按窗口切并重复前缀、类成员独立。"""

from __future__ import annotations

from pmagent.knowledge.index.models import Chunk, ParsedFile, Symbol

CHUNK_POLICY_VERSION = "1"
MAX_TOKENS = 1200
MAX_CHARS = MAX_TOKENS * 4
OVERLAP_LINES = 3


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _prefix(path: str, symbol: Symbol) -> str:
    return (
        f"// file: {path}\n"
        f"// symbol: {symbol.qualified_name}\n"
        f"// signature: {symbol.signature}\n"
    )


def _slice(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


def _split_body(text: str, prefix: str) -> list[str]:
    body_lines = text.splitlines()
    pieces: list[str] = []
    index = 0
    while index < len(body_lines):
        size = 0
        cursor = index
        while cursor < len(body_lines) and size + len(body_lines[cursor]) + 1 <= MAX_CHARS:
            size += len(body_lines[cursor]) + 1
            cursor += 1
        if cursor == index:
            cursor += 1
        pieces.append(prefix + "\n".join(body_lines[index:cursor]))
        if cursor >= len(body_lines):
            break
        index = max(index + 1, cursor - OVERLAP_LINES)
    return pieces


def build_chunks(parsed: ParsedFile, source: str) -> list[Chunk]:
    lines = source.splitlines()
    if not lines:
        return []

    chunks: list[Chunk] = []
    functions = [s for s in parsed.symbols if s.kind == "function"]
    classes = [s for s in parsed.symbols if s.kind == "class"]
    macros = [s for s in parsed.symbols if s.kind == "macro"]

    for symbol in functions:
        text = _slice(lines, symbol.line_start, symbol.line_end)
        if not text.strip():
            continue
        prefix = _prefix(parsed.path, symbol)
        if estimate_tokens(text) <= MAX_TOKENS:
            chunks.append(
                Chunk(
                    symbol_qualified=symbol.qualified_name,
                    path=parsed.path,
                    line_start=symbol.line_start,
                    line_end=symbol.line_end,
                    text=prefix + text,
                    token_estimate=estimate_tokens(prefix + text),
                    kind="function",
                )
            )
            continue

        pieces = _split_body(text, prefix)
        cursor = symbol.line_start
        for piece in pieces:
            piece_lines = piece.count("\n") - prefix.count("\n") + 1
            chunks.append(
                Chunk(
                    symbol_qualified=symbol.qualified_name,
                    path=parsed.path,
                    line_start=cursor,
                    line_end=min(symbol.line_end, cursor + piece_lines - 1),
                    text=piece,
                    token_estimate=estimate_tokens(piece),
                    kind="function_part",
                )
            )
            cursor += max(1, piece_lines - OVERLAP_LINES)

    for symbol in classes:
        member_starts = [
            f.line_start
            for f in functions
            if f.line_start >= symbol.line_start and f.line_end <= symbol.line_end
        ]
        end = min(member_starts) - 1 if member_starts else symbol.line_end
        if end < symbol.line_start:
            continue
        text = _slice(lines, symbol.line_start, end)
        if not text.strip():
            continue
        prefix = _prefix(parsed.path, symbol)
        chunks.append(
            Chunk(
                symbol_qualified=symbol.qualified_name,
                path=parsed.path,
                line_start=symbol.line_start,
                line_end=end,
                text=prefix + text,
                token_estimate=estimate_tokens(prefix + text),
                kind="class",
            )
        )

    for symbol in macros:
        text = _slice(lines, symbol.line_start, symbol.line_end)
        if not text.strip():
            continue
        prefix = _prefix(parsed.path, symbol)
        chunks.append(
            Chunk(
                symbol_qualified=symbol.qualified_name,
                path=parsed.path,
                line_start=symbol.line_start,
                line_end=symbol.line_end,
                text=prefix + text,
                token_estimate=estimate_tokens(prefix + text),
                kind="macro",
            )
        )

    return chunks
