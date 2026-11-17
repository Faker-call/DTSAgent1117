"""子 Agent 层：意图识别、问答组装、澄清、多轮对话与故障定位（M09/M10）。"""

from pmagent.agents.answer import Answer, AnswerComposer, Citation, CitationValidator
from pmagent.agents.candidates import Candidate, CandidateFinder
from pmagent.agents.clarify import ClarificationPolicy
from pmagent.agents.diagnosis import DiagnosisAgent, render_markdown
from pmagent.agents.dialogue import DialogueManager, DialogueTurn
from pmagent.agents.intent import (
    ClassificationResult,
    Features,
    IntentClassifier,
    IntentRules,
    extract_features,
)
from pmagent.agents.patch import (
    LLMPatchGenerator,
    PatchAuthor,
    PatchGenerator,
    PatchStore,
    apply_patch,
    revert_patch,
)
from pmagent.agents.toolbroker import ToolBroker
from pmagent.agents.triage import stage_label, triage, verification_steps

__all__ = [
    "Answer",
    "AnswerComposer",
    "Candidate",
    "CandidateFinder",
    "Citation",
    "CitationValidator",
    "ClarificationPolicy",
    "ClassificationResult",
    "DiagnosisAgent",
    "DialogueManager",
    "DialogueTurn",
    "Features",
    "IntentClassifier",
    "IntentRules",
    "LLMPatchGenerator",
    "PatchAuthor",
    "PatchGenerator",
    "PatchStore",
    "ToolBroker",
    "apply_patch",
    "extract_features",
    "render_markdown",
    "revert_patch",
    "stage_label",
    "triage",
    "verification_steps",
]
