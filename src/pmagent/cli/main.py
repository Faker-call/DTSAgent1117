"""命令行入口与退出码约定。

退出码：0 成功 / 1 内部错误 / 2 参数或配置错误 / 3 证据不足 /
4 定位失败 / 5 预算超限 / 6 权限不足 / 130 用户中断
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pmagent import __version__
from pmagent.agents import (
    AnswerComposer,
    CitationValidator,
    DiagnosisAgent,
    DialogueManager,
    IntentClassifier,
    LLMPatchGenerator,
    PatchAuthor,
    PatchStore,
    apply_patch,
    render_markdown,
    revert_patch,
)
from pmagent.authz import (
    PasswordHasherService,
    Permission,
    PolicyEngine,
    Role,
    SessionManager,
    SingleWriterLock,
    UserStore,
    explain_denial,
)
from pmagent.config import (
    config_path,
    load_config,
    redacted_view,
    write_default_config,
)
from pmagent.contracts import LocalizationReport, Product, SessionState, User
from pmagent.errors import (
    BudgetExceeded,
    ExitCode,
    InsufficientEvidence,
    Interrupted,
    LocalizationFailed,
    PermissionDenied,
    PmagentError,
    UsageError,
)
from pmagent.eval import (
    EvalRunner,
    admit_case,
    append_review,
    calibration_buckets,
    cohen_kappa,
    compare_reports,
    load_cases,
)
from pmagent.evidence import EvidenceNormalizer
from pmagent.knowledge.corpus import DocCorpus
from pmagent.knowledge.index import (
    CodeIndex,
    EmbeddingMeta,
    IndexBuilder,
    pack_index,
    unpack_index,
)
from pmagent.knowledge.retrieval import HybridRetriever
from pmagent.memory import CaseFeedback, MemoryStore, RunEventLog
from pmagent.safety import ReplayEngine
from pmagent.store import ArtifactStore

NOT_IMPLEMENTED = "该命令尚未实现（当前进度：P0 骨架阶段）"

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="花瓣地图渲染引擎 SDK 智能问答与问题定位 Agent",
)
config_app = typer.Typer(help="配置管理")
auth_app = typer.Typer(help="身份与账号管理")
user_app = typer.Typer(help="用户管理（仅管理员）")
index_app = typer.Typer(help="索引管理")
runs_app = typer.Typer(help="运行记录")
memory_app = typer.Typer(help="长期记忆案例")
patch_app = typer.Typer(help="补丁建议")
eval_app = typer.Typer(help="离线评测与回放")
app.add_typer(config_app, name="config")
app.add_typer(auth_app, name="auth")
app.add_typer(index_app, name="index")
app.add_typer(runs_app, name="runs")
app.add_typer(memory_app, name="memory")
app.add_typer(patch_app, name="patch")
app.add_typer(eval_app, name="eval")
auth_app.add_typer(user_app, name="user")

console = Console()
err_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        help="显示版本号",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """花瓣地图渲染引擎 SDK 智能问答与问题定位 Agent。"""


def _not_implemented() -> None:
    raise PmagentError(NOT_IMPLEMENTED, hint="请查看 docs/cli.md 了解已实现能力")


def _store() -> UserStore:
    return UserStore()


def _session() -> SessionManager:
    return SessionManager(_store())


def _require(permission: Permission | None = None) -> User:
    """校验登录态与命令所需权限；不满足时以退出码 6 结束。"""
    user = _session().require_user()
    if permission is None:
        return user
    decision = PolicyEngine().authorize(user, f"cli:{permission}", permission=permission)
    if decision.decision != "allow":
        raise PermissionDenied(explain_denial(decision))
    return user


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@config_app.command("init")
def config_init(
    path: str | None = typer.Option(None, "--path", help="配置文件路径"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的配置"),
) -> None:
    """生成默认配置文件。"""
    target = config_path(path)
    if target.exists() and not force:
        console.print(f"配置文件已存在：{target}（使用 --force 覆盖）")
        return
    if target.exists() and force:
        target.unlink()
    write_default_config(target)
    console.print(f"已生成配置文件：{target}")


@config_app.command("show")
def config_show(
    path: str | None = typer.Option(None, "--path", help="配置文件路径"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """显示生效配置（密钥仅显示是否已设置）。"""
    cfg = load_config(path)
    view = redacted_view(cfg)
    if as_json:
        console.print_json(json.dumps(view, ensure_ascii=False))
        return
    table = Table(title="生效配置", show_header=True, header_style="bold")
    table.add_column("配置项")
    table.add_column("值")
    for section, values in view.items():
        if isinstance(values, dict):
            for key, value in values.items():
                table.add_row(f"{section}.{key}", str(value))
        else:
            table.add_row(section, str(values))
    console.print(table)


# ---------------------------------------------------------------------------
# 业务命令（骨架阶段占位）
# ---------------------------------------------------------------------------


def _index_builder(index_root: str | None = None) -> IndexBuilder:
    cfg = load_config()
    root = Path(index_root) if index_root else cfg.index.root
    return IndexBuilder(
        root,
        keep_snapshots=cfg.index.keep_snapshots,
        embedding=EmbeddingMeta(model_id=cfg.embedding.model, dim=cfg.embedding.dim),
    )


@index_app.command("build")
def index_build(
    repo: str = typer.Option(".", "--repo", help="仓库根目录"),
    commit: str | None = typer.Option(None, "--commit", help="指定 commit（默认 HEAD）"),
    incremental: bool = typer.Option(False, "--incremental", help="基于上一快照增量更新"),
    index_root: str | None = typer.Option(None, "--index-root", help="索引根目录"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """构建或更新索引（需要维护者及以上）。"""
    _require(Permission.INDEX_BUILD)
    builder = _index_builder(index_root)
    lock = SingleWriterLock(builder.index_root.parent / "locks" / "index.lock")
    lock.acquire(timeout=1.0)
    try:
        manifest = (
            builder.update(repo, commit) if incremental else builder.build(repo, commit)
        )
    finally:
        lock.release()
    if as_json:
        console.print_json(json.dumps(manifest.to_dict(), ensure_ascii=False))
        return
    console.print(
        f"索引完成：commit={manifest.commit[:12]} 文件={manifest.file_count} "
        f"符号={manifest.symbol_count} 块={manifest.chunk_count} 错误={manifest.errors}"
    )


@index_app.command("status")
def index_status(
    index_root: str | None = typer.Option(None, "--index-root", help="索引根目录"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出"),
) -> None:
    """查看索引状态（登录即可）。"""
    _require(Permission.INDEX_READ)
    manifest = _index_builder(index_root).current_manifest()
    if manifest is None:
        console.print("索引尚未构建")
        return
    if as_json:
        console.print_json(json.dumps(manifest.to_dict(), ensure_ascii=False))
        return
    table = Table(title="索引状态", show_header=True, header_style="bold")
    table.add_column("项")
    table.add_column("值")
    for key, value in manifest.to_dict().items():
        if key in {"extra", "embedding_model_version"}:
            continue
        table.add_row(key, str(value))
    console.print(table)


@index_app.command("push")
def index_push(
    to: str = typer.Option(..., "--to", help="制品输出目录"),
    index_root: str | None = typer.Option(None, "--index-root"),
) -> None:
    """打包当前索引为可分发的制品（需要维护者及以上）。"""
    _require(Permission.INDEX_BUILD)
    builder = _index_builder(index_root)
    manifest = builder.current_manifest()
    if manifest is None:
        raise PmagentError("索引尚未构建")
    archive, digest = pack_index(builder.index_root, manifest.commit, to)
    console.print(f"已打包：{archive}（sha256 {digest[:12]}…）")


@index_app.command("pull")
def index_pull(
    source: str = typer.Option(..., "--from", help="制品路径"),
    index_root: str | None = typer.Option(None, "--index-root"),
) -> None:
    """从制品恢复索引（需要维护者及以上）。"""
    _require(Permission.INDEX_BUILD)
    builder = _index_builder(index_root)
    commit = unpack_index(source, builder.index_root)
    console.print(f"已恢复索引：{commit}")


@app.command("ask")
def ask_cmd(
    question: str | None = typer.Argument(None, help="问题内容（留空则仅维护会话）"),
    session: str | None = typer.Option(None, "--session", help="会话标识，用于跨进程续接"),
    sdk_version: str | None = typer.Option(None, "--sdk-version"),
    platform: str | None = typer.Option(None, "--platform"),
    corpus: str | None = typer.Option(None, "--corpus", help="文档语料库路径"),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="无法交互时退化为默认问答并标注未确认"
    ),
    offline: bool = typer.Option(
        False, "--offline", help="断网降级：仅规则与检索，不调用模型"
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """交互式或单次问答：意图识别 → 检索 → 引用校验。"""
    _require(Permission.QUERY_ASK)
    store = MemoryStore()
    try:
        session_id = session or uuid.uuid4().hex[:8]
        state = store.load_session(session_id) or SessionState(id=session_id)
        if sdk_version:
            state.slots.sdk_version = sdk_version
        if platform:
            state.slots.platform = platform
        if not question:
            store.save_session(state)
            payload = {
                "session": session_id,
                "sdk_version": state.slots.sdk_version,
                "platform": state.slots.platform,
                "resumed": session is not None,
            }
            if as_json:
                console.print_json(json.dumps(payload, ensure_ascii=False))
            else:
                console.print(
                    f"会话 {session_id}：版本={state.slots.sdk_version or '未知'} "
                    f"平台={state.slots.platform or '未知'}"
                )
            return

        dialogue = DialogueManager(
            state,
            classifier=IntentClassifier(
                interactive=not offline
                and not non_interactive
                and sys.stdin.isatty()
            ),
        )
        turn = dialogue.handle(question)
        store.save_session(state)
    finally:
        store.close()

    decision = turn.result.decision
    if turn.route == "clarify":
        raise InsufficientEvidence(
            f"意图不明确：{turn.clarification}",
            hint="补充版本、平台或技术材料后重试",
        )
    if turn.route == "diagnose":
        raise InsufficientEvidence(
            "已识别为故障排查，但问答通道没有可解析证据",
            hint="使用 pmagent diagnose 提供 --log / --stack / --crash",
        )

    corpus_path = Path(
        corpus or os.environ.get("PMAGENT_CORPUS", str(Path(".pmagent") / "corpus.db"))
    )
    if not corpus_path.exists():
        raise InsufficientEvidence(
            "未配置知识库",
            hint=f"用 --corpus 指定语料库，或设置 PMAGENT_CORPUS（当前 {corpus_path}）",
        )

    corpus_db = DocCorpus(corpus_path)
    try:
        retriever = HybridRetriever(doc_corpus=corpus_db)
        hits = retriever.search(
            question,
            intent=state.intent or "usage",
            version=state.slots.sdk_version,
            limit=5,
        )
        answer = AnswerComposer().compose(question, hits)
        CitationValidator(doc_corpus=corpus_db).validate(answer)
        if offline:
            answer.notes.append("离线模式：仅检索与规则，能力受限")
    finally:
        corpus_db.close()

    if not hits:
        raise InsufficientEvidence(
            "知识库中没有命中", hint="补充版本信息，或换个说法再试"
        )

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "intent": decision.model_dump(mode="json"),
                    "route": turn.route,
                    "conclusion": answer.conclusion,
                    "basis": answer.basis,
                    "citations": [
                        {"id": item.citation_id, "ref": item.ref, "kind": item.kind}
                        for item in answer.citations
                    ],
                    "confidence": answer.confidence,
                    "notes": answer.notes,
                },
                ensure_ascii=False,
            )
        )
        return

    console.print(f"[bold]结论[/bold] {answer.conclusion}")
    for line in answer.basis:
        console.print(f"  {line}")
    for example in answer.examples:
        console.print(example)
    for note in answer.notes:
        console.print(f"[yellow]注意[/yellow] {note}")
    console.print(f"[dim]意图：{decision.primary}（{decision.channel}）[/dim]")


@app.command("diagnose")
def diagnose_cmd(
    query: str = typer.Argument("", help="现象描述"),
    log: list[str] = typer.Option(None, "--log", help="日志文件，可重复"),
    stack: list[str] = typer.Option(None, "--stack", help="堆栈文件，可重复"),
    crash: str | None = typer.Option(None, "--crash", help="崩溃转储或文本报告"),
    shot: list[str] = typer.Option(None, "--shot", help="截图，可重复"),
    repro: str | None = typer.Option(None, "--repro", help="复现工程目录"),
    sdk_version: str | None = typer.Option(None, "--version", help="SDK 版本"),
    platform: str | None = typer.Option(None, "--platform"),
    backend: str | None = typer.Option(None, "--backend", help="渲染后端"),
    repo: str = typer.Option(".", "--repo", help="引擎仓库根目录"),
    index_root: str | None = typer.Option(None, "--index-root", help="索引根目录"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """问题定位：解析证据并检查充分性。"""
    _require(Permission.DIAGNOSE_RUN)
    normalized = EvidenceNormalizer().build(
        query,
        logs=log,
        stacks=stack,
        crash=crash,
        shots=shot,
        repro=repro,
        product=Product(
            sdk_version=sdk_version, platform=platform, render_backend=backend
        ),
    )
    context = normalized.context
    summary = {
        "problem_id": context.id,
        "symptoms": [symptom.description for symptom in context.symptoms],
        "logs": len(context.logs),
        "stacks": len(context.stacks),
        "crash": context.crash is not None,
        "visuals": len(context.visuals),
        "repro": context.repro is not None,
        "missing": [
            {"needed": item.needed, "purpose": item.purpose} for item in normalized.missing
        ],
    }
    if as_json:
        if normalized.missing:
            console.print_json(json.dumps(summary, ensure_ascii=False))
    else:
        table = Table(title="问题上下文", show_header=True, header_style="bold")
        table.add_column("证据")
        table.add_column("数量/状态")
        table.add_row("日志", str(len(context.logs)))
        table.add_row("堆栈", str(len(context.stacks)))
        table.add_row("崩溃", "有" if context.crash else "无")
        table.add_row("截图", str(len(context.visuals)))
        table.add_row("复现工程", "有" if context.repro else "无")
        console.print(table)
        for item in normalized.missing:
            console.print(f"[yellow]缺少[/yellow] {item.needed}（{item.purpose}）")

    if normalized.missing:
        raise InsufficientEvidence(
            f"证据不足，缺少 {len(normalized.missing)} 项",
            hint="按上面列出的补充材料清单准备后重试",
        )

    cfg = load_config()
    root = Path(index_root) if index_root else cfg.index.root
    manifest = IndexBuilder(root).current_manifest()
    if manifest is None:
        raise LocalizationFailed(
            "索引尚未构建，无法定位",
            hint="先执行 pmagent index build --repo <path>",
        )

    index = CodeIndex.open(
        root,
        expected_embedding=EmbeddingMeta(model_id=cfg.embedding.model, dim=cfg.embedding.dim),
    )
    try:
        agent = DiagnosisAgent(
            index,
            retriever=HybridRetriever(code_index=index),
            repo_root=repo,
            index_version=manifest.commit,
        )
        report = agent.run(normalized.context)
    finally:
        index.close()

    store = ArtifactStore()
    store.ensure_layout()
    report_path = store.report_path(normalized.context.id, "md")
    report_path.write_text(render_markdown(report, normalized.context), encoding="utf-8")
    (store.report_path(normalized.context.id, "json")).write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )

    if as_json:
        console.print_json(report.model_dump_json())
        return

    top = report.hypotheses[0]
    console.print(
        f"[bold]定位报告[/bold] 阶段={report.triage.stage} "
        f"不确定度={report.uncertainty.level}"
    )
    for hypothesis in report.hypotheses:
        location = hypothesis.code_locations[0] if hypothesis.code_locations else None
        where = f"{location.file}:{location.line_start}" if location else "无位置"
        console.print(
            f"  {hypothesis.id} 置信度={hypothesis.confidence:.2f} {where} "
            f"{hypothesis.statement[:60]}"
        )
    console.print(f"[dim]报告：{report_path}[/dim]")
    console.print(f"[dim]首要结论：{top.id}[/dim]")


@patch_app.command("generate")
def patch_generate(
    report: str = typer.Option(..., "--report", help="定位报告 JSON 路径"),
    repo: str = typer.Option(".", "--repo", help="引擎仓库根目录"),
    index_root: str | None = typer.Option(None, "--index-root"),
    count: int = typer.Option(2, "--count", help="候选数量"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """基于定位报告生成补丁工件（需要维护者及以上）。"""
    _require(Permission.PATCH_SUGGEST)
    path = Path(report)
    if not path.exists():
        raise UsageError(f"定位报告不存在：{path}")
    localization = LocalizationReport.model_validate_json(path.read_text(encoding="utf-8"))
    if not localization.hypotheses:
        raise PmagentError("定位报告中没有假设，无法生成补丁")
    hypothesis = next(
        (
            item
            for item in localization.hypotheses
            if item.id == localization.top_root_cause_ref
        ),
        localization.hypotheses[0],
    )

    cfg = load_config()
    from pmagent.gateway import DeepSeekGateway

    gateway = DeepSeekGateway(cfg.model)

    root = Path(index_root) if index_root else cfg.index.root
    index = CodeIndex.open(
        root,
        expected_embedding=EmbeddingMeta(model_id=cfg.embedding.model, dim=cfg.embedding.dim),
    )
    try:
        author = PatchAuthor(
            index, repo_root=repo, generator=LLMPatchGenerator(gateway)
        )
        artifacts = author.propose(hypothesis, run_id=path.stem, count=count)
    finally:
        index.close()

    store = PatchStore()
    for artifact in artifacts:
        store.save(artifact, run_id=path.stem)

    if as_json:
        console.print_json(
            json.dumps([item.model_dump(mode="json") for item in artifacts], ensure_ascii=False)
        )
        return
    for artifact in artifacts:
        console.print(
            f"[bold]{artifact.id}[/bold] 文件={len(artifact.files_changed)} "
            f"自审={artifact.rationale[1] if len(artifact.rationale) > 1 else '-'}"
        )
        for note in artifact.rationale[2:]:
            console.print(f"  - {note}")
    console.print(f"[dim]工件目录：artifacts/patches/{path.stem}/[/dim]")


@patch_app.command("list")
def patch_list(
    run_id: str = typer.Option(..., "--run-id"),
) -> None:
    """列出某次运行的补丁工件。"""
    _require(Permission.PATCH_SUGGEST)
    artifacts = PatchStore().list(run_id)
    if not artifacts:
        console.print("暂无补丁工件")
        return
    table = Table(title=f"补丁 {run_id}", show_header=True, header_style="bold")
    table.add_column("id")
    table.add_column("文件数")
    table.add_column("风险")
    for artifact in artifacts:
        table.add_row(
            artifact.id,
            str(len(artifact.files_changed)),
            "高" if artifact.risk.note else "低",
        )
    console.print(table)


@patch_app.command("apply")
def patch_apply(
    run_id: str = typer.Argument(...),
    patch_id: str = typer.Argument(...),
    repo: str = typer.Option(".", "--repo"),
    yes: bool = typer.Option(False, "--yes", help="显式确认应用到工作区"),
) -> None:
    """把补丁应用到工作区（需 --yes 二次确认）。"""
    _require(Permission.PATCH_APPLY)
    artifact, diff_path = PatchStore().load(run_id, patch_id)
    info = apply_patch(repo, diff_path, confirmed=yes)
    console.print(
        f"已应用 {artifact.id}；快照 {info['snapshot_id']}，"
        f"可用 pmagent patch revert {run_id} {patch_id} 回滚"
    )


@patch_app.command("revert")
def patch_revert(
    run_id: str = typer.Argument(...),
    patch_id: str = typer.Argument(...),
    repo: str = typer.Option(".", "--repo"),
) -> None:
    """回滚已应用的补丁。"""
    _require(Permission.PATCH_APPLY)
    _artifact, diff_path = PatchStore().load(run_id, patch_id)
    revert_patch(repo, diff_path)
    console.print(f"已回滚 {patch_id}")


@app.command("explain")
def explain_cmd() -> None:
    """解释指定符号或代码位置。"""
    _not_implemented()


@runs_app.command("list")
def runs_list(
    limit: int = typer.Option(10, "--limit", help="最多显示条数"),
) -> None:
    """列出最近的运行记录。"""
    _require(Permission.QUERY_ASK)
    store = MemoryStore()
    try:
        runs = store.list_runs(limit=limit)
    finally:
        store.close()
    if not runs:
        console.print("暂无运行记录")
        return
    table = Table(title="运行记录", show_header=True, header_style="bold")
    table.add_column("run_id")
    table.add_column("类型")
    table.add_column("状态")
    table.add_column("开始时间")
    for run in runs:
        table.add_row(run["run_id"], run["kind"], run["status"], run["started_at"])
    console.print(table)


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(..., help="运行标识"),
    from_stage: str | None = typer.Option(None, "--from-stage", help="从指定阶段重跑"),
) -> None:
    """查看某次运行的阶段、进度与索引版本。"""
    _require(Permission.QUERY_ASK)
    store = MemoryStore()
    try:
        run = store.load_run(run_id)
    finally:
        store.close()
    if run is None:
        raise PmagentError(f"运行不存在：{run_id}")

    log = RunEventLog(Path(".pmagent") / "runs" / run_id)
    events = log.read()
    table = Table(title=f"运行 {run_id}", show_header=True, header_style="bold")
    table.add_column("seq")
    table.add_column("类型")
    table.add_column("阶段/状态")
    for event in events:
        detail = str(event.payload.get("stage") or event.payload.get("status") or "")
        table.add_row(str(event.seq), event.type, detail)
    console.print(table)
    console.print(f"状态：{run['status']}，索引版本：{run['index_version'] or '未知'}")

    progress_events = [event for event in events if event.type == "progress"]
    if progress_events:
        console.print(f"进度快照：{len(progress_events)} 条")
        for event in progress_events[-3:]:
            console.print(
                f"  #{event.payload.get('call_index')} "
                f"{event.payload.get('stage')} "
                f"verdict={event.payload.get('verdict')} "
                f"action={event.payload.get('action')} "
                f"{event.payload.get('reason', '')}"
            )

    manifest = _index_builder().current_manifest()
    if (
        manifest is not None
        and run["index_version"]
        and manifest.commit[:12] != run["index_version"][:12]
    ):
        console.print(
            "[yellow]警告[/yellow] 当前索引版本与运行记录不一致，历史定位可能失效；"
            "可使用 --from-stage 重跑"
        )
    if from_stage:
        stages = [event.payload.get("stage") for event in events if event.type == "stage_started"]
        stages = [stage for stage in stages if isinstance(stage, str)]
        index = stages.index(from_stage) if from_stage in stages else 0
        console.print(f"将从阶段 {from_stage} 开始重跑：{stages[index:]}")


@app.command("review")
def review_cmd(
    run_id: str = typer.Option(..., "--run-id", help="运行标识"),
    wrong: bool = typer.Option(False, "--wrong", help="标记结论错误"),
    correct: bool = typer.Option(False, "--correct", help="标记结论正确"),
    reason: str | None = typer.Option(None, "--reason", help="纠正原因"),
) -> None:
    """人工纠正结论：错误结论下架并沉淀为反例。"""
    reviewer = _require(Permission.CASE_CONFIRM)
    if not wrong and not correct:
        raise UsageError("需要指定 --wrong 或 --correct")
    store = MemoryStore()
    try:
        run = store.load_run(run_id)
        if run is None:
            raise PmagentError(f"运行不存在：{run_id}")
        store.save_run(
            run_id,
            kind=run["kind"],
            status="wrong" if wrong else "correct",
            index_version=run["index_version"],
            state=run["state"],
            finished_at=None,
        )
        if wrong:
            CaseFeedback(store).reject_patch(f"run:{run_id}", reason or "未说明")
    finally:
        store.close()
    verdict = "错误" if wrong else "正确"
    console.print(f"已记录 {run_id} 的评审结果（{reviewer.username}）：{verdict}")


@memory_app.command("list")
def memory_list(
    limit: int = typer.Option(20, "--limit"),
) -> None:
    """列出已确认的长期案例。"""
    _require(Permission.QUERY_ASK)
    store = MemoryStore()
    try:
        cases = CaseFeedback(store).examples(limit=limit)
    finally:
        store.close()
    if not cases:
        console.print("暂无案例")
        return
    table = Table(title="长期案例", show_header=True, header_style="bold")
    table.add_column("case_id")
    table.add_column("症状")
    table.add_column("根因")
    table.add_column("置信度")
    for case in cases:
        table.add_row(
            case.case_id,
            case.symptom[:40],
            case.root_cause[:40],
            f"{case.confidence:.2f}",
        )
    console.print(table)


@memory_app.command("forget")
def memory_forget(case_id: str = typer.Argument(..., help="案例标识")) -> None:
    """下架错误案例。"""
    _require(Permission.CASE_CONFIRM)
    store = MemoryStore()
    try:
        CaseFeedback(store).expire(case_id)
    finally:
        store.close()
    console.print(f"已下架案例：{case_id}")


@auth_app.command("init")
def auth_init(
    username: str = typer.Option("admin", "--username", help="管理员用户名"),
    password: str | None = typer.Option(None, "--password", help="密码（CI 用；留空则交互输入）"),
) -> None:
    """首次初始化管理员账号（只允许一次）。"""
    store = _store()
    if store.count_admins() > 0:
        raise PermissionDenied(
            "已存在管理员账号，拒绝重复初始化", hint="使用 pmagent auth user add 新增用户"
        )
    secret = password or typer.prompt("设置管理员密码", hide_input=True, confirmation_prompt=True)
    if len(secret) < 8:
        raise UsageError("密码长度至少 8 位")
    store.create_user(username, PasswordHasherService().hash(secret), [Role.ADMIN])
    console.print(f"已创建管理员：{username}")


@auth_app.command("login")
def auth_login(
    username: str = typer.Option(..., "--username", prompt="用户名"),
    password: str | None = typer.Option(None, "--password", help="密码（CI 用；留空则交互输入）"),
) -> None:
    """登录并缓存 12 小时令牌。"""
    secret = password or typer.prompt("密码", hide_input=True)
    _session().login(username, secret)
    console.print(f"已登录：{username}（令牌有效期 12 小时）")


@auth_app.command("logout")
def auth_logout() -> None:
    """退出登录并清除本地令牌。"""
    _session().logout()
    console.print("已退出登录")


@auth_app.command("whoami")
def auth_whoami() -> None:
    """显示当前登录用户与角色。"""
    user = _require()
    roles = ", ".join(user.roles) or "无"
    console.print(f"{user.username}（{roles}）")


@user_app.command("add")
def user_add(
    username: str = typer.Option(..., "--username"),
    role: str = typer.Option("developer", "--role", help="admin/maintainer/developer/viewer"),
    password: str | None = typer.Option(None, "--password"),
) -> None:
    """新增用户（仅管理员）。"""
    _require(Permission.USER_MANAGE)
    try:
        roles = [Role(role)]
    except ValueError as exc:
        raise UsageError(f"未知角色：{role}") from exc
    secret = password or typer.prompt("设置密码", hide_input=True, confirmation_prompt=True)
    if len(secret) < 8:
        raise UsageError("密码长度至少 8 位")
    _store().create_user(username, PasswordHasherService().hash(secret), roles)
    console.print(f"已新增用户：{username}（{role}）")


@user_app.command("role")
def user_role(
    username: str = typer.Option(..., "--username"),
    roles: str = typer.Option(..., "--roles", help="逗号分隔，如 developer,viewer"),
) -> None:
    """调整用户角色（仅管理员）。"""
    _require(Permission.USER_MANAGE)
    try:
        parsed = [Role(item.strip()) for item in roles.split(",") if item.strip()]
    except ValueError as exc:
        raise UsageError(f"未知角色：{roles}") from exc
    if not parsed:
        raise UsageError("至少指定一个角色")
    _store().set_roles(username, parsed)
    console.print(f"已更新 {username} 的角色：{', '.join(r.value for r in parsed)}")


@user_app.command("disable")
def user_disable(username: str = typer.Option(..., "--username")) -> None:
    """禁用用户（仅管理员）。"""
    _require(Permission.USER_MANAGE)
    _store().set_status(username, "disabled")
    console.print(f"已禁用用户：{username}")


@eval_app.command("run")
def eval_run(
    dataset: str = typer.Option("data/eval/seed.jsonl", "--dataset"),
    mode: str = typer.Option("retrieval", "--mode", help="retrieval 或 diagnosis"),
    index_root: str | None = typer.Option(None, "--index-root"),
    corpus: str | None = typer.Option(None, "--corpus", help="文档语料库"),
    limit: int | None = typer.Option(None, "--limit"),
    out: str | None = typer.Option(None, "--out"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """跑离线评测并写出指标报告。"""
    _require(Permission.EVAL_RUN)
    cases = load_cases(dataset)
    cfg = load_config()
    root = Path(index_root) if index_root else cfg.index.root
    index = None
    manifest = IndexBuilder(root).current_manifest()
    if manifest is not None:
        index = CodeIndex.open(
            root,
            expected_embedding=EmbeddingMeta(
                model_id=cfg.embedding.model, dim=cfg.embedding.dim
            ),
        )
    doc_corpus = DocCorpus(corpus) if corpus and Path(corpus).exists() else None
    try:
        retriever = HybridRetriever(code_index=index, doc_corpus=doc_corpus)
        agent = DiagnosisAgent(index) if (mode == "diagnosis" and index) else None
        report = EvalRunner(
            code_index=index, retriever=retriever, agent=agent
        ).run(cases, mode=mode, limit=limit)
    finally:
        if index is not None:
            index.close()
        if doc_corpus is not None:
            doc_corpus.close()

    report["dataset"] = str(dataset)
    report["mode"] = mode
    path = Path(out) if out else Path("artifacts") / "eval" / f"{int(time.time())}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if as_json:
        console.print_json(json.dumps(report, ensure_ascii=False))
        return
    console.print(
        f"样本 {report['sample_size']}"
        f"（代码 {report['evaluated_code']} / 文档 {report['evaluated_docs']}）"
    )
    for key, value in report["metrics"].items():
        console.print(f"  {key}: {value}")
    console.print(f"[dim]报告：{path}[/dim]")


@eval_app.command("compare")
def eval_compare(
    baseline: str = typer.Option(..., "--baseline"),
    current: str = typer.Option(..., "--current"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """对比两次评测报告，列出退化用例。"""
    _require(Permission.EVAL_RUN)
    base = json.loads(Path(baseline).read_text(encoding="utf-8"))
    cur = json.loads(Path(current).read_text(encoding="utf-8"))
    diff = compare_reports(base, cur)
    if as_json:
        console.print_json(json.dumps(diff, ensure_ascii=False))
        return
    for metric, values in diff["deltas"].items():
        console.print(
            f"{metric}: {values['baseline']} → {values['current']} ({values['delta']})"
        )
    degraded = diff["degraded_cases"]
    console.print(f"退化用例：{len(degraded)}")
    for case_id in degraded:
        console.print(f"  - {case_id}")


@eval_app.command("calibrate")
def eval_calibrate(
    report: str = typer.Option(..., "--report"),
    buckets: int = typer.Option(5, "--buckets"),
) -> None:
    """置信度校准曲线与 ECE。"""
    _require(Permission.EVAL_RUN)
    data = json.loads(Path(report).read_text(encoding="utf-8"))
    pairs = [
        (float(row["confidence"]), bool(row["hit"]))
        for row in data.get("cases", [])
        if row.get("confidence") is not None
    ]
    rows, ece = calibration_buckets(pairs, buckets)
    table = Table(title="置信度校准", show_header=True, header_style="bold")
    for column in ("区间", "样本", "平均置信度", "实际准确率"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            row["bucket"],
            str(row.get("n", 0)),
            str(row.get("confidence", "-")),
            str(row.get("accuracy", "-")),
        )
    console.print(table)
    console.print(f"ECE = {ece}（越小越接近校准）")


@eval_app.command("agreement")
def eval_agreement(
    a: str = typer.Option(..., "--a", help="标注员 A 的 JSONL"),
    b: str = typer.Option(..., "--b", help="标注员 B 的 JSONL"),
    threshold: float = typer.Option(0.6, "--threshold"),
) -> None:
    """双人标注一致性（Cohen's kappa）。"""
    _require(Permission.EVAL_RUN)

    def _labels(path: str) -> dict[str, str]:
        labels: dict[str, str] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            labels[str(item["id"])] = str(item["label"])
        return labels

    kappa = cohen_kappa(_labels(a), _labels(b))
    console.print(f"Cohen's kappa = {kappa}（阈值 {threshold}）")
    if kappa < threshold:
        console.print("[yellow]低于阈值[/yellow] 触发第三人或组长复核")
