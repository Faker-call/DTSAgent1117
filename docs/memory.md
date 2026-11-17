# 记忆与恢复

## 四层记忆

| 层 | 载体 | 内容 | 保留 |
|---|---|---|---|
| L0 工作记忆 | 进程内 `WorkingMemory` | 当前阶段临时结构 | 进程结束释放 |
| L1 会话记忆 | `sessions` 表 | 槽位（版本/平台）、意图、已排除假设、轮次摘要 | 90 天未访问清理 |
| L2 任务记忆 | `runs` 表 + `.pmagent/runs/<id>/` | 运行状态、事件流、checkpoint | 30 天 |
| L3 长期记忆 | `cases` 表 | 人工确认的案例（问题/根因/修复） | 180 天 TTL，可手动失效 |

默认库位置：`.pmagent/memory.db`（可用 `PMAGENT_MEMORY_DB` 覆盖）。

## 证据账本

上下文里只放索引卡片（`[E1] code src/a.cc:10-20 score=0.80 channels=bm25 :: BuildStrip`），
正文通过 `rehydrate` 取回。压缩只改变"哪些卡片在窗口内"，**不修改账本**，因此压缩后引用仍可回溯。

## 压缩瀑布

| 级别 | 动作 | 事实覆盖要求 |
|---|---|---|
| C1 | 结构化抽取（保留前三行 + 省略行数） | ≥ 0.8 |
| C2 | 按 score 收缩到一半 | ≥ 0.8 |
| C3 | 首行摘要 | ≥ 0.8 |
| C4 | 空输出，标记 `degraded` | — |

每级压缩后抽取事实（错误码、`::` 符号、版本号、时间戳），覆盖率低于 0.8 即回退上一级。

## 中断恢复

- 每个阶段 `append` 事件到 `events.jsonl`，并原子写 `state.json`（临时文件 + `os.replace`）。
- `RunEventLog.resume(stages)` 依据 `stage_finished` 事件计算已完成阶段，返回下一个待执行阶段。
- `interrupted()` 判断最后一条事件是否为中断。
- 索引版本不一致时 `pmagent runs show` 会提示历史定位可能失效，并给 `--from-stage` 重跑建议。

## 案例回流

```
四类信号（patch_merged / ticket_closed / repro_gone / review_confirmed）
   → 五元组候选（症状/证据/根因/修复/验证）
   → 按 症状 + 根因位置 指纹去重
   → 人工确认（可选留出集门禁）
   → L3 案例 → 三路复用：检索先验 / few-shot 示例 / 评测种子
   → 失效（TTL/行号漂移）、复发降权、被拒补丁入反例库
```

先验只返回位置键与权重，**不作为证据类别**；`pmagent review --wrong` 会把结论标记错误并沉淀反例。
