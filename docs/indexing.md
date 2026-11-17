# 索引

## 目录布局

```
<index.root>/                    默认 ~/.pmagent/index
├── current                      当前快照指针（commit 前 12 位）
└── <commit12>/
    ├── index.db                 SQLite：files / symbols / refs / includes / chunks / chunks_fts / chunks_vec / commits / index_errors
    └── manifest.json            版本与统计信息
```

## manifest 与版本守卫

| 字段 | 说明 |
|---|---|
| `schema_version` | 索引结构版本 |
| `chunk_policy_version` | 切块策略版本 |
| `embedding_model_id` / `embedding_dim` | 向量化模型与维度 |
| `commit` | 对应源码提交 |
| `file_count` / `symbol_count` / `chunk_count` / `errors` | 统计 |
| `compile_commands_available` | 是否存在编译数据库（当前为 false） |

打开索引时四项版本必须与运行时一致，否则抛 `IndexVersionMismatch` 并提示重建——**不做降级放行**，因为向量空间不兼容会导致结果错但不报错。

## 构建与增量

```bash
pmagent index build --repo <path>                    # 全量
pmagent index build --repo <path> --incremental      # 基于上一快照增量
pmagent index status
```

增量流程：`git diff --name-only 旧提交..新提交` → 复制上一快照 → 删除变更文件的全部行 → 重新解析写入 → 重算统计 → 更新 `current`。
复制后修改保证失败时旧快照仍可用。

## 切块

- 函数整块，前缀包含 `// file:`、`// symbol:`、`// signature:`，保证块自包含。
- 超过 1200 token 的函数按字符窗口切分，每块重复前缀，相邻块重叠 3 行。
- 类只取声明部分（成员函数单独成块），宏单独成块。
- 声明与命名空间只入符号表，不切块。

## 调用图

从 `call_expression` 提取被调名，写入 `refs` 并标注 `confidence=heuristic`。同名重载与虚调用无法区分，精确调用点留待 libclang 精化。

## 内容过滤

默认排除 `vendor/`、`third_party/`、`3rdparty/`、`.build/`、`build/`、`node_modules/`；被排除数量记录在 manifest 的 `extra.discovery`。

## 制品分发

```bash
pmagent index push --to <dir>     # 生成 index-<commit>.tar.gz + .sha256
pmagent index pull --from <archive>
```

解包前校验 sha256；拒绝绝对路径、`..` 与符号链接。

## 已知限制

- 当前机器没有 C++ 编译器，`compile_commands.json` 不可用，`compile_commands_available=false`。
- tree-sitter 语法覆盖不足时会在 `index_errors` 记录，但**仍尽量抽取已解析的符号**；替代语料上约 20% 文件有语法错误，集中在宏密集头文件与 Objective-C++（`.mm`）。
- 向量列 `chunk_vectors` 已建表，向量化在第 6 组接入。
