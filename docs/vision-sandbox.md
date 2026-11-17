# 视觉与沙箱

## 截图规则检测

`VisualAnalyzer` 只用 Pillow 与纯 Python，无外部模型也能出结论：

| 缺陷 | 判据 |
|---|---|
| 纯色块 `solid` | 网格单元标准差 ≤ 3，且连通区域面积占 2%–40% |
| 缝隙 `seam` | 相邻列/行均值的 RGB 差 ≥ 30，且两侧列/行自身标准差 ≤ 8（排除噪点） |
| 色带 `band` | 水平方向出现 ≥3 条均匀行边界，取首尾包成区域 |
| 错位 `misalign` | 两条相邻缝隙间距 ≤ 6 px |

实测（构造样本）：4 类缺陷全部检出；渐变图与随机噪声图**误检为 0**。

## 多模态描述与冲突标注

`ModelDescriber` 把图片 base64 后交给 `deepseek-flash` 图像输入，返回 `{description, kinds, regions}`。

- 规则结果与模型结果**并列保留**，各自带 `source` 字段；
- 重叠但类型不同的区域标注为 `conflict:rule=X,model=Y`，不互相覆盖；
- 模型调用失败时静默回退到规则结果，不中断流程。

## 复现沙箱

| 模式 | 条件 | 隔离 |
|---|---|---|
| 容器 | 检测到 `docker` | 非 root、`--network=none`、`--memory`/`--cpus` 配额、仅挂载工程目录 |
| 本地进程 | 无 docker（当前环境） | 独立工作目录、环境变量白名单、超时；**无网络与内存硬隔离** |

复现配置 `pmagent.yaml`：

```yaml
build: cmake --build build -j
run: ./build/repro
timeout: 120
env:
  MLN_LOG_LEVEL: debug
```

未提供 `build`/`run` 时状态为 `blocked`；构建失败立即短路，不会继续执行 run。

## 实测

| 项 | 结果 |
|---|---|
| 5 个复现工程可执行率 | **0.8**（3 成功 + 1 失败退出码 + 1 超时） |
| 超时处理 | `exit_code=None`，状态 `timeout`，不阻塞调用方 |
| 环境差异 | 结果中记录宿主平台、`docker_available`、`network_allowed` |

## 已知限制

- 本机没有 Docker，容器路径**未做端到端实测**，仅实现并保留代码路径。
- 进程模式无法真正禁网，报告中会明确写"无网络隔离"警告。
- 错位检测是启发式（双缝间距），对复杂错位场景可能漏检。
