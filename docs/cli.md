# 命令行

## 全局选项

| 选项 | 说明 |
|---|---|
| `--help` | 显示帮助 |
| `--version` | 显示版本号 |

## 命令

| 命令 | 作用 | 状态 |
|---|---|---|
| `pmagent config init [--path P] [--force]` | 生成默认配置文件 | 已实现 |
| `pmagent config show [--path P] [--json]` | 显示生效配置（密钥只显示是否设置） | 已实现 |
| `pmagent auth init/login/logout/whoami` | 初始化管理员、登录、退出、查看身份 | 已实现 |
| `pmagent auth user add/role/disable` | 用户管理（仅管理员） | 已实现 |
| `pmagent index status` | 查看索引状态（登录即可） | 占位 |
| `pmagent index build` | 构建索引（维护者及以上） | 占位 |
| `pmagent ask` | 意图识别 → 检索 → 引用校验（需 `--corpus` 或 `PMAGENT_CORPUS`） | 已实现 |
| `pmagent diagnose` | 证据归一化 → 四源候选 → 定位报告（需已构建索引） | 已实现 |
| `pmagent runs show/list` | 运行记录与恢复提示 | 已实现 |
| `pmagent review --wrong/--correct` | 结论纠正与反例沉淀 | 已实现 |
| `pmagent memory list/forget` | 长期案例查看与下架 | 已实现 |
| `pmagent patch generate/list/apply/revert` | 补丁生成、查看、应用（需 `--yes`）与回滚 | 已实现 |
| `pmagent explain` | 解释符号或代码位置 | 占位 |
| `pmagent eval run/compare/calibrate/agreement/review/replay` | 离线评测、对比、校准、标注一致性、人工评审与回放 | 已实现 |

命令与权限的对应关系见 [rbac.md](rbac.md)。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 内部错误 |
| 2 | 参数或配置错误 |
| 3 | 证据不足 |
| 4 | 定位失败 |
| 5 | 预算或超时终止 |
| 6 | 权限不足或未登录 |
| 130 | 用户中断（已写 checkpoint，可续跑） |

## 配置优先级

命令行覆盖 > 环境变量（`PMAGENT_*`，嵌套用 `__` 分隔）> 配置文件（默认 `~/.pmagent/config.toml`）> 内置默认值。

示例：`PMAGENT_MODEL__CHAT_MODEL=deepseek-v4-pro pmagent ask "..."`

## 密钥

密钥不写入配置文件，只从环境变量读取：

| 变量 | 用途 |
|---|---|
| `DEEPSEEK_API_KEY` | 对话与视觉模型 |
| `QWEN3.7` | 向量化模型 |
