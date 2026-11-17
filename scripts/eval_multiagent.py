"""多 Agent 编排评测：派发准确率、贡献度、并行加速比与去重率。

用法：python scripts/eval_multiagent.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pmagent.contracts import IntentDecision, SubAgentResult, TaskBrief
from pmagent.orchestrator import Coordinator, SubAgentRuntime

ROUTING_CASES = [
    ("usage", "knowledge"),
    ("concept", "knowledge"),
    ("defect", "diagnosis"),
    ("localize", "diagnosis"),
    ("usage", "knowledge"),
    ("usage", "knowledge"),
    ("concept", "knowledge"),
    ("defect", "diagnosis"),
    ("localize", "diagnosis"),
    ("defect", "diagnosis"),
    ("usage", "knowledge"),
    ("concept", "knowledge"),
]


def main() -> int:
    coordinator = Coordinator(SubAgentRuntime())
    correct = sum(
        1
        for intent, expected in ROUTING_CASES
        if coordinator.route(IntentDecision(primary=intent)) == expected  # type: ignore[arg-type]
    )

    runtime = SubAgentRuntime()

    def retrieval(brief: TaskBrief) -> SubAgentResult:
        time.sleep(0.1)
        scope = brief.intent.get("scope")
        return SubAgentResult(
            task_id=brief.task_id,
            agent_role="retrieval",
            evidence_ids=[f"E-{scope}-1", f"E-{scope}-2"],
            findings=[{"location": f"{scope}.cc", "score": 0.5}],
        )

    def diagnosis(brief: TaskBrief) -> SubAgentResult:
        return SubAgentResult(
            task_id=brief.task_id,
            agent_role="diagnosis",
            evidence_ids=["E-code-1"],
            confidence=0.7,
        )

    runtime.register("retrieval", retrieval)
    runtime.register("diagnosis", diagnosis)
    result = Coordinator(runtime).dispatch(
        IntentDecision(primary="defect"), "tile seam", problem_ref="run1"
    )

    report = {
        "routing_cases": len(ROUTING_CASES),
        "dispatch_accuracy": round(correct / len(ROUTING_CASES), 3),
        "branches": result.metrics.branches,
        "short_circuited": result.metrics.short_circuited,
        "per_agent_evidence": result.metrics.per_agent_evidence,
        "unique_evidence": len(result.evidence_ids),
        "duplicates": result.metrics.duplicates,
        "duplicate_rate": round(
            result.metrics.duplicates
            / max(1, result.metrics.duplicates + len(result.evidence_ids)),
            3,
        ),
        "serial_ms": result.metrics.serial_ms,
        "parallel_ms": result.metrics.parallel_ms,
        "speedup": result.metrics.speedup,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    Path("docs/eval-multiagent.md").write_text(
        "\n".join(
            [
                "# 多 Agent 编排基线",
                "",
                f"- 派发用例：{len(ROUTING_CASES)}，准确率 **{report['dispatch_accuracy']}**",
                f"- 并行分支：{report['branches']}（含 4 路检索 + 1 路定位）",
                f"- 并行加速比：**{report['speedup']}×**"
                f"（串行合计 {report['serial_ms']} ms / 实际 {report['parallel_ms']} ms）",
                f"- 重复证据率：{report['duplicate_rate']}"
                f"（去重后 {report['unique_evidence']} 条）",
                f"- 各 Agent 证据贡献：{report['per_agent_evidence']}",
                "",
                "## 说明",
                "",
                "- 分支延迟用固定 100ms 模拟，仅验证并行结构，不代表真实检索耗时。",
                "- 派发用例是按规则设计的，准确率反映路由分支覆盖，不是真实用户分布。",
                "- 冲突裁决与失败隔离由单元测试覆盖，不在本脚本重复测量。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
