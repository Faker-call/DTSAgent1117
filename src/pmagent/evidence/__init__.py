"""证据层：日志、堆栈、崩溃、视觉、复现沙箱与脱敏（M08）。"""

from pmagent.evidence.crash import CrashInspector
from pmagent.evidence.logs import LogAnalysis, LogAnalyzer, TemplateStat, mask_line
from pmagent.evidence.normalizer import EvidenceNormalizer, NormalizedEvidence
from pmagent.evidence.redaction import RedactionResult, RedactionRule, Redactor
from pmagent.evidence.sandbox import (
    CommandResult,
    ReproConfig,
    ReproSandbox,
    SandboxResult,
    load_repro_config,
)
from pmagent.evidence.stacks import StackSymbolizer, SymbolicationResult, parse_frames
from pmagent.evidence.vision import ModelDescriber, Region, VisualAnalysis, VisualAnalyzer

__all__ = [
    "CommandResult",
    "CrashInspector",
    "EvidenceNormalizer",
    "LogAnalysis",
    "LogAnalyzer",
    "ModelDescriber",
    "NormalizedEvidence",
    "RedactionResult",
    "RedactionRule",
    "Redactor",
    "Region",
    "ReproConfig",
    "ReproSandbox",
    "SandboxResult",
    "StackSymbolizer",
    "SymbolicationResult",
    "TemplateStat",
    "VisualAnalysis",
    "VisualAnalyzer",
    "load_repro_config",
    "mask_line",
    "parse_frames",
]
