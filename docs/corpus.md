# 替代语料报告

- 语料根目录：`corpus/maplibre-native`
- C++ 家族文件总数：2368
- 排除 vendor/third_party 后：2228
- 总行数：667,970
- 排除 vendor 后行数：329,113

## 顶层目录分布（按文件数）

| 目录 | 文件数 |
|---|---|
| `src` | 814 |
| `platform` | 712 |
| `include` | 464 |
| `test` | 168 |
| `plugins` | 24 |
| `benchmark` | 17 |
| `render-test` | 17 |
| `expression-test` | 9 |
| `bin` | 3 |

## 解析验证

- tree-sitter-cpp：成功 46 / 失败 4（样本 50 个文件）
- libclang：成功 5 / 失败 0（样本 5 个文件，参数 `-std=c++17`）

## 已知限制

- 当前机器没有 C++ 编译器，CMake 无法生成真实的 `compile_commands.json`。
  索引与补丁校验按设计走降级路径：tree-sitter 全量解析 + libclang 兜底参数解析，
  并在 `manifest` 中标注 `compile_commands_available=false`。
- 安装 MSVC Build Tools 或 MinGW 后，可用 `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` 重新生成并启用精确解析。
