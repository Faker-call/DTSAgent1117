"""注入对抗集：文档/工单/日志/对话中埋指令，验证不触发工具调用。"""

from __future__ import annotations

from pathlib import Path

from pmagent.eval import category_distribution, load_cases, missing_summary
from pmagent.safety import scan, tool_calls_in, wrap

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES = REPO_ROOT / "data/eval/injection_cases.jsonl"


def test_adversarial_set_shape() -> None:
    cases = load_cases(CASES)
    assert len(cases) >= 20
    assert set(category_distribution(cases)) == {"security"}
    assert all(case.expected_behavior == "no_tool_call" for case in cases)
    assert missing_summary(cases) == {}


def test_adversarial_content_never_triggers_tools() -> None:
    for case in load_cases(CASES):
        assert tool_calls_in(case.query) == []
        wrap(case.query)


def test_chat_vectors_are_detected() -> None:
    detected = 0
    for case in load_cases(CASES):
        if case.id.startswith("INJ-CHAT"):
            result = scan(case.query)
            assert result.detected, case.id
            detected += 1
    assert detected >= 3


def test_regression_set_contains_security_cases() -> None:
    seed = load_cases(REPO_ROOT / "data/eval/seed.jsonl")
    security = [case for case in seed if case.category == "security"]
    assert len(security) >= 20
