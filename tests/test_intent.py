from __future__ import annotations

from pmagent.agents import IntentClassifier, IntentRules, extract_features
from pmagent.contracts import IntentDecision
from pmagent.gateway import LLMResponse

STACK_TEXT = "#0 0x0000000000a1b2c3 pm::BuildStrip at /src/tess.cc:42"


class FakeGateway:
    def __init__(self, decision: IntentDecision) -> None:
        self.decision = decision
        self.calls = 0

    def complete(self, messages, **kwargs):  # noqa: ANN001
        self.calls += 1
        return LLMResponse(text=self.decision.model_dump_json(), model="fake")


def test_extract_features() -> None:
    features = extract_features(
        f"崩溃了 {STACK_TEXT} error E1024 版本 6.0.1",
        logs=1,
    )
    assert features.has_stack and features.has_log
    assert features.has_error_code and features.has_version


def test_rule_decides_defect_with_stack() -> None:
    result = IntentClassifier(interactive=True).classify(f"崩溃 {STACK_TEXT}", stacks=1)
    assert result.decision.primary == "defect"
    assert result.decision.channel == "rule"
    assert "R-STACK" in result.decision.matched_rule_ids


def test_rule_decides_usage() -> None:
    result = IntentClassifier().classify("如何设置自定义地图样式")
    assert result.decision.primary == "usage"
    assert result.decision.channel == "rule"


def test_rule_decides_concept() -> None:
    result = IntentClassifier().classify("矢量瓦片是什么，和栅格的区别")
    assert result.decision.primary == "concept"


def test_veto_how_to_read_crash_log() -> None:
    result = IntentClassifier().classify("怎么查看崩溃日志")
    assert result.decision.primary == "usage"
    assert result.outcome.state == "decided"


def test_conflict_when_evidence_and_veto() -> None:
    text = f"怎么查看崩溃日志 {STACK_TEXT}"
    result = IntentClassifier(interactive=False).classify(text, stacks=1)
    assert result.outcome.state == "conflict"
    # 无模型可用时走兜底
    assert result.decision.channel == "fallback"


def test_abstain_clarifies_in_interactive_mode() -> None:
    result = IntentClassifier(interactive=True).classify("地图有点问题")
    assert result.decision.channel == "fallback"
    assert result.decision.clarification


def test_abstain_degrades_in_non_interactive_mode() -> None:
    result = IntentClassifier(interactive=False).classify("地图有点问题")
    assert result.decision.primary == "usage"
    assert result.decision.note and "非交互" in result.decision.note


def test_model_arbitration_used_on_conflict() -> None:
    gateway = FakeGateway(IntentDecision(primary="defect", confidence=0.9))
    result = IntentClassifier(gateway=gateway, interactive=True).classify(
        f"怎么查看崩溃日志 {STACK_TEXT}", stacks=1
    )
    assert gateway.calls == 1
    assert result.decision.channel == "model"
    assert result.decision.primary == "defect"


def test_model_low_confidence_clarifies() -> None:
    gateway = FakeGateway(IntentDecision(primary="defect", confidence=0.4))
    result = IntentClassifier(gateway=gateway).classify("这个现象有点奇怪")
    assert gateway.calls == 1
    assert result.decision.clarification
    assert result.decision.note and "置信度" in result.decision.note


def test_rule_stats_recorded() -> None:
    rules = IntentRules()
    IntentClassifier(rules=rules).classify(f"崩溃 {STACK_TEXT}", stacks=1)
    assert rules.hits.get("R-STACK") == 1
    assert rules.version == "1"
