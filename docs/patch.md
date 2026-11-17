# 补丁建议

## 链路

```
定位报告 → 上下文收集 → 生成候选 diff → 约束校验 → 静态检查 → 影响面 → 自审 → 工件
```

| 步骤 | 内容 |
|---|---|
| 上下文收集 | 目标函数体、调用方、被调方、同文件风格样本、相关测试、近期提交 |
| 生成 | 模型按"单文件、不改公共头、保持风格、不重排"约束生成 1–3 个候选 |
| 约束校验 | 单文件、≤150 行、不改 `.h/.hpp` 与 `include/`、不越界 |
| 静态检查 | diff 结构 → clang-format → 受影响编译单元编译；缺工具逐级标注 `skipped` |
| 影响面 | 调用方数量、GL/Vulkan 后端关键字、线程原语、渲染热路径 |
| 自审 | 改动面、测试需求、调用方回归；输分数与 `accept/review` 建议 |

## 安全边界

- 默认**只写工件**到 `artifacts/patches/<run_id>/`，不动业务仓库；
- `patch apply` 必须加 `--yes`，应用前自动把当前工作区 diff 存到 `.pmagent/patch-snapshots/`；
- `patch revert` 反向应用补丁，失败时提示用快照人工恢复。

## CLI

```bash
pmagent patch generate --report artifacts/reports/<id>.json --repo . --count 2
pmagent patch list --run-id <id>
pmagent patch apply <run_id> <patch_id> --repo . --yes
pmagent patch revert <run_id> <patch_id> --repo .
```

## 已知限制

- 本机没有 `clang-format`、`compile_commands.json` 与 C++ 编译器，格式与编译检查均降级为 `skipped` 并在报告中注明，**不会声称编译通过**。
- 补丁内容依赖模型质量；不接模型时 `patch generate` 直接以配置错误退出（退出码 2）。
- 自审是规则评分（改动面/测试/调用方），不是模型评审；模型评审可在后续版本接入。