@eval_app.command("review")
def eval_review(
    report: str = typer.Option(..., "--report"),
    case_id: str = typer.Option(..., "--case-id"),
    score: int = typer.Option(..., "--score", min=1, max=5),
    accept: bool = typer.Option(False, "--accept", help="确认案例进入 L3 案例库"),
    reviewer: str = typer.Option("human", "--reviewer"),
    root_cause: str = typer.Option("", "--root-cause"),
) -> None:
    """人工打分回写；确认的案例进入案例库。"""
    _require(Permission.EVAL_RUN)
    data = json.loads(Path(report).read_text(encoding="utf-8"))
    row = next((item for item in data.get("cases", []) if item.get("id") == case_id), None)
    if row is None:
        raise UsageError(f"报告中不存在用例：{case_id}")
    entry = {
        "case_id": case_id,
        "score": score,
        "accepted": accept,
        "reviewer": reviewer,
        "symptom": row.get("query", ""),
        "root_cause": root_cause,
        "root_cause_file": (row.get("expected_files") or [None])[0],
        "evidence_refs": [],
    }
    path = append_review(entry)
    if accept:
        store = MemoryStore()
        try:
            case = admit_case(entry, CaseFeedback(store), reviewer=reviewer)
        finally:
            store.close()
        console.print(f"已进入案例库：{case.case_id}")
    console.print(f"评分写入 {path}")


