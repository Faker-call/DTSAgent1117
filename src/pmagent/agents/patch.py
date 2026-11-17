"""补丁建议：上下文收集、diff 校验、静态检查、影响面、自审、工件与应用/回滚。"""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pmagent.contracts import (
    Hypothesis,
    PatchArtifact,
    PatchChecks,
    PatchCheckStatus,
    PatchRisk,
    Verification,
)
from pmagent.errors import PmagentError, UsageError
from pmagent.knowledge.index import CodeIndex

MAX_FILES = 3
MAX_CHANGED_LINES = 150
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,?\d+)? @@")
FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)
ADDED_RE = re.compile(r"^\+(?!\+\+)", re.MULTILINE)
REMOVED_RE = re.compile(r"^-(?!--)", re.MULTILINE)

THREAD_WORDS = ("mutex", "atomic", "thread", "lock", "async", "condition_variable")
PERF_WORDS = ("for (", "while (", "render", "frame", "draw")
BACKEND_WORDS = ("vulkan", "opengl", " gl", "gles", "metal")


@dataclass
class PatchContext:
    file: str
    symbol: str
    body: str = ""
    callers: list[str] = field(default_factory=list)
    callees: list[str] = field(default_factory=list)
    style_samples: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    historical_fixes: list[str] = field(default_factory=list)


class PatchContextCollector:
    def __init__(
        self, code_index: CodeIndex, *, repo_root: str | Path | None = None
    ) -> None:
        self.code_index = code_index
        self.repo_root = Path(repo_root) if repo_root else None

    def collect(self, file_path: str, symbol: str) -> PatchContext:
        context = PatchContext(file=file_path, symbol=symbol)
        row = self.code_index.conn.execute(
            "SELECT text FROM chunks WHERE symbol_qualified = ? LIMIT 1", (symbol,)
        ).fetchone()
        if row is not None:
            context.body = row["text"]
        leaf = symbol.split("::")[-1]
        context.callers = [
            item["from_symbol"]
            for item in self.code_index.conn.execute(
                "SELECT DISTINCT from_symbol FROM refs WHERE to_name = ? LIMIT 10", (leaf,)
            ).fetchall()
        ]
        context.callees = [
            item["to_name"]
            for item in self.code_index.conn.execute(
                "SELECT DISTINCT to_name FROM refs WHERE from_symbol = ? LIMIT 10", (symbol,)
            ).fetchall()
        ]
        style_rows = self.code_index.conn.execute(
            "SELECT text FROM chunks WHERE file_path = ? AND symbol_qualified != ?"
            " LIMIT 2",
            (file_path, symbol),
        ).fetchall()
        context.style_samples = [item["text"][:600] for item in style_rows]
        test_rows = self.code_index.conn.execute(
            "SELECT DISTINCT file_path FROM symbols WHERE file_path LIKE '%test%'"
            " AND qualified_name LIKE ? LIMIT 5",
            (f"%{leaf}%",),
        ).fetchall()
        context.related_tests = [item["file_path"] for item in test_rows]
        context.historical_fixes = self._git_log(file_path)
        return context

    def _git_log(self, file_path: str) -> list[str]:
        if self.repo_root is None:
            return []
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repo_root),
                    "log",
                    "-n5",
                    "--pretty=format:%h %s",
                    "--",
                    file_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# diff 校验
# ---------------------------------------------------------------------------


@dataclass
class DiffSummary:
    files: list[str] = field(default_factory=list)
    added: int = 0
    removed: int = 0
    hunks: int = 0


def parse_diff(text: str) -> DiffSummary:
    if not text.strip().startswith(("--- ", "diff --git")):
        raise ValueError("不是 unified diff")
    files = FILE_RE.findall(text)
    return DiffSummary(
        files=files,
        added=len(ADDED_RE.findall(text)),
        removed=len(REMOVED_RE.findall(text)),
        hunks=len([line for line in text.splitlines() if HUNK_RE.match(line)]),
    )


def validate_diff(
    text: str,
    *,
    allowed_files: list[str] | None = None,
    max_files: int = MAX_FILES,
    max_changed_lines: int = MAX_CHANGED_LINES,
    allow_headers: bool = False,
) -> list[str]:
    errors: list[str] = []
    try:
        summary = parse_diff(text)
    except ValueError as exc:
        return [str(exc)]
    if not summary.files:
        errors.append("diff 中没有文件变更")
    if len(summary.files) > max_files:
        errors.append(f"改动文件数 {len(summary.files)} 超过上限 {max_files}")
    if summary.added + summary.removed > max_changed_lines:
        errors.append(f"改动行数 {summary.added + summary.removed} 超过上限 {max_changed_lines}")
    if allowed_files is not None:
        for path in summary.files:
            if path not in allowed_files:
                errors.append(f"改动越界：{path} 不在允许范围内")
    if not allow_headers:
        for path in summary.files:
            if path.endswith((".h", ".hpp", ".hh", ".hxx")):
                errors.append(f"不允许改动公共头文件：{path}")
    if any(path.startswith("include/") for path in summary.files) and not allow_headers:
        errors.append("不允许改动对外 include 目录")
    return errors


