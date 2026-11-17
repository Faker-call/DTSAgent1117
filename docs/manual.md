# 使用手册

## 1. 安装与初始化

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
pmagent --version
pmagent config init
pmagent auth init --username admin --password <密码>
pmagent auth login --username admin --password <密码>
```

配置优先级：CLI 参数 > 环境变量 > 项目配置 > 用户配置。密钥只从环境变量读取：
`DEEPSEEK_API_KEY`、`QWEN3.7`。

## 2. 索引维护

```bash
pmagent index build --repo corpus/maplibre-native
pmagent index status
pmagent index push --to artifacts/index.tar.zst
pmagent index pull --from artifacts/index.tar.zst
```

- 增量构建会比对 git commit 与文件 mtime；
- `index status` 显示文件数、切块数、embedding 是否齐全；
- 索引损坏时 `CodeIndex.open` 报"索引快照缺失"，重新 build 即可。

## 3. 问答

```bash
pmagent ask "如何设置自定义地图样式" --corpus docs.db --sdk-version 6.0.1
pmagent ask "..." --corpus docs.db --offline     # 断网：只用规则与检索
pmagent ask "..." --session s1 --non-interactive # CI：不阻塞
```

无知识库、证据不足、意图不明分别以退出码 3 结束并给出下一步提示。

## 4. 诊断

```bash
pmagent diagnose "放大后路网断裂" \
  --log engine.log --stack crash.txt --version 6.0.1 \
  --repo corpus/maplibre-native --index-root corpus/.index

# 并行检索分支后再收敛（分支数、加速比、去重数会打印）
pmagent diagnose "..." --multi-agent --repo ... --index-root ...

# 中断后复用已保存证据上下文
pmagent diagnose --resume <run_id> --repo ... --index-root ...

# 解释符号或文件行号
pmagent explain pm::Painter::drawFill --index-root corpus/.index
```

产物：`artifacts/reports/<id>.md` 与 `.json`（供 `patch generate` 使用）。

## 5. 补丁

```bash
pmagent patch generate --report artifacts/reports/<id>.json --repo . --count 2
pmagent patch list --run-id <id>
pmagent patch apply <run_id> <patch_id> --repo . --yes   # 默认不写仓库
pmagent patch revert <run_id> <patch_id> --repo .
```

应用前自动把工作区 diff 存到 `.pmagent/patch-snapshots/`；生成结束后会打印本次模型成本（元 / tokens），超出 `budget.max_cost_cny` 立即终止。

## 6. 预算与成本

- 单次运行预算：`budget.max_tokens`、`budget.max_wall_seconds`；
- 多 Agent 并行会放大 token 消耗：4 路检索 + 定位 Agent 约 5 份上下文；
- 简单用法问题走短路（单 Agent），只有缺陷排查才扇出；
- 超预算退出码 5，可用 `--resume <run_id>` 复用已保存的问题上下文续跑。

## 7. 记忆清理

```bash
pmagent memory list
pmagent memory forget --case-id <id>     # 下架失效案例
```

L0/L1 会话记忆按 TTL 自动清理；L3 案例默认 180 天过期。

## 8. 评测与回放

```bash
pmagent eval run --dataset data/eval/seed.jsonl --mode retrieval
pmagent eval compare --baseline old.json --current new.json
pmagent eval calibrate --report new.json
pmagent eval replay <run_id>
```

## 9. 故障排查

| 现象 | 处理 |
|---|---|
| 退出码 2 | 配置或参数错误；`pmagent config show` 看当前值 |
| 退出码 3 | 证据不足；按提示补充日志/堆栈/版本 |
| 退出码 4 | 定位失败；确认索引已构建、版本一致 |
| 退出码 5 | 预算超限；调大预算或缩小范围 |
| 退出码 6 | 权限不足；按提示联系管理员分配角色 |
| "索引版本不一致" | 重新 `index build`，或 `runs show --from-stage` 重跑 |

## 10. 命令清单

`pmagent --help` 可查看全部命令：`config`、`auth`、`index`、`ask`、`diagnose`、`patch`、`runs`、`memory`、`review`、`eval`、`explain`。
