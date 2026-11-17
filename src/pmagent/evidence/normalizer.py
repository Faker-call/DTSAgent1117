"""证据归一化：装配 ProblemContext、脱敏与充分性判定。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from pmagent.contracts import (
    EvidenceRequest,
    ProblemContext,
    Product,
    RedactionRecord,
    ReproEvidence,
    Symptom,
    TicketRef,
)
from pmagent.evidence.crash import CrashInspector
from pmagent.evidence.logs import LogAnalysis, LogAnalyzer
from pmagent.evidence.redaction import Redactor
from pmagent.evidence.sandbox import ReproSandbox
from pmagent.evidence.stacks import StackSymbolizer, SymbolicationResult
from pmagent.evidence.vision import VisualAnalyzer

MINIMAL_EVIDENCE = {
    "defect": "日志 / 堆栈 / 崩溃转储 / 复现工程 / 截图（任一项）",
    "localize": "带文件行号的堆栈或明确的代码位置",
    "usage": "",
    "concept": "",
}


@dataclass
class NormalizedEvidence:
    context: ProblemContext
    missing: list[EvidenceRequest] = field(default_factory=list)
    log_analyses: list[LogAnalysis] = field(default_factory=list)
    stack_results: list[SymbolicationResult] = field(default_factory=list)


class EvidenceNormalizer:
    def __init__(
        self,
        *,
        redactor: Redactor | None = None,
        log_analyzer: LogAnalyzer | None = None,
        symbolizer: StackSymbolizer | None = None,
        crash_inspector: CrashInspector | None = None,
        visual_analyzer: VisualAnalyzer | None = None,
        sandbox: ReproSandbox | None = None,
    ) -> None:
        self.redactor = redactor or Redactor()
        self.log_analyzer = log_analyzer or LogAnalyzer()
        self.symbolizer = symbolizer or StackSymbolizer()
        self.crash_inspector = crash_inspector or CrashInspector()
        self.visual_analyzer = visual_analyzer or VisualAnalyzer()
        self.sandbox = sandbox or ReproSandbox()

    def build(
        self,
        query: str,
        *,
        logs: list[str] | None = None,
        stacks: list[str] | None = None,
        crash: str | None = None,
        shots: list[str] | None = None,
        repro: str | None = None,
        product: Product | None = None,
        tickets: list[TicketRef] | None = None,
        intent: str = "defect",
    ) -> NormalizedEvidence:
        redacted_query = self.redactor.redact(query)
        context = ProblemContext(
            id=uuid.uuid4().hex[:12],
            raw_query=redacted_query.text,
            product=product or Product(),
            symptoms=[Symptom(description=redacted_query.text or "未描述症状")]
            if redacted_query.text
            else [],
            redaction=RedactionRecord(rules_applied=redacted_query.rules_applied),
            tickets=tickets or [],
        )

        analyses: list[LogAnalysis] = []
        for path in logs or []:
            analysis = self.log_analyzer.analyze(path)
            context.logs.append(analysis.evidence)
            analyses.append(analysis)

        stack_results: list[SymbolicationResult] = []
        for path in stacks or []:
            result = self.symbolizer.symbolicate(path)
            context.stacks.append(result.evidence)
            stack_results.append(result)

        if crash:
            context.crash = self.crash_inspector.inspect(crash)

        for shot in shots or []:
            context.visuals.append(self.visual_analyzer.analyze(shot).evidence)

        if repro:
            sandbox_result = self.sandbox.run(repro)
            context.repro = ReproEvidence(
                root_path=str(repro),
                build_cmd=sandbox_result.build.command if sandbox_result.build else None,
                run_cmd=sandbox_result.run.command if sandbox_result.run else None,
                sandbox_result=sandbox_result.as_dict(),
            )

        missing = self.assess(context, intent=intent)
        context.missing = missing
        return NormalizedEvidence(
            context=context,
            missing=missing,
            log_analyses=analyses,
            stack_results=stack_results,
        )

    def assess(self, context: ProblemContext, *, intent: str = "defect") -> list[EvidenceRequest]:
        missing: list[EvidenceRequest] = []
        if intent in {"defect", "localize"}:
            has_evidence = bool(
                context.logs or context.stacks or context.crash or context.repro or context.visuals
            )
            if not has_evidence:
                missing.append(
                    EvidenceRequest(
                        needed=MINIMAL_EVIDENCE[intent],
                        purpose="确定触发条件与失败位置，缺少证据只能给出泛化建议",
                    )
                )
            if intent == "localize" and not self._has_location(context):
                missing.append(
                    EvidenceRequest(
                        needed="带文件行号的堆栈或明确的代码位置",
                        purpose="没有可定位的代码位置就无法收敛根因",
                    )
                )
        if not context.product.sdk_version:
            missing.append(
                EvidenceRequest(needed="SDK 版本", purpose="过滤版本相关的文档与缺陷记录")
            )
        if context.visuals and not context.product.platform:
            missing.append(
                EvidenceRequest(needed="平台与渲染后端", purpose="视觉差异常与后端分支相关")
            )
        return missing

    @staticmethod
    def _has_location(context: ProblemContext) -> bool:
        for stack in context.stacks:
            if any(frame.file and frame.line for frame in stack.frames):
                return True
        return False


__all__ = ["EvidenceNormalizer", "MINIMAL_EVIDENCE", "NormalizedEvidence"]
