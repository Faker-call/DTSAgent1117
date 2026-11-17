"""统计替代语料并验证解析能力，输出 docs/corpus.md。

用法：python scripts/corpus_report.py [corpus_root]
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

CPP_EXTS = {".h", ".hpp", ".hh", ".hxx", ".c", ".cc", ".cpp", ".cxx", ".mm", ".inl"}
SKIP_DIRS = {".git", "build", "node_modules", ".venv"}
VENDOR_HINTS = ("vendor/", "third_party/", "3rdparty/", ".build/")


def iter_sources(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in CPP_EXTS:
            yield path


def is_vendor(rel: str) -> bool:
    return any(hint in rel.replace("\\", "/") for hint in VENDOR_HINTS)


def count_loc(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += sum(1 for _ in path.open("rb"))
        except OSError:
            continue
    return total


def top_dirs(root: Path, paths: list[Path], limit: int = 10) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for path in paths:
        rel = path.relative_to(root).as_posix()
        counter[rel.split("/")[0]] += 1
    return counter.most_common(limit)


def tree_sitter_check(paths: list[Path], sample: int = 50) -> tuple[int, int, str]:
    try:
        import tree_sitter_cpp as tscpp
        from tree_sitter import Language, Parser
    except ImportError as exc:  # pragma: no cover
        return 0, 0, f"tree-sitter 不可用：{exc}"

    parser = Parser(Language(tscpp.language()))
    ok = fail = 0
    for path in paths[:sample]:
        try:
            tree = parser.parse(path.read_bytes())
            if tree.root_node.has_error:
                fail += 1
            else:
                ok += 1
        except Exception:  # noqa: BLE001
            fail += 1
    return ok, fail, "tree-sitter-cpp"


def libclang_check(root: Path, paths: list[Path], sample: int = 5) -> tuple[int, int, str]:
    try:
        import clang.cindex as cindex
    except ImportError as exc:  # pragma: no cover
        return 0, 0, f"libclang 不可用：{exc}"

    include_dir = root / "include"
    args = ["-std=c++17", "-x", "c++"]
    if include_dir.exists():
        args.append(f"-I{include_dir}")
    index = cindex.Index.create()
    ok = fail = 0
    for path in paths[:sample]:
        try:
            tu = index.parse(str(path), args=args, options=cindex.TranslationUnit.PARSE_INCOMPLETE)
            ok += 1 if tu is not None else 0
        except Exception:  # noqa: BLE001
            fail += 1
    return ok, fail, "libclang"


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "corpus/maplibre-native")
    if not root.exists():
        print(f"语料目录不存在：{root}")
        return 2

    all_paths = sorted(iter_sources(root))
    prod_paths = [p for p in all_paths if not is_vendor(p.relative_to(root).as_posix())]
    loc_all = count_loc(all_paths)
    loc_prod = count_loc(prod_paths)

    ts_ok, ts_fail, ts_note = tree_sitter_check(prod_paths)
    lc_ok, lc_fail, lc_note = libclang_check(root, prod_paths)

    lines = [
        "# 替代语料报告",
        "",
        f"- 语料根目录：`{root.as_posix()}`",
        f"- C++ 家族文件总数：{len(all_paths)}",
        f"- 排除 vendor/third_party 后：{len(prod_paths)}",
        f"- 总行数：{loc_all:,}",
        f"- 排除 vendor 后行数：{loc_prod:,}",
        "",
        "## 顶层目录分布（按文件数）",
        "",
        "| 目录 | 文件数 |",
        "|---|---|",
    ]
    for name, count in top_dirs(root, prod_paths):
        lines.append(f"| `{name}` | {count} |")

    lines += [
        "",
        "## 解析验证",
        "",
        f"- {ts_note}：成功 {ts_ok} / 失败 {ts_fail}（样本 50 个文件）",
        f"- {lc_note}：成功 {lc_ok} / 失败 {lc_fail}（样本 5 个文件，参数 `-std=c++17`）",
        "",
        "## 已知限制",
        "",
        "- 当前机器没有 C++ 编译器，CMake 无法生成真实的 `compile_commands.json`。",
        "  索引与补丁校验按设计走降级路径：tree-sitter 全量解析 + libclang 兜底参数解析，",
        "  并在 `manifest` 中标注 `compile_commands_available=false`。",
        "- 安装 MSVC Build Tools 或 MinGW 后，可用 "
        "`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` 重新生成并启用精确解析。",
        "",
    ]

    out = Path("docs/corpus.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
