"""多轮对话：意图漂移检测与误路由回退。"""

from __future__ import annotations

from dataclasses import dataclass

from pmagent.agents.clarify import ClarificationPolicy
from pmagent.agents.intent import ClassificationResult, IntentClassifier
from pmagent.contracts import SessionState


@dataclass
class DialogueTurn:
    result: ClassificationResult
    route: str
    clarification: str | None = None
    drift: bool = False


class DialogueManager:
    def __init__(
        self,
        state: SessionState,
        *,
        classifier: IntentClassifier | None = None,
        policy: ClarificationPolicy | None = None,
    ) -> None:
        self.state = state
        self.classifier = classifier or IntentClassifier()
        self.policy = policy or ClarificationPolicy()

    def handle(
        self,
        text: str,
        *,
        logs: int = 0,
        stacks: int = 0,
        shots: int = 0,
        repro: bool = False,
    ) -> DialogueTurn:
        result = self.classifier.classify(
            text, logs=logs, stacks=stacks, shots=shots, repro=repro
        )
        decision = result.decision
        drift = self.state.intent is not None and self.state.intent != decision.primary
        if drift:
            decision.note = (decision.note + "；" if decision.note else "") + (
                f"意图由 {self.state.intent} 切换为 {decision.primary}，保留已收集证据"
            )
        self.state.intent = decision.primary
        self.state.turn_summary = text[:200]

        route = "clarify" if decision.clarification else self._route(decision.primary)
        if route == "diagnose" and not decision.evidence_gate.get("passed"):
            route = "clarify"
            decision.clarification = (
                decision.clarification
                or "已识别为故障排查，但还没有可解析的证据；能提供日志、堆栈或复现步骤吗？"
            )
        return DialogueTurn(
            result=result,
            route=route,
            clarification=decision.clarification,
            drift=drift,
        )

    @staticmethod
    def _route(intent: str) -> str:
        return "qa" if intent in {"usage", "concept"} else "diagnose"

    def fallback_to_qa(self, reason: str) -> str:
        """诊断路径发现无可用证据时回退问答。"""
        self.state.intent = "usage"
        return f"未能定位（{reason}），已回退为问答：可以解释相关用法或排查建议。"


__all__ = ["DialogueManager", "DialogueTurn"]