def static_checks(diff: str, *, repo_root: str | Path | None = None) -> PatchChecks:
    checks = PatchChecks()
    try:
        parse_diff(diff)
        checks.format = PatchCheckStatus(status="passed", detail="diff 结构可解析")
    except ValueError as exc:
        checks.format = PatchCheckStatus(status="failed", detail=str(exc))
        return checks

    clang_format = shutil.which("clang-format")
    if clang_format is None:
        checks.format = PatchCheckStatus(
            status="skipped", detail="未安装 clang-format，格式检查降级"
        )
    else:
        checks.format = PatchCheckStatus(status="passed", detail="clang-format 可用")

    compile_commands = None
    if repo_root is not None:
        for candidate in ("compile_commands.json", "build/compile_commands.json"):
            path = Path(repo_root) / candidate
            if path.exists():
                compile_commands = path
                break
    if compile_commands is None:
        checks.compile = PatchCheckStatus(
            status="skipped", detail="缺少 compile_commands.json，未做编译校验"
        )
    else:
        checks.compile = PatchCheckStatus(
            status="skipped", detail="已找到编译数据库，但本机无 C++ 编译器"
        )
    checks.unit_tests = PatchCheckStatus(status="skipped", detail="补丁阶段不执行测试")
    return checks


def analyze_impact(
    diff: str, *, callers: list[str], repo_root: str | Path | None = None
) -> PatchRisk:
    lowered = diff.lower()
    backends = [word.strip() for word in BACKEND_WORDS if word in lowered]
    risk = PatchRisk(
        abi=any(path.startswith("include/") for path in FILE_RE.findall(diff)),
        thread_safety=any(word in lowered for word in THREAD_WORDS),
        backends=sorted(set(backends)),
        perf=any(word in lowered for word in PERF_WORDS),
    )
    notes = []
    if len(callers) > 5:
        notes.append(f"调用方较多（{len(callers)} 个），回归面较大")
    if risk.thread_safety:
        notes.append("改动涉及并发原语，需检查锁顺序")
    if risk.perf:
        notes.append("改动位于渲染热路径，关注帧耗时")
    risk.note = "；".join(notes)
    return risk


def self_review(
    diff: str, context: PatchContext, *, callers: list[str] | None = None
) -> dict[str, Any]:
    issues: list[str] = []
    try:
        summary = parse_diff(diff)
    except ValueError as exc:
        return {"score": 0.0, "issues": [str(exc)], "recommendation": "reject"}
    if len(summary.files) != 1:
        issues.append("改动跨多个文件，可能不是最小修复")
    if summary.added + summary.removed > 60:
        issues.append("改动偏大，检查是否治标")
    if not context.related_tests:
        issues.append("未找到相关测试，建议补充用例")
    if callers and len(callers) > 5:
        issues.append("调用方较多，需评估回归")
    score = max(0.0, 1.0 - 0.2 * len(issues))
    return {
        "score": round(score, 2),
        "issues": issues,
        "recommendation": "accept" if score >= 0.6 else "review",
    }


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


class PatchGenerator(Protocol):
    def generate(self, context: PatchContext, hypothesis: Hypothesis, count: int) -> list[str]: ...


def build_patch_prompt(context: PatchContext, hypothesis: Hypothesis, count: int) -> str:
    """代码片段来自仓库，属于不可信输入：包裹后再进提示词。"""
    from pmagent.safety import wrap

    return (
        "为下面的缺陷生成最小 unified diff。要求：只改一个文件、不改公共头文件、"
        "保持现有风格、不重排无关代码。\n"
        f"假设：{hypothesis.statement}\n"
        f"文件：{context.file}\n符号：{context.symbol}\n"
        "代码：\n"
        f"{wrap(context.body[:4000])}\n"
        f"调用方：{context.callers}\n"
        f"生成 {count} 个候选，输出 JSON {{patches: [diff字符串]}}"
    )


class LLMPatchGenerator:
    def __init__(self, gateway: Any, *, model_role: str = "reasoner") -> None:
        self.gateway = gateway
        self.model_role = model_role

    def generate(
        self, context: PatchContext, hypothesis: Hypothesis, count: int
    ) -> list[str]:  # pragma: no cover
        from pydantic import BaseModel

        from pmagent.gateway import complete_json

        class _PatchList(BaseModel):
            patches: list[str] = []

        result = complete_json(
            self.gateway,
            [{"role": "user", "content": build_patch_prompt(context, hypothesis, count)}],
            _PatchList,
        )
        return result.value.patches[:count]


