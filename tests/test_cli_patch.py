from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from pmagent.agents import PatchStore
from pmagent.contracts import (
    CodeLocation,
    Hypothesis,
    LocalizationReport,
    PatchArtifact,
    PatchChecks,
    PatchRisk,
    Provenance,
    Triage,
    Uncertainty,
    Verification,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path: Path, *args: str, unset_key: bool = True) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PMAGENT_DB"] = str(tmp_path / "state.db")
    env["PMAGENT_SESSION"] = str(tmp_path / "session.json")
    env["PMAGENT_MEMORY_DB"] = str(tmp_path / "memory.db")
    env["PYTHONIOENCODING"] = "utf-8"
    if unset_key:
        env.pop("DEEPSEEK_API_KEY", None)
    env.pop("PMAGENT_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-m", "pmagent", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _login(tmp_path: Path) -> None:
    assert _run(tmp_path, "auth", "init", "--username", "admin", "--password", "secret123").returncode == 0
    assert _run(tmp_path, "auth", "login", "--username", "admin", "--password", "secret123").returncode == 0


def _report(tmp_path: Path) -> Path:
    report = LocalizationReport(
        problem_context_ref="p1",
        triage=Triage(stage="geometry"),
        hypotheses=[
            Hypothesis(
                id="H1",
                statement="边界未闭合",
                code_locations=[
                    CodeLocation(file="a.cc", line_start=2, line_end=2, symbol="pm::Alpha")
                ],
            )
        ],
        top_root_cause_ref="H1",
        uncertainty=Uncertainty(level="medium", note="demo"),
        provenance=Provenance(models=["deterministic"]),
    )
    path = tmp_path / "report.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")
    return path


def test_generate_missing_report(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(tmp_path, "patch", "generate", "--report", str(tmp_path / "nope.json"))
    assert result.returncode == 2


def test_generate_requires_model_key(tmp_path: Path) -> None:
    _login(tmp_path)
    report = _report(tmp_path)
    result = _run(tmp_path, "patch", "generate", "--report", str(report), unset_key=True)
    assert result.returncode == 2
    assert "密钥" in result.stderr or "模型" in result.stderr


def test_apply_requires_yes(tmp_path: Path) -> None:
    _login(tmp_path)
    artifact = PatchArtifact(
        id="P1",
        localization_ref="H1",
        diff="--- a/a.cc\n+++ b/a.cc\n@@ -1,1 +1,1 @@\n-x\n+y\n",
        files_changed=["a.cc"],
        risk=PatchRisk(),
        checks=PatchChecks(),
        verification=Verification(),
    )
    PatchStore(tmp_path / "patches").save(artifact, run_id="run1")

    # 工件目录由默认 artifacts/patches 决定，这里用默认位置写入
    default_store = PatchStore()
    default_store.save(artifact, run_id="cli-run")

    result = _run(tmp_path, "patch", "apply", "cli-run", "P1", "--repo", str(tmp_path))
    assert result.returncode == 2
    assert "--yes" in result.stderr or "确认" in result.stderr


def test_list_empty(tmp_path: Path) -> None:
    _login(tmp_path)
    result = _run(tmp_path, "patch", "list", "--run-id", "no-such-run")
    assert result.returncode == 0
    assert "暂无补丁工件" in result.stdout
