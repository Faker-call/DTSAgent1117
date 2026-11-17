"""集成验收：性能、恢复、权限、并发与多 Agent 对比。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pmagent.authz import ResourceBusy, SingleWriterLock
from pmagent.authz.policy import PolicyEngine as Policy
from pmagent.contracts import IntentDecision, SubAgentResult, TaskBrief, ToolProposal, User
from pmagent.eval.metrics import percentile
from pmagent.knowledge.index import CodeIndex, IndexBuilder
from pmagent.memory import RunEventLog
from pmagent.orchestrator import Coordinator, Pipeline, Stage, SubAgentRuntime

REPO_ROOT = Path(__file__).resolve().parents[1]
QUESTION = "如何设置自定义地图样式"

A_CC = "namespace pm {\nint Alpha(int n) { return n + 1; }\n}\n"


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    env["PMAGENT_DB"] = str(tmp_path / "state.db")
    env["PMAGENT_SESSION"] = str(tmp_path / "session.json")
    env["PMAGENT_MEMORY_DB"] = str(tmp_path / "memory.db")
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PMAGENT_TOKEN", None)
    env.pop("DEEPSEEK_API_KEY", None)
    return env


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pmagent", *args],
        cwd=tmp_path,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _login(tmp_path: Path, username: str = "admin") -> None:
    if username == "admin":
        _run(tmp_path, "auth", "init", "--username", "admin", "--password", "secret123")
    _run(tmp_path, "auth", "login", "--username", username, "--password", "secret123")


def _repo(tmp_path: Path, content: str = A_CC) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@e.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    (repo / "a.cc").write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True, check=True)
    return repo


# -- 16.2 性能与成本 --------------------------------------------------------


def test_perf_ask_p95_under_10s(tmp_path: Path) -> None:
    from pmagent.contracts import DocRecord
    from pmagent.knowledge.corpus import DocCorpus

    _login(tmp_path)
    corpus = tmp_path / "docs.db"
    db = DocCorpus(corpus)
    db.add_documents(
        [
            DocRecord(
                id="s",
                title="样式",
                text=f"# 样式\n\n{QUESTION}：调用 setCustomMapStyle。\n",
            )
        ]
    )
    db.close()

    durations: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        result = _run(
            tmp_path, "ask", QUESTION, "--corpus", str(corpus), "--sdk-version", "6.0.1"
        )
        durations.append((time.perf_counter() - started) * 1000)
        assert result.returncode == 0, result.stderr
    assert percentile(durations, 95) < 10_000


def test_perf_diagnose_under_120s(tmp_path: Path) -> None:
    _login(tmp_path)
    repo = _repo(tmp_path)
    index_root = tmp_path / "index"
    IndexBuilder(index_root).build(repo)
    stack = tmp_path / "stack.txt"
    stack.write_text("#0 0x0000000000a1b2c3 pm::Alpha at a.cc:2\n", encoding="utf-8")
    log = tmp_path / "log.txt"
    log.write_text("ERROR decode failed E1024\n", encoding="utf-8")

    durations: list[float] = []
    for _ in range(3):
        started = time.perf_counter()
        result = _run(
            tmp_path,
            "diagnose",
            "放大后断裂",
            "--log",
            str(log),
            "--stack",
            str(stack),
            "--version",
            "6.0.1",
            "--repo",
            str(repo),
            "--index-root",
            str(index_root),
        )
        durations.append((time.perf_counter() - started) * 1000)
        assert result.returncode == 0, result.stderr
    assert percentile(durations, 95) < 120_000


# -- 16.4 恢复演练 ----------------------------------------------------------


def test_resume_skips_completed_stages(tmp_path: Path) -> None:
    log = RunEventLog(tmp_path / "run1")
    executed: list[str] = []
    stages = ["normalize", "triage", "slice", "report"]
    budget = {"tokens": 0}

    def make(name: str):  # noqa: ANN202
        def fn(ctx):  # noqa: ANN001
            executed.append(name)
            budget["tokens"] += 100
            log.append("stage_finished", stage=name, status="ok")

        return fn

    Pipeline([Stage(name, make(name)) for name in stages[:2]]).run({})
    log.checkpoint({"tokens_used": budget["tokens"]})
    log.append("interrupted", reason="sigint")

    point = log.resume(stages)
    assert point.next_stage == "slice"
    assert point.completed == ["normalize", "triage"]
    assert point.state["tokens_used"] == 200

    Pipeline([Stage(name, make(name)) for name in stages[2:]]).run({})
    assert executed == stages
    assert budget["tokens"] == 400


# -- 16.5 权限与并发 --------------------------------------------------------


def test_permission_matrix_cli(tmp_path: Path) -> None:
    _login(tmp_path)
    assert (
        _run(tmp_path, "auth", "user", "add", "--username", "v", "--role", "viewer", "--password", "secret123").returncode
        == 0
    )
    assert (
        _run(tmp_path, "auth", "user", "add", "--username", "d", "--role", "developer", "--password", "secret123").returncode
        == 0
    )
    repo = _repo(tmp_path)
    _login(tmp_path, "v")
    denied = _run(tmp_path, "index", "build", "--repo", str(repo))
    assert denied.returncode == 6

    _login(tmp_path, "d")
    denied = _run(tmp_path, "patch", "apply", "r", "p", "--yes")
    assert denied.returncode == 6

    _login(tmp_path)
    allowed = _run(tmp_path, "index", "build", "--repo", str(repo))
    assert allowed.returncode == 0


def test_concurrent_build_rejected_without_corruption(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root = tmp_path / "index"
    results: list[str] = []
    errors: list[str] = []

    def worker() -> None:
        try:
            IndexBuilder(root).build(repo)
            results.append("ok")
        except ResourceBusy:
            errors.append("busy")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 1
    index = CodeIndex.open(root)
    try:
        assert index.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    finally:
        index.close()


def test_audit_trace_has_user(tmp_path: Path) -> None:
    class Recorder:
        def __init__(self) -> None:
            self.events: list[dict] = []

        def record(self, event: str, **fields: object) -> None:
            self.events.append({"event": event, **fields})

    recorder = Recorder()
    engine = Policy(trace=recorder)
    user = User(user_id="u1", username="admin", roles=["admin"], status="active")
    decision = engine.authorize_tool(
        user,
        "diagnosis",
        ToolProposal(tool="code.get_slice", stage="slice", args={"path": "a.cc"}),
    )
    assert decision.decision == "allow"
    assert recorder.events and recorder.events[0]["user_id"] == "u1"


# -- 16.6 多 Agent 对比 -----------------------------------------------------


def _ok(task_id: str, role: str, evidence: list[str]) -> SubAgentResult:
    return SubAgentResult(
        task_id=task_id, agent_role=role, status="ok", evidence_ids=evidence
    )


def test_single_and_multi_share_evidence() -> None:
    runtime = SubAgentRuntime()
    runtime.register("knowledge", lambda brief: _ok(brief.task_id, "knowledge", ["E1", "E2"]))
    runtime.register(
        "retrieval",
        lambda brief: _ok(
            brief.task_id, "retrieval", ["E1", f"E-{brief.intent.get('scope')}"]
        ),
    )
    runtime.register("diagnosis", lambda brief: _ok(brief.task_id, "diagnosis", ["E2"]))

    single = Coordinator(runtime).dispatch(
        IntentDecision(primary="usage", evidence_gate={"passed": True}),
        "how",
        problem_ref="p1",
        evidence_sufficient=True,
    )
    multi = Coordinator(runtime).dispatch(
        IntentDecision(primary="defect"), "crash", problem_ref="p1", scopes=["code", "docs"]
    )
    assert single.metrics.short_circuited
    assert set(single.evidence_ids) <= set(multi.evidence_ids)
    assert multi.metrics.duplicates >= 1


def test_branch_failure_keeps_conclusion() -> None:
    runtime = SubAgentRuntime()

    def broken(brief: TaskBrief) -> SubAgentResult:
        raise RuntimeError("branch down")

    runtime.register("retrieval", broken)
    runtime.register("diagnosis", lambda brief: _ok(brief.task_id, "diagnosis", ["E2"]))
    result = Coordinator(runtime).dispatch(
        IntentDecision(primary="defect"), "crash", problem_ref="p1", scopes=["code"]
    )
    assert result.degraded
    assert "E2" in result.evidence_ids


def test_lock_busy_is_resource_busy(tmp_path: Path) -> None:
    holder = SingleWriterLock(tmp_path / "x.lock")
    holder.acquire()
    try:
        with pytest.raises(ResourceBusy):
            SingleWriterLock(tmp_path / "x.lock").acquire(timeout=0.05)
    finally:
        holder.release()
