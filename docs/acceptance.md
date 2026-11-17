# 集成验收报告

对应 tasks.md 第 16 组六项，全部有可复跑的测试或脚本。

## 16.1 三条链路端到端

`tests/test_e2e.py`：

| 链路 | 结果 | 产物 |
|---|---|---|
| ask | 退出码 0 | 结论 + 引用（`--json` 可读） |
| diagnose | 退出码 0 | `artifacts/reports/<id>.md` 与 `.json` |
| patch | 应用/回滚退出码 0 | 补丁工件 + 工作区恢复原状 |
| patch generate 无密钥 | 退出码 2 | 明确的配置错误，不伪造补丁 |

## 16.2 性能与成本

`scripts/measure_acceptance.py`：

| 项 | 实测 | 目标 |
|---|---|---|
| 问答 p95 | **674 ms** | ≤ 10 s |
| 诊断 p95 | **746 ms** | ≤ 120 s |
| 模型成本 | 0 元（无模型调用） | 预算内 |

真实模型成本估算见 [perf.md](perf.md)，未在有密钥环境实测，不虚报。

## 16.3 安全

`tests/test_security_acceptance.py`：脱敏、只读仓库、沙箱降级、注入防护四项全部通过，详见 [security.md](security.md)。

## 16.4 恢复演练

`tests/test_integration_ops.py::test_resume_skips_completed_stages`：

- 中断后 `resume` 返回 `next_stage=slice`；
- `completed=[normalize, triage]`，已完成阶段不再执行；
- token 计量 400（2 阶段 ×2 轮），**没有重复计费**。

## 16.5 权限与并发

- viewer 执行 `index build` → 退出码 6；
- developer 执行 `patch apply` → 退出码 6；
- admin 执行 `index build` → 0；
- 审计记录包含 `user_id`，可追溯到人；
- 两个线程同时构建同一索引：一个成功、一个 `ResourceBusy`，最终索引可正常打开、文件数正确。

## 16.6 多 Agent 对比

- 用法类 + 证据充分 → 单 Agent（短路生效）；
- 同一问题走多 Agent 时，单 Agent 的证据集合是多 Agent 的子集；
- 汇合去重生效（`duplicates ≥ 1`）；
- 单分支失败时整体标记 `DEGRADED`，但定位结论（E2）仍保留。
