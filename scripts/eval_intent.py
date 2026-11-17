"""意图识别评测：路由准确率、兜底率与通道分布。

用法：python scripts/eval_intent.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pmagent.agents import IntentClassifier

STACK = "#0 0x0000000000a1b2c3 pm::render::BuildStrip at /src/tess.cc:42"

CASES: list[tuple[str, str, bool]] = [
    ("如何设置自定义地图样式", "usage", False),
    ("怎么调用 setStyle 重置样式", "usage", False),
    ("怎样添加一个线图层", "usage", False),
    ("求一个加载离线瓦片的示例", "usage", False),
    ("setCamera 的参数怎么配置", "usage", False),
    ("如何查看当前缩放级别", "usage", False),
    ("矢量瓦片是什么", "concept", False),
    ("栅格和矢量瓦片的区别", "concept", False),
    ("地图渲染引擎的工作原理", "concept", False),
    ("样式版本的概念介绍", "concept", False),
    ("瓦片金字塔是什么", "concept", False),
    ("为什么需要 LOD", "concept", False),
    (f"崩溃了 {STACK}", "defect", True),
    (f"启动时报错 {STACK}", "defect", True),
    (f"缩放后进程退出 {STACK}", "defect", True),
    (f"偶发闪退 {STACK}", "defect", True),
    (f"黑屏并崩溃 {STACK}", "defect", True),
    (f"内存涨到 4G 后崩溃 {STACK}", "defect", True),
    ("error E1024 解码失败", "defect", False),
    ("日志里出现 FATAL 渲染线程崩溃", "defect", False),
    ("瓦片请求 404 并报错", "defect", False),
    ("地图空白没有报错", "defect", False),
    ("标注错位很严重", "defect", False),
    ("帧率掉到 10fps", "defect", False),
    # 反例：求解释的疑问词作用于故障对象本身，不应判缺陷
    ("怎么查看崩溃日志", "usage", False),
    ("如何读取堆栈信息", "usage", False),
    ("怎么设置日志级别", "usage", False),
    # 意图模糊：非交互模式退化为用法并标注未确认
    ("地图有点问题", "usage", False),
    ("为什么地图是黑的", "defect", False),
]


def main() -> int:
    classifier = IntentClassifier(interactive=False)
    correct = 0
    channels: Counter[str] = Counter()
    mismatches: list[dict] = []
    fallbacks = 0

    for text, expected, has_stack in CASES:
        result = classifier.classify(text, stacks=1 if has_stack else 0)
        decision = result.decision
        channels[decision.channel] += 1
        if decision.channel == "fallback":
            fallbacks += 1
        if decision.primary == expected:
            correct += 1
        else:
            mismatches.append(
                {"text": text[:40], "expected": expected, "actual": decision.primary}
            )

    report = {
        "cases": len(CASES),
        "accuracy": round(correct / len(CASES), 3),
        "fallback_rate": round(fallbacks / len(CASES), 3),
        "channels": dict(channels),
        "mismatches": mismatches[:5],
        "rule_version": classifier.rules.version,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    Path("docs/eval-intent.md").write_text(
        "\n".join(
            [
                "# 意图识别基线",
                "",
                f"- 用例数：{len(CASES)}",
                f"- 规则集版本：{classifier.rules.version}",
                f"- 准确率：**{report['accuracy']}**",
                f"- 兜底率：{report['fallback_rate']}",
                f"- 通道分布：{report['channels']}",
                "",
                "## 说明",
                "",
                "- 纯规则通道（无模型、无网络）下的结果；接入模型仲裁后应重新基线。",
                "- **重要**：规则与用例是同步编写的，准确率反映的是规则对这些模式的覆盖度，",
                "  不代表真实用户分布的泛化能力；真实基线需要独立标注集。",
                "- 错例："
                + (json.dumps(mismatches[:5], ensure_ascii=False) if mismatches else "无"),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
