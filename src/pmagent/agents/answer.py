"""回答组装与引用校验：结论-依据-示例骨架，引用必须可核验。"""

from __future__ import annotations

from dataclasses import dataclass, field

from pmagent.contracts import RetrievalHit


@dataclass
class Citation:
    citation_id: str
    kind: str
    ref: str
    snippet: str = ""
    chunk_id: str = ""


@dataclass
class Answer:
    conclusion: str
    basis: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    valid: bool
    removed: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


class AnswerComposer:
    def compose(self, question: str, hits: list[RetrievalHit]) -> Answer:
        if not hits:
            return Answer(
                conclusion="没有检索到可引用的资料，无法给出结论。",
                notes=["知识库中未命中；建议补充版本信息或换个说法"],
                confidence=0.2,
            )

        citations: list[Citation] = []
        basis: list[str] = []
        examples: list[str] = []
        for index, hit in enumerate(hits[:5], start=1):
            citation_id = f"C{index}"
            location = hit.path or ""
            if hit.line_start:
                location = f"{location}:{hit.line_start}-{hit.line_end or hit.line_start}"
            citations.append(
                Citation(
                    citation_id=citation_id,
                    kind=hit.kind,
                    ref=location,
                    snippet=hit.snippet[:200],
                    chunk_id=hit.chunk_id,
                )
            )
            first_line = (hit.snippet or "").strip().splitlines()[0][:100] if hit.snippet else ""
            basis.append(f"[{citation_id}] {hit.symbol or location} — {first_line}")
            if hit.kind == "code" and len(examples) < 2:
                examples.append(f"```\n{hit.snippet[:300]}\n```")

        top = hits[0]
        conclusion = (
            f"根据 {len(citations)} 条资料，最相关的是 "
            f"{top.symbol or top.path or '检索结果'}，请以引用内容为准。"
        )
        confidence = min(0.9, 0.4 + 0.1 * len(citations))
        notes: list[str] = []
        if len(hits) == 1:
            notes.append("仅命中一条资料，结论覆盖面有限")
        return Answer(
            conclusion=conclusion,
            basis=basis,
            examples=examples,
            citations=citations,
            confidence=confidence,
            notes=notes,
        )


class CitationValidator:
    """规则层校验 + 可选的模型支持度检查；无法校验时显式标注。"""

    def __init__(self, code_index=None, doc_corpus=None, support_check=None) -> None:  # noqa: ANN001
        self.code_index = code_index
        self.doc_corpus = doc_corpus
        self.support_check = support_check

    def validate(self, answer: Answer, question: str = "") -> ValidationResult:
        removed: list[str] = []
        issues: list[str] = []
        for citation in list(answer.citations):
            status = self._check(citation)
            if status is None:
                issues.append(f"{citation.citation_id} 无法校验（缺少校验源）")
            elif not status:
                removed.append(citation.citation_id)
                issues.append(f"{citation.citation_id} 引用不存在，已移除")
                continue
            if self.support_check is not None and not self.support_check(question, citation):
                removed.append(citation.citation_id)
                issues.append(f"{citation.citation_id} 模型判定结论未被引用内容支持，已移除")

        if removed:
            answer.citations = [
                item for item in answer.citations if item.citation_id not in removed
            ]
            keep = [item for item in answer.basis if not self._mentions(item, removed)]
            dropped = [item for item in answer.basis if self._mentions(item, removed)]
            answer.basis = keep + [f"{item}（未核实）" for item in dropped]
            answer.notes.append("部分引用已移除，相关结论降级为未核实")
        return ValidationResult(valid=not removed, removed=removed, issues=issues)

    def _check(self, citation: Citation) -> bool | None:
        if citation.kind == "code":
            if self.code_index is None:
                return None
            row = self.code_index.conn.execute(
                "SELECT 1 FROM chunks WHERE id = ? LIMIT 1",
                (self._chunk_number(citation.chunk_id or citation.ref),),
            ).fetchone()
            return row is not None
        if self.doc_corpus is None:
            return None
        row = self.doc_corpus._conn.execute(
            "SELECT 1 FROM doc_chunks WHERE id = ? LIMIT 1",
            (self._doc_number(citation.chunk_id or citation.ref),),
        ).fetchone()
        return row is not None

    @staticmethod
    def _chunk_number(ref: str) -> int:
        digits = "".join(char for char in ref if char.isdigit())
        return int(digits) if digits else -1

    @staticmethod
    def _doc_number(ref: str) -> int:
        if ref.startswith("doc-"):
            ref = ref[4:]
        digits = "".join(char for char in ref if char.isdigit())
        return int(digits) if digits else -1

    @staticmethod
    def _mentions(line: str, ids: list[str]) -> bool:
        return any(f"[{citation_id}]" in line for citation_id in ids)


__all__ = ["Answer", "AnswerComposer", "Citation", "CitationValidator", "ValidationResult"]