@eval_app.command("replay")
def eval_replay(
    run_id: str = typer.Argument(..., help="运行标识"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """从工件与事件日志回放某次运行的结论。"""
    _require(Permission.QUERY_ASK)
    result = ReplayEngine().replay(run_id)
    top = result.report.hypotheses[0] if result.report.hypotheses else None
    if as_json:
        console.print_json(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "digest": result.digest,
                    "consistent": result.consistent,
                    "issues": result.issues,
                    "top": {
                        "id": top.id,
                        "statement": top.statement,
                        "confidence": top.confidence,
                    }
                    if top
                    else None,
                },
                ensure_ascii=False,
            )
        )
        return
    console.print(
        f"回放 {result.run_id}：摘要 {result.digest}，"
        f"一致性 {'通过' if result.consistent else '有差异'}"
    )
    if top is not None:
        console.print(f"首要结论 {top.id} 置信度 {top.confidence:.2f}")
    for issue in result.issues:
        console.print(f"[yellow]注意[/yellow] {issue}")


# ---------------------------------------------------------------------------
# 测试钩子：仅在 PMAGENT_TEST_HOOKS=1 时可用，用于验证退出码映射
# ---------------------------------------------------------------------------


_FAULTS: dict[int, type[PmagentError]] = {
    3: InsufficientEvidence,
    4: LocalizationFailed,
    5: BudgetExceeded,
    6: PermissionDenied,
    130: Interrupted,
}


