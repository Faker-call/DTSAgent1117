from __future__ import annotations

from pmagent.agents import ClarificationPolicy, DialogueManager, IntentClassifier
from pmagent.contracts import EvidenceRequest, SessionState

STACK_TEXT = "#0 0x0000000000a1b2c3 pm::BuildStrip at /src/tess.cc:42"


def test_route_qa_for_usage() -> None:
    manager = DialogueManager(SessionState(id="s1"))
    turn = manager.handle("如何设置自定义地图样式")
    assert turn.route == "qa"
    assert manager.state.intent == "usage"


def test_route_diagnose_for_defect_with_evidence() -> None:
    manager = DialogueManager(SessionState(id="s1"))
    turn = manager.handle(f"崩溃 {STACK_TEXT}", stacks=1)
    assert turn.route == "diagnose"


def test_route_clarify_when_defect_without_evidence() -> None:
    manager = DialogueManager(SessionState(id="s1"))
    turn = manager.handle("地图打不开", repro=False)
    assert turn.route in {"qa", "clarify"}


def test_intent_drift_is_recorded_and_evidence_kept() -> None:
    state = SessionState(id="s1")
    state.intent = "usage"
    state.evidence_ids = ["E1", "E2"]
    manager = DialogueManager(state, classifier=IntentClassifier(interactive=True))
    turn = manager.handle(f"崩溃了 {STACK_TEXT}", stacks=1)
    assert turn.drift
    assert state.evidence_ids == ["E1", "E2"]
    assert "切换" in (turn.result.decision.note or "")


def test_fallback_to_qa_switches_intent() -> None:
    state = SessionState(id="s1")
    manager = DialogueManager(state)
    message = manager.fallback_to_qa("没有可诊断证据")
    assert state.intent == "usage"
    assert "回退" in message


def test_clarification_skips_answered_version() -> None:
    state = SessionState(id="s1")
    state.slots.sdk_version = "6.0.1"
    policy = ClarificationPolicy()
    questions = policy.questions(
        state,
        [
            EvidenceRequest(needed="SDK 版本", purpose="过滤版本"),
            EvidenceRequest(needed="平台与渲染后端", purpose="后端差异"),
        ],
    )
    assert len(questions) == 1
    assert "平台" in questions[0]


def test_clarification_max_three() -> None:
    policy = ClarificationPolicy()
    questions = policy.questions(
        SessionState(id="s1"),
        [
            EvidenceRequest(needed="SDK 版本", purpose="a"),
            EvidenceRequest(needed="平台与渲染后端", purpose="b"),
            EvidenceRequest(needed="日志 / 堆栈 / 崩溃转储 / 复现工程 / 截图（任一项）", purpose="c"),
            EvidenceRequest(needed="带文件行号的堆栈或明确的代码位置", purpose="d"),
        ],
    )
    assert len(questions) <= 3
