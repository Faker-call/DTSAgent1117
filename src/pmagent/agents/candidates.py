"""候选生成：堆栈 / 检索 / 相似案例 / 可疑提交四源合并。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pmagent.contracts import ProblemContext
from pmagent.knowledge.index import CodeIndex
from pmagent.knowledge.retrieval import HybridRetriever


@dataclass
class Candidate:
    symbol: str
    file: str
    line_start: int = 0
    line_end: int = 0
    sources: set[str] = field(default_factory=set)
    retrieval_score: float = 0.0
    text: str = ""
    commits: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.file}::{self.symbol}"


class CandidateFinder:
    def __init__(
        self,
        code_index: CodeIndex,
        *,
        retriever: HybridRetriever | None = None,
        case_prior: Callable[[str], dict[str, float]] | None = None,
        repo_root: str | Path | None = None,
        commit_window: int = 5,
    ) -> None:
        self.code_index = code_index
        self.retriever = retriever or HybridRetriever(code_index=code_index)
        self.case_prior = case_prior
        self.repo_root = Path(repo_root) if repo_root else None
        self.commit_window = commit_window

    def find(self, context: ProblemContext, *, limit: int = 20) -> list[Candidate]:
        merged: dict[str, Candidate] = {}
        for candidate in self._from_stack(context):
            self._merge(merged, candidate)
        for candidate in self._from_retrieval(context):
            self._merge(merged, candidate)
        for candidate in self._from_cases(context):
            self._merge(merged, candidate)
        candidates = sorted(
            merged.values(),
            key=lambda item: (len(item.sources), item.retrieval_score),
            reverse=True,
        )[:limit]
        for candidate in candidates:
            candidate.commits = self._recent_commits(candidate.file)
        return candidates

    # -- 四源 -------------------------------------------------------------
    def _from_stack(self, context: ProblemContext) -> list[Candidate]:
        results: list[Candidate] = []
        for stack in context.stacks:
            for frame in stack.frames:
                if frame.file and frame.line:
                    row = self.code_index.conn.execute(
                        "SELECT qualified_name FROM symbols WHERE file_path = ?"
                        " AND line_start <= ? AND line_end >= ?"
                        " AND kind IN ('function', 'declaration')"
                        " ORDER BY (line_end - line_start) ASC LIMIT 1",
                        (frame.file, frame.line, frame.line),
                    ).fetchone()
                    if row is None:
                        row = self.code_index.conn.execute(
                            "SELECT qualified_name FROM symbols WHERE file_path = ?"
                            " AND line_start <= ? AND line_end >= ?"
                            " ORDER BY (line_end - line_start) ASC LIMIT 1",
                            (frame.file, frame.line, frame.line),
                        ).fetchone()
                    symbol = row["qualified_name"] if row else (frame.function or "")
                    results.append(
                        Candidate(
                            symbol=symbol,
                            file=frame.file,
                            line_start=frame.line,
                            line_end=frame.line,
                            sources={"stack"},
                        )
                    )
                elif frame.function:
                    for hit in self.code_index.find_symbols(frame.function)[:3]:
                        results.append(
                            Candidate(
                                symbol=hit["qualified_name"],
                                file=hit["file_path"],
                                line_start=hit["line_start"],
                                line_end=hit["line_end"],
                                sources={"stack"},
                            )
                        )
        return results

    def _from_retrieval(self, context: ProblemContext) -> list[Candidate]:
        query = context.raw_query or " ".join(s.description for s in context.symptoms)
        hits = self.retriever.search(query, intent="localize", limit=10)
        results: list[Candidate] = []
        for hit in hits:
            if hit.kind != "code" or not hit.path or not hit.symbol:
                continue
            results.append(
                Candidate(
                    symbol=hit.symbol,
                    file=hit.path,
                    line_start=hit.line_start or 0,
                    line_end=hit.line_end or 0,
                    sources={"retrieval"},
                    retrieval_score=hit.score,
                    text=hit.snippet,
                )
            )
        return results

    def _from_cases(self, context: ProblemContext) -> list[Candidate]:
        if self.case_prior is None:
            return []
        query = context.raw_query or " ".join(s.description for s in context.symptoms)
        prior = self.case_prior(query) or {}
        results: list[Candidate] = []
        for key, weight in prior.items():
            if "::" in key and "/" not in key:
                for hit in self.code_index.find_symbols(key)[:1]:
                    results.append(
                        Candidate(
                            symbol=hit["qualified_name"],
                            file=hit["file_path"],
                            line_start=hit["line_start"],
                            line_end=hit["line_end"],
                            sources={"case"},
                            retrieval_score=float(weight),
                        )
                    )
            else:
                row = self.code_index.conn.execute(
                    "SELECT qualified_name, line_start, line_end FROM symbols"
                    " WHERE file_path = ? LIMIT 1",
                    (key,),
                ).fetchone()
                if row:
                    results.append(
                        Candidate(
                            symbol=row["qualified_name"],
                            file=key,
                            line_start=row["line_start"],
                            line_end=row["line_end"],
                            sources={"case"},
                            retrieval_score=float(weight),
                        )
                    )
        return results

    # -- 提交 -------------------------------------------------------------
    def _recent_commits(self, file_path: str) -> list[str]:
        if self.repo_root is None:
            return []
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo_root),
                    "log",
                    f"-n{self.commit_window}",
                    "--pretty=format:%h %s",
                    "--",
                    file_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0:
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    # -- 合并 -------------------------------------------------------------
    @staticmethod
    def _merge(merged: dict[str, Candidate], candidate: Candidate) -> None:
        existing = merged.get(candidate.key)
        if existing is None:
            merged[candidate.key] = candidate
            return
        existing.sources |= candidate.sources
        existing.retrieval_score = max(existing.retrieval_score, candidate.retrieval_score)
        if not existing.text:
            existing.text = candidate.text
        if not existing.line_start:
            existing.line_start = candidate.line_start
            existing.line_end = candidate.line_end


def candidate_summary(candidates: list[Candidate]) -> str:
    return json.dumps(
        [
            {"symbol": item.symbol, "file": item.file, "sources": sorted(item.sources)}
            for item in candidates
        ],
        ensure_ascii=False,
    )


__all__ = ["Candidate", "CandidateFinder", "candidate_summary"]