@app.command("_raise", hidden=True)
def _raise_cmd(code: int = typer.Option(..., "--code")) -> None:
    if os.environ.get("PMAGENT_TEST_HOOKS") != "1":
        raise UsageError("未知命令")
    exc_type = _FAULTS.get(code)
    if exc_type is None:
        raise UsageError(f"不支持的故障码: {code}")
    raise exc_type(f"simulated fault {code}")


# ---------------------------------------------------------------------------
# 退出码调度
# ---------------------------------------------------------------------------


def dispatch(argv: list[str] | None = None) -> int:
    """执行 CLI 并返回进程退出码，便于测试与嵌入。"""
    try:
        app(args=list(argv) if argv is not None else None, standalone_mode=False)
        return int(ExitCode.OK)
    except typer.Exit as exc:
        return int(exc.exit_code)
    except PmagentError as exc:
        err_console.print(f"[red]错误[/red] {exc.message}")
        if exc.hint:
            err_console.print(f"[dim]建议：{exc.hint}[/dim]")
        return int(exc.exit_code)
    except KeyboardInterrupt:
        err_console.print("[yellow]已中断[/yellow]")
        return int(ExitCode.INTERRUPTED)
    except Exception as exc:  # noqa: BLE001 - 参数错误与内部错误兜底
        # Typer 的参数解析错误带有 exit_code=2，与内部错误区分
        if getattr(exc, "exit_code", None) == int(ExitCode.USAGE):
            message = getattr(exc, "format_message", None)
            err_console.print(f"[red]参数错误[/red] {message() if message else exc}")
            return int(ExitCode.USAGE)
        err_console.print(f"[red]内部错误[/red] {exc.__class__.__name__}: {exc}")
        return int(ExitCode.INTERNAL)


def main() -> None:
    sys.exit(dispatch())


__all__ = ["app", "dispatch", "main"]
