"""意图识别：结构化特征 + 三态规则 + 否决项 + 模型仲裁 + 分级兜底。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pmagent.contracts import IntentDecision

STACK_FRAME = re.compile(r"#\d+\s+0x[0-9a-fA-F]+")
ERROR_CODE = re.compile(r"\b[A-Z]{1,6}[-_]?\d{2,6}\b")
VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
CODE_FENCE = re.compile(r"```")
HOW_WORD = re.compile(r"(如何|怎么|怎样|求示例|示例|用法|参数|接口)")
WHY_WORD = re.compile(r"(为什么|为何|根因|原因)")
CONCEPT_WORD = re.compile(r"(是什么|区别|原理|概念|介绍)")
LOCATE_WORD = re.compile(r"(定位|哪一行|哪个函数|根因)")
FILE_LINE = re.compile(r"[\w./\\-]+\.(?:cc|cpp|h|hpp|mm):\d+")
CRASH_WORD = re.compile(r"(崩溃|闪退|crash|segfault|coredump)", re.IGNORECASE)
VISUAL_WORD = re.compile(r"(黑屏|发黑|变黑|黑的|空白|错位|闪烁|缝隙|裂缝|色带|花屏)")
PERF_WORD = re.compile(r"(掉帧|卡顿|fps|内存涨|内存增长|加载慢)", re.IGNORECASE)
ERROR_WORD = re.compile(r"(报错|错误|异常|失败|fatal)", re.IGNORECASE)

DEFECT_THRESHOLD = 0.75
QA_THRESHOLD = 0.5


@dataclass
class Features:
    has_stack: bool = False
    has_log: bool = False
    has_screenshot: bool = False
    has_repro: bool = False
    has_error_code: bool = False
    has_version: bool = False
    has_code_block: bool = False
    has_file_location: bool = False
    how_words: list[str] = field(default_factory=list)
    why_words: list[str] = field(default_factory=list)
    concept_words: list[str] = field(default_factory=list)
    locate_words: list[str] = field(default_factory=list)

    @property
    def attachments(self) -> int:
        return sum(
            [self.has_stack, self.has_log, self.has_screenshot, self.has_repro]
        )


def extract_features(
    text: str,
    *,
    logs: int = 0,
    stacks: int = 0,
    shots: int = 0,
    repro: bool = False,
) -> Features:
    return Features(
        has_stack=stacks > 0 or bool(STACK_FRAME.search(text)),
        has_log=logs > 0,
        has_screenshot=shots > 0,
        has_repro=repro,
        has_error_code=bool(ERROR_CODE.search(text)),
        has_version=bool(VERSION.search(text)),
        has_code_block=bool(CODE_FENCE.search(text)),
        has_file_location=bool(FILE_LINE.search(text)),
        how_words=HOW_WORD.findall(text),
        why_words=WHY_WORD.findall(text),
        concept_words=CONCEPT_WORD.findall(text),
        locate_words=LOCATE_WORD.findall(text),
    )


@dataclass
class RuleOutcome:
    state: str  # decided | conflict | abstain
    intent: str | None = None
    confidence: float = 0.0
    matched: list[str] = field(default_factory=list)
    vetoed: list[str] = field(default_factory=list)
    reason: str = ""


class IntentRules:
    """规则集版本化，命中计数用于定期回测。"""

    version = "1"

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}

    def _hit(self, rule_id: str) -> None:
        self.hits[rule_id] = self.hits.get(rule_id, 0) + 1

    # 否决规则：求解释的疑问词作用于"故障对象本身"
    def veto(self, features: Features, text: str) -> list[str]:
        vetoed: list[str] = []
        if re.search(r"(怎么|如何|怎样)(查看|看|读|获取|打开).*(日志|崩溃|堆栈)", text):
            vetoed.append("V-ASK-HOW-TO-READ")
            self._hit("V-ASK-HOW-TO-READ")
        if re.search(r"(怎么|如何)(使用|调用|设置|配置)", text) and not features.attachments:
            vetoed.append("V-USAGE-WITHOUT-EVIDENCE")
            self._hit("V-USAGE-WITHOUT-EVIDENCE")
        return vetoed

    def evaluate(self, features: Features, text: str) -> RuleOutcome:
        vetoed = self.veto(features, text)
        defect_rules: list[str] = []
        if features.has_stack:
            defect_rules.append("R-STACK")
        if features.has_log and features.has_error_code:
            defect_rules.append("R-LOG-ERROR")
        if features.has_screenshot:
            defect_rules.append("R-SCREENSHOT")
        if features.has_repro:
            defect_rules.append("R-REPRO")
        if CRASH_WORD.search(text):
            defect_rules.append("R-TEXT-CRASH")
        if VISUAL_WORD.search(text):
            defect_rules.append("R-TEXT-VISUAL")
        if PERF_WORD.search(text):
            defect_rules.append("R-TEXT-PERF")
        if ERROR_WORD.search(text) or features.has_error_code:
            defect_rules.append("R-TEXT-ERROR")
        for rule_id in defect_rules:
            self._hit(rule_id)

        usage_rules: list[str] = []
        if features.how_words and not features.attachments:
            usage_rules.append("R-HOW-WORD")
            self._hit("R-HOW-WORD")
        if (features.concept_words and not features.attachments) or (
            features.why_words and not defect_rules
        ):
            usage_rules.append("R-CONCEPT-WORD")
            self._hit("R-CONCEPT-WORD")

        locate = bool(features.locate_words and (features.has_file_location or features.has_stack))
        if locate:
            self._hit("R-LOCATE")
            return RuleOutcome(
                state="decided",
                intent="localize",
                confidence=0.95,
                matched=["R-LOCATE"],
                vetoed=vetoed,
            )

        # 明确询问"如何查看日志/堆栈"且无附件：这是求解释，不是报故障
        if "V-ASK-HOW-TO-READ" in vetoed and not features.attachments:
            return RuleOutcome(
                state="decided",
                intent="usage",
                confidence=0.85,
                matched=["V-ASK-HOW-TO-READ"],
                vetoed=vetoed,
            )

        if defect_rules and not vetoed:
            return RuleOutcome(
                state="decided",
                intent="defect",
                confidence=0.9,
                matched=defect_rules,
                vetoed=vetoed,
            )
        if defect_rules and vetoed:
            return RuleOutcome(
                state="conflict",
                intent="defect",
                confidence=0.5,
                matched=defect_rules,
                vetoed=vetoed,
                reason="存在故障证据但求解释的疑问词否决了缺陷判定",
            )
        if usage_rules:
            intent = "usage" if "R-HOW-WORD" in usage_rules else "concept"
            return RuleOutcome(
                state="decided",
                intent=intent,
                confidence=0.85,
                matched=usage_rules,
                vetoed=vetoed,
            )
        return RuleOutcome(state="abstain", matched=[], vetoed=vetoed, reason="无规则命中")


@dataclass
class ClassificationResult:
    decision: IntentDecision
    features: Features
    outcome: RuleOutcome


class IntentClassifier:
    def __init__(
        self,
        *,
        gateway: Any | None = None,
        rules: IntentRules | None = None,
        interactive: bool = True,
    ) -> None:
        self.gateway = gateway
        self.rules = rules or IntentRules()
        self.interactive = interactive

    def classify(
        self,
        text: str,
        *,
        logs: int = 0,
        stacks: int = 0,
        shots: int = 0,
        repro: bool = False,
    ) -> ClassificationResult:
        features = extract_features(
            text, logs=logs, stacks=stacks, shots=shots, repro=repro
        )
        outcome = self.rules.evaluate(features, text)

        if outcome.state == "decided":
            decision = IntentDecision(
                primary=outcome.intent,  # type: ignore[arg-type]
                secondary=[],
                confidence=outcome.confidence,
                channel="rule",
                matched_rule_ids=outcome.matched,
                evidence_gate={
                    "passed": features.attachments > 0,
                    "missing": [] if features.attachments else ["日志/堆栈/截图"],
                },
            )
            return ClassificationResult(decision, features, outcome)

        arbitration = self._arbitrate(text, features, outcome)
        if arbitration is not None:
            return ClassificationResult(arbitration, features, outcome)

        return ClassificationResult(self._fallback(text, features), features, outcome)

    # -- 模型仲裁 ---------------------------------------------------------
    def _arbitrate(
        self, text: str, features: Features, outcome: RuleOutcome
    ) -> IntentDecision | None:
        if self.gateway is None:
            return None
        try:
            from pmagent.gateway import complete_json

            result = complete_json(
                self.gateway,
                [
                    {
                        "role": "system",
                        "content": (
                            "判断用户意图：usage（怎么用）、concept（概念）、"
                            "defect（报故障）、localize（要求定位）。"
                            "只输出 JSON：{primary, secondary, confidence}。"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                IntentDecision,
                role="router",
            )
        except Exception:  # noqa: BLE001 - 模型不可用时走兜底
            return None

        decision = result.value
        if decision.primary in {"defect", "localize"} and decision.confidence < DEFECT_THRESHOLD:
            return self._clarify(decision.primary, decision.confidence, channel="model")
        decision.channel = "model"
        decision.matched_rule_ids = list(outcome.matched)
        if outcome.state == "conflict":
            decision.note = "规则冲突，由模型仲裁"
        return decision

    # -- 兜底 -------------------------------------------------------------
    def _fallback(self, text: str, features: Features) -> IntentDecision:
        if not self.interactive:
            return IntentDecision(
                primary="usage",
                confidence=0.3,
                channel="fallback",
                evidence_gate={"passed": features.attachments > 0, "missing": []},
                note="意图未确认（非交互模式，默认问答）",
            )
        question = "你希望我解释怎么使用，还是排查一个具体故障？"
        return IntentDecision(
            primary="usage",
            confidence=0.3,
            channel="fallback",
            evidence_gate={"passed": features.attachments > 0, "missing": []},
            clarification=question,
            note="意图不明确，先澄清",
        )

    @staticmethod
    def _clarify(intent: str, confidence: float, *, channel: str) -> IntentDecision:
        return IntentDecision(
            primary=intent,  # type: ignore[arg-type]
            confidence=confidence,
            channel=channel,  # type: ignore[arg-type]
            clarification="可以补充日志、堆栈或复现步骤吗？",
            note="置信度不足，先澄清",
        )


__all__ = [
    "ClassificationResult",
    "DEFECT_THRESHOLD",
    "Features",
    "IntentClassifier",
    "IntentRules",
    "QA_THRESHOLD",
    "RuleOutcome",
    "extract_features",
]
