# 多 Agent 编排

## 拓扑

星型：`Coordinator`（代码）向领域子 Agent 派发 `TaskBrief`，子 Agent 之间不通信。

| 角色 | 允许工具 | 截止 | 预算 |
|---|---|---|---|
| knowledge | 检索/文档/切片 | 30s | 40k |
| diagnosis | 检索/切片/git/符号化 | 90s | 120k |
| retrieval | 检索与重排（只读） | 40s | 50k |
| patch | 切片/`git.diff` | 60s | 80k |
| critic | 只读证据 | 20s | 20k |

白名单与 `authz/policy.AGENT_TOOLS` 共用一份，越权提案在运行时直接判 `failed`。

## 派发

```
usage/concept + 证据充分 → 单 Agent（短路）
usage/concept            → 检索 → 知识 Agent
defect/localize          → 4 路检索并行 + 定位 Agent
```

短路的意义：简单用法问题不走多 Agent，避免多花 token 与延迟。

## 并行扇出

- 线程池并发（最多 4 路），共享整体截止时间；
- 单路超时或抛错只影响该路，结果为 `failed` 并标注原因，其余照常汇合；
- 汇合时按证据编号去重，重复计入 `duplicates`；
- 同一位置出现不同结论时进入 `conflicts`，由 Critic 复核。

## 证据账本

所有检索结果登记到共享 `EvidenceLedger`，编号全局唯一（`E1`、`E2`…），因此并行分支不会产生编号冲突；正文按需回取。

## 检索 Agent

只做"查询规划 + 多轮补检"，**不产出结论**：

1. 首轮检索；
2. 命中不足 `min_hits` 时用"原查询 + 范围"补检一轮；
3. 仍不足返回 `degraded(insufficient)`，由上游决定是否澄清。

## 评审 Agent

触发条件：置信度落在 0.6–0.75 灰区、存在冲突、或改动跨模块。只读证据、不改结论；无模型时用关键词覆盖做确定性评审，输出 `support/challenge` 与缺失证据。

## 实测

| 指标 | 数值 |
|---|---|
| 派发准确率（12 条） | 1.000 |
| 并行加速比 | **4.0×**（串行 376 ms / 实际 94 ms） |
| 重复证据率 | 0.111 |
| 证据贡献 | retrieval 8 / diagnosis 1 |

加速比用固定 100ms 模拟分支延迟，只验证并行结构，不代表真实检索耗时。
