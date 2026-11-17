from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pmagent.agents import PatchAuthor, PatchStore, apply_patch, revert_patch
from pmagent.agents.patch import (
    analyze_impact,
    parse_diff,
    self_review,
    static_checks,
    validate_diff,
)
from pmagent.contracts import CodeLocation, Hypothesis
from pmagent.errors import PmagentError, UsageError
from pmagent.knowledge.index import CodeIndex, IndexBuilder

A_CC = "namespace pm {\nint Alpha(int n) { return Beta(n) + 1; }\nint Beta(int n) { return n; }\n}\n"

GOOD_DIFF = """--- a/a.cc
+++ b/a.cc
@@ -1,4 +1,4 @@
 namespace pm {
-int Alpha(int n) { return Beta(n) + 1; }
+int Alpha(int n) { return Beta(n) + 2; }
 int Beta(int n) { return n; }
 }
"""

MULTI_FILE_DIFF = GOOD_DIFF + "".join(
    f"--- a/f{index}.cc\n+++ b/f{index}.cc\n@@ -1,1 +1,1 @@\n-x\n+y\n"
    for index in range(3)
)

HEADER_DIFF = """--- a/include/api.h
+++ b/include/api.h
@@ -1,1 +1,1 @@
-x
+y
"""


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "a.cc").write_text(A_CC, encoding="utf-8")
    (repo / "test_alpha.cc").write_text("void TestAlpha() {}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True, check=True)
    return repo


@pytest.fixture()
def indexed(tmp_path: Path):  # noqa: ANN201
    repo = _repo(tmp_path)
    root = tmp_path / "index"
    IndexBuilder(root).build(repo)
    index = CodeIndex.open(root)
    try:
        yield index, repo
    finally:
        index.close()


class FakeGenerator:
    def __init__(self, diffs: list[str]) -> None:
        self.diffs = diffs

    def generate(self, context, hypothesis, count):  # noqa: ANN001
        return self.diffs


def test_parse_diff() -> None:
    summary = parse_diff(GOOD_DIFF)
    assert summary.files == ["a.cc"]
    assert summary.added == 1
    assert summary.removed == 1
    assert summary.hunks == 1


def test_parse_diff_rejects_non_diff() -> None:
    with pytest.raises(ValueError):
        parse_diff("hello world")


def test_validate_diff_accepts_minimal() -> None:
    assert validate_diff(GOOD_DIFF, allowed_files=["a.cc"]) == []


def test_validate_diff_rejects_multi_file() -> None:
    errors = validate_diff(MULTI_FILE_DIFF)
    assert any("超过上限" in error for error in errors)


def test_validate_diff_rejects_header() -> None:
    errors = validate_diff(HEADER_DIFF)
    assert any("公共头文件" in error or "include" in error for error in errors)


def test_validate_diff_rejects_out_of_scope_file() -> None:
    errors = validate_diff(GOOD_DIFF, allowed_files=["other.cc"])
    assert any("越界" in error for error in errors)


def test_validate_diff_rejects_large_change() -> None:
    big = GOOD_DIFF + "\n".join(f"+line{i}" for i in range(200))
    errors = validate_diff(big, allowed_files=["a.cc"])
    assert any("改动行数" in error for error in errors)


def test_static_checks_skip_without_toolchain() -> None:
    checks = static_checks(GOOD_DIFF)
    assert checks.compile.status == "skipped"
    assert "compile_commands" in checks.compile.detail


def test_impact_analysis_flags_threads_and_backends() -> None:
    diff = GOOD_DIFF.replace(
        "Beta(n) + 2;", "Beta(n) + 2; // mutex vulkan render"
    )
    risk = analyze_impact(diff, callers=["c1", "c2", "c3", "c4", "c5", "c6"])
    assert risk.thread_safety
    assert "vulkan" in risk.backends
    assert "调用方较多" in risk.note


def test_self_review_scores_small_single_file(tmp_path: Path) -> None:
    from pmagent.agents.patch import PatchContext

    context = PatchContext(file="a.cc", symbol="pm::Alpha", related_tests=["test_alpha.cc"])
    review = self_review(GOOD_DIFF, context, callers=["c1"])
    assert review["score"] >= 0.8
    assert review["recommendation"] == "accept"


def test_self_review_penalizes_multi_file() -> None:
    from pmagent.agents.patch import PatchContext

    context = PatchContext(file="a.cc", symbol="pm::Alpha")
    review = self_review(MULTI_FILE_DIFF, context)
    assert review["score"] < 0.8
    assert any("多个文件" in issue for issue in review["issues"])


def test_context_collector(indexed) -> None:  # noqa: ANN001
    index, repo = indexed
    from pmagent.agents.patch import PatchContextCollector

    context = PatchContextCollector(index, repo_root=repo).collect("a.cc", "pm::Alpha")
    assert "Alpha" in context.body
    assert "pm::Alpha" in context.callers or any("Beta" in callee for callee in context.callees)
    assert context.historical_fixes
    assert context.related_tests


def test_author_produces_artifact(indexed) -> None:  # noqa: ANN001
    index, repo = indexed
    hypothesis = Hypothesis(
        id="H1",
        statement="边界未处理",
        code_locations=[CodeLocation(file="a.cc", line_start=2, line_end=3, symbol="pm::Alpha")],
    )
    author = PatchAuthor(index, repo_root=repo, generator=FakeGenerator([GOOD_DIFF]))
    artifacts = author.propose(hypothesis, run_id="run1")
    assert len(artifacts) == 1
    assert artifacts[0].files_changed == ["a.cc"]
    assert artifacts[0].checks.compile.status == "skipped"
    assert artifacts[0].base_commit


def test_author_filters_invalid_diff(indexed) -> None:  # noqa: ANN001
    index, repo = indexed
    hypothesis = Hypothesis(
        id="H1",
        statement="x",
        code_locations=[CodeLocation(file="a.cc", line_start=2, line_end=2, symbol="pm::Alpha")],
    )
    author = PatchAuthor(index, repo_root=repo, generator=FakeGenerator([MULTI_FILE_DIFF]))
    with pytest.raises(PmagentError):
        author.propose(hypothesis, run_id="run1")


def test_store_roundtrip(indexed, tmp_path: Path) -> None:  # noqa: ANN001
    index, repo = indexed
    hypothesis = Hypothesis(
        id="H1",
        statement="x",
        code_locations=[CodeLocation(file="a.cc", line_start=2, line_end=2, symbol="pm::Alpha")],
    )
    artifacts = PatchAuthor(index, repo_root=repo, generator=FakeGenerator([GOOD_DIFF])).propose(
        hypothesis, run_id="run1"
    )
    store = PatchStore(tmp_path / "patches")
    store.save(artifacts[0], run_id="run1")
    loaded, diff_path = store.load("run1", artifacts[0].id)
    assert loaded.id == artifacts[0].id
    assert diff_path.exists()
    assert len(store.list("run1")) == 1


def test_apply_requires_confirmation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    diff = tmp_path / "p.diff"
    diff.write_text(GOOD_DIFF, encoding="utf-8")
    with pytest.raises(UsageError):
        apply_patch(repo, diff, confirmed=False)


def test_apply_and_revert(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    diff = tmp_path / "p.diff"
    diff.write_text(GOOD_DIFF, encoding="utf-8")
    apply_patch(repo, diff, confirmed=True)
    assert "+ 2" in (repo / "a.cc").read_text(encoding="utf-8")
    revert_patch(repo, diff)
    assert "+ 2" not in (repo / "a.cc").read_text(encoding="utf-8")
