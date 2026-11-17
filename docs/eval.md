# 评测体系

## 数据集

`data/eval/seed.jsonl`，**202 条**，按类别分层：

| 类别 | 数量 | 来源 |
|---|---|---|
| usage | 66 | 文档标题 + 工单 |
| defect | 38 | 合成工单 |
| localize | 38 | 工单 + "根因在哪个文件"变体 |
| concept | 40 | 文档标题 |
| security | 20 | 注入对抗集 |

标注缺失：**26 条 usage 工单缺少 `expected_answer_contains`**（工单本身没有标准答案），统计见 `scripts/build_eval_set.py` 输出。

## 指标

| 指标 | 定义 |
|---|---|
| Recall@10 | 期望文件出现在前 10 条命中的比例 |
| Top-1 / Top-3 | 期望文件排在首位的比例 / 出现在前 3 的比例 |
| 结论正确性 | 期望答案片段出现在回答中的比例 |
| 虚假高置信率 | 置信度 ≥0.75 但未命中的比例 |
| 无效调用率 | 无命中或抛异常的用例比例 |
| 停滞检出率 | 预留字段，编排循环接入后生效（当前恒 0） |
| 平均有效调用数 | 每条用例的检索/执行次数（当前 1） |
| 时延 | p95（毫秒） |
| 成本 | 人民币；未调用付费模型时为 0 |

## 检索基线（202 条）

`pmagent eval run --dataset data/eval/seed.jsonl --mode retrieval`

| 指标 | 数值 |
|---|---|
| 代码用例 / 文档用例 | 102 / 80 |
| Recall@10 / Top-1 / Top-3 | 0.0 / 0.0 / 0.0 |
| 结论正确性 | 0.0 |
| 虚假高置信率 | 0.0 |
| 无效调用率 | **0.604** |
| p95 时延 | 0 ms（无命中时几乎无耗时） |

**原因**：查询是中文、代码是英文标识符，当前无向量通道；工单的根因文件是合成路径，部分在语料中不存在。这是**下界**，不是架构结论。修复路径：向量化 + 真实工单。

## 命令

```bash
pmagent eval run --dataset data/eval/seed.jsonl --mode retrieval --out artifacts/eval/base.json
pmagent eval compare --baseline artifacts/eval/base.json --current artifacts/eval/new.json
pmagent eval calibrate --report artifacts/eval/base.json
pmagent eval agreement --a a.jsonl --b b.jsonl --threshold 0.6
pmagent eval review --report artifacts/eval/base.json --case-id T-SYN-001 --score 4 --accept
pmagent eval replay <run_id>
```

## 门禁与案例回流

- 双人标注 kappa 低于 0.6 时提示"触发第三人或组长复核"；
- 人工确认的案例经 `admit_case` 进入 L3 案例库；
- 留出集门禁：`holdout_gate` 要求留出集全部命中，否则案例不得进入复用池。
