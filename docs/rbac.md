# 身份与权限

## 角色

| 角色 | 定位 |
|---|---|
| `admin` 管理员 | 全部权限，含用户管理 |
| `maintainer` 维护者 | 可重建索引、确认案例、应用补丁 |
| `developer` 开发者 | 可做定位、生成补丁建议、运行评测与沙箱 |
| `viewer` 观察者 | 只读：问答与索引状态查询 |

## 权限矩阵

| 权限 | admin | maintainer | developer | viewer |
|---|---|---|---|---|
| `index.read` 索引读取 | ✓ | ✓ | ✓ | ✓ |
| `index.build` 索引构建 | ✓ | ✓ | — | — |
| `case.confirm` 案例确认 | ✓ | ✓ | — | — |
| `patch.apply` 补丁应用 | ✓ | ✓ | — | — |
| `patch.suggest` 补丁建议 | ✓ | ✓ | ✓ | — |
| `eval.run` 评测运行 | ✓ | ✓ | ✓ | — |
| `sandbox.run` 沙箱运行 | ✓ | ✓ | ✓ | — |
| `user.manage` 用户管理 | ✓ | — | — | — |
| `secret.own` 自有密钥 | ✓ | ✓ | ✓ | ✓ |
| `query.ask` 问答 | ✓ | ✓ | ✓ | ✓ |
| `diagnose.run` 问题定位 | ✓ | ✓ | ✓ | — |

矩阵定义在 `src/pmagent/authz/permissions.py`，命令与工具都读同一份，避免规则漂移。

## 命令与权限

| 命令 | 所需权限 |
|---|---|
| `pmagent auth init` | 无（仅首次、无管理员时可用） |
| `pmagent auth login/logout/whoami` | 无 / 登录态 |
| `pmagent auth user add/role/disable` | `user.manage` |
| `pmagent index status` | `index.read` |
| `pmagent index build` | `index.build` |

拒绝时退出码为 **6**，并给出缺少的权限与所需角色。

## 登录与自动化

```bash
pmagent auth init --username admin          # 首次初始化，交互输入密码
pmagent auth login --username admin         # 令牌缓存 12 小时
pmagent auth whoami
```

CI 场景不落盘会话文件，直接用环境变量：

```bash
export PMAGENT_TOKEN=<token>
```

令牌有效期 12 小时；连续 5 次密码错误会锁定账号 5 分钟。

## 密钥隔离

密钥只从**当前进程**的环境变量读取（`DEEPSEEK_API_KEY`、`QWEN3.7`）：

- 不写入配置文件；
- `config show` 只显示"已设置 / 未设置"；
- 管理员也没有读取他人密钥的接口。

## 单写者锁

索引构建与案例入库使用文件锁（`.pmagent/locks/*.lock`）：

- 第二个请求会等待并在超时后报告持有者（`user@host (pid)`）；
- 心跳超过 5 分钟视为陈旧，可被接管，避免进程被杀后锁永久占用。

## 可插拔后端

`PolicyEngine` 依赖 `AuthzBackend` 协议，默认实现是本地矩阵 `LocalBackend`。
接入企业身份源时只需替换该实现，命令与工具无需改动。
