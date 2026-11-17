"""文件发现：git 优先，回退到目录遍历；扩展名与内容过滤。"""

from __future__ import annotations

import subprocess
from pathlib import Path

CPP_EXTS = {".h", ".hpp", ".hh", ".hxx", ".c", ".cc", ".cpp", ".cxx", ".mm", ".inl"}

DEFAULT_EXCLUDES: tuple[str, ...] = (
    "vendor/",
    "third_party/",
    "3rdparty/",
    ".build/",
    "node_modules/",
    ".git/",
    "build/",
    "dist/",
)


def is_excluded(rel_path: str, excludes: tuple[str, ...] | list[str] = DEFAULT_EXCLUDES) -> bool:
    normalized = rel_path.replace("\\", "/")
    return any(token in normalized for token in excludes)


def _git_files(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = result.stdout.decode("utf-8", errors="replace")
    return [item for item in raw.split("\0") if item]


def discover_files(
    root: str | Path,
    *,
    excludes: tuple[str, ...] | list[str] = DEFAULT_EXCLUDES,
    include_tests: bool = True,
) -> tuple[list[str], dict[str, int]]:
    """返回（待索引的相对路径列表，被过滤计数）。"""
    root = Path(root)
    candidates = _git_files(root)
    source = "git"
    if candidates is None:
        source = "walk"
        candidates = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        ]

    stats = {"non_cpp": 0, "excluded": 0, "tests": 0}
    selected: list[str] = []
    for rel in candidates:
        suffix = Path(rel).suffix.lower()
        if suffix not in CPP_EXTS:
            stats["non_cpp"] += 1
            continue
        if is_excluded(rel, excludes):
            stats["excluded"] += 1
            continue
        if not include_tests and ("/test/" in f"/{rel}" or rel.startswith("test/")):
            stats["tests"] += 1
            continue
        selected.append(rel)

    stats["source"] = 1 if source == "git" else 0  # type: ignore[assignment]
    return sorted(selected), stats


__all__ = ["CPP_EXTS", "DEFAULT_EXCLUDES", "discover_files", "is_excluded"]