class PatchAuthor:
    def __init__(
        self,
        code_index: CodeIndex,
        *,
        repo_root: str | Path | None = None,
        generator: PatchGenerator | None = None,
    ) -> None:
        self.code_index = code_index
        self.repo_root = Path(repo_root) if repo_root else None
        self.collector = PatchContextCollector(code_index, repo_root=repo_root)
        self.generator = generator

    def propose(
        self,
        hypothesis: Hypothesis,
        *,
        run_id: str,
        count: int = 2,
    ) -> list[PatchArtifact]:
        if not hypothesis.code_locations:
            raise PmagentError("假设缺少代码位置，无法生成补丁")
        location = hypothesis.code_locations[0]
        context = self.collector.collect(location.file, location.symbol or "")
        if self.generator is None:
            raise PmagentError(
                "补丁生成需要模型", hint="配置 DEEPSEEK_API_KEY 后重试，或使用 --dry-run"
            )
        diffs = self.generator.generate(context, hypothesis, count)
        artifacts: list[PatchArtifact] = []
        for index, diff in enumerate(diffs, start=1):
            errors = validate_diff(
                diff, allowed_files=[location.file] if location.file else None
            )
            if errors:
                continue
            checks = static_checks(diff, repo_root=self.repo_root)
            risk = analyze_impact(diff, callers=context.callers, repo_root=self.repo_root)
            review = self_review(diff, context, callers=context.callers)
            artifacts.append(
                PatchArtifact(
                    id=f"P{index}",
                    localization_ref=hypothesis.id,
                    base_commit=self._head(),
                    diff=diff,
                    files_changed=parse_diff(diff).files,
                    rationale=[
                        f"针对假设：{hypothesis.statement}",
                        f"自审得分 {review['score']}，建议 {review['recommendation']}",
                        *review["issues"],
                    ],
                    risk=risk,
                    checks=checks,
                    verification=Verification(
                        steps=[
                            "在复现工程上应用补丁并重跑",
                            "确认缺陷现象消失且无新告警",
                        ],
                        acceptance=["复现步骤不再触发", "相关单测通过"],
                    ),
                )
            )
        if not artifacts:
            raise PmagentError("所有候选补丁都未通过 diff 校验", hint="检查生成约束")
        return artifacts

    def _head(self) -> str | None:
        if self.repo_root is None:
            return None
        try:
            result = subprocess.run(
                ["git", "-C", str(self.repo_root), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() or None


# ---------------------------------------------------------------------------
# 工件存储与应用/回滚
# ---------------------------------------------------------------------------


class PatchStore:
    def __init__(self, root: str | Path = Path("artifacts") / "patches") -> None:
        self.root = Path(root)

    def save(self, artifact: PatchArtifact, *, run_id: str) -> Path:
        directory = self.root / run_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{artifact.id}.diff").write_text(artifact.diff, encoding="utf-8")
        (directory / f"{artifact.id}.json").write_text(
            artifact.model_dump_json(indent=2), encoding="utf-8"
        )
        return directory

    def load(self, run_id: str, patch_id: str) -> tuple[PatchArtifact, Path]:
        directory = self.root / run_id
        meta = directory / f"{patch_id}.json"
        diff = directory / f"{patch_id}.diff"
        if not meta.exists() or not diff.exists():
            raise PmagentError(f"补丁不存在：{run_id}/{patch_id}")
        return PatchArtifact.model_validate_json(meta.read_text(encoding="utf-8")), diff

    def list(self, run_id: str) -> list[PatchArtifact]:
        directory = self.root / run_id
        if not directory.exists():
            return []
        return [
            PatchArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]


def apply_patch(
    repo_root: str | Path, diff_path: str | Path, *, confirmed: bool
) -> dict[str, Any]:
    repo = Path(repo_root)
    diff = Path(diff_path)
    if not confirmed:
        raise UsageError(
            "应用补丁需要显式确认",
            hint="加 --yes 确认；默认只生成工件，不修改工作区",
        )
    if not diff.exists():
        raise PmagentError(f"补丁文件不存在：{diff}")
    snapshot = repo / ".pmagent" / "patch-snapshots"
    snapshot.mkdir(parents=True, exist_ok=True)
    snapshot_id = uuid.uuid4().hex[:8]
    backup = snapshot / f"{snapshot_id}.diff"
    backup.write_text(_git(repo, "diff") or "", encoding="utf-8")

    result = subprocess.run(
        ["git", "-C", str(repo), "apply", str(diff.resolve())],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PmagentError(
            f"补丁应用失败：{result.stderr.strip()[:300]}", hint="工作区未改动，可检查补丁上下文"
        )
    marker = snapshot / f"{snapshot_id}.applied"
    marker.write_text(str(diff), encoding="utf-8")
    return {"snapshot_id": snapshot_id, "backup": str(backup), "marker": str(marker)}


def revert_patch(repo_root: str | Path, diff_path: str | Path) -> None:
    repo = Path(repo_root)
    diff = Path(diff_path)
    if not diff.exists():
        raise PmagentError(f"补丁文件不存在：{diff}")
    result = subprocess.run(
        ["git", "-C", str(repo), "apply", "-R", str(diff.resolve())],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PmagentError(
            f"回滚失败：{result.stderr.strip()[:300]}",
            hint="工作区可能已被修改，请用快照人工恢复",
        )


def _git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


__all__ = [
    "DiffSummary",
    "LLMPatchGenerator",
    "PatchAuthor",
    "PatchContext",
    "PatchContextCollector",
    "PatchGenerator",
    "PatchStore",
    "analyze_impact",
    "apply_patch",
    "parse_diff",
    "revert_patch",
    "self_review",
    "static_checks",
    "validate_diff",
]
