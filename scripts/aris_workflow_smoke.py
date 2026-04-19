from __future__ import annotations

import json
import sys
import tempfile
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.routers import agent_workspace as agent_workspace_router
from apps.api.routers import projects as projects_router
from packages.ai.project.amadeus_compat import build_run_directory, build_run_log_path
from packages.ai.project import multi_agent_runner as project_multi_agent_runner_module
from packages.ai.project import workflow_runner as project_workflow_runner_module
from packages.ai.project.multi_agent_runner import run_multi_agent_project_workflow
from packages.ai.project.workflow_runner import run_project_workflow
from packages.ai.project.workflow_catalog import (
    build_run_orchestration,
    build_stage_trace,
    is_active_project_workflow,
)
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.domain.schemas import PaperCreate
from packages.integrations import llm_client as llm_client_module
from packages.integrations.llm_client import LLMResult
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import PaperRepository, ProjectRepository


@dataclass
class SmokeResult:
    workflow: str
    run_id: str
    status: str
    report_title: str
    excerpt: str
    artifacts: list[str]
    details: dict[str, Any] | None = None


@dataclass
class SmokeFailure:
    workflow: str
    error: str


def _configure_test_db() -> None:
    import packages.storage.models  # noqa: F401

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db.SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _prepare_local_workspace(workspace: Path) -> None:
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "scripts").mkdir(parents=True, exist_ok=True)
    (workspace / "notes").mkdir(parents=True, exist_ok=True)
    (workspace / "outputs").mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("# ARIS Smoke Workspace\n", encoding="utf-8")
    (workspace / "src" / "main.py").write_text("print('aris smoke workspace')\n", encoding="utf-8")
    (workspace / "scripts" / "run_smoke.py").write_text("print('smoke run ok')\n", encoding="utf-8")
    (workspace / "notes" / "context.md").write_text("AnchorCoT smoke context\n", encoding="utf-8")


def _attach_project_fixtures(
    repo: ProjectRepository,
    paper_repo: PaperRepository,
    *,
    project_id: str,
    workflow_type: ProjectWorkflowType,
    project_name: str,
    workdir: Path | None,
) -> None:
    digest = abs(hash((workflow_type.value, project_name)))
    suffixes = ("a", "b")
    for index, suffix in enumerate(suffixes, start=1):
        paper = paper_repo.upsert_paper(
            PaperCreate(
                arxiv_id=f"2603.{digest % 70000 + 10000 + index}",
                title=f"{workflow_type.value} smoke paper {suffix.upper()}",
                abstract=(
                    f"A deterministic smoke abstract for {workflow_type.value}. "
                    "It provides enough context for ARIS workflow validation."
                ),
                metadata={},
            )
        )
        repo.add_paper_to_project(project_id=project_id, paper_id=paper.id)
    if workdir is not None:
        repo.create_repo(
            project_id=project_id,
            repo_url=f"https://github.com/example/{workflow_type.value}.git",
            local_path=str(workdir),
            is_workdir_repo=True,
        )


def _seed_run(
    *,
    workflow_type: ProjectWorkflowType,
    project_name: str,
    workdir: Path | None,
    prompt: str,
    workspace_server_id: str | None = None,
    remote_workdir: str | None = None,
    metadata: dict[str, Any] | None = None,
    max_iterations: int | None = None,
) -> str:
    with db.session_scope() as session:
        repo = ProjectRepository(session)
        paper_repo = PaperRepository(session)
        project = repo.create_project(
            name=project_name,
            description=f"{workflow_type.value} smoke",
            workdir=str(workdir) if workdir else None,
            workspace_server_id=workspace_server_id,
            remote_workdir=remote_workdir,
        )
        target = repo.ensure_default_target(project.id)
        assert target is not None
        orchestration = build_run_orchestration(
            workflow_type,
            None,
            target_id=target.id,
            workspace_server_id=target.workspace_server_id,
            reset_stage_status=True,
        )
        run = repo.create_run(
            project_id=project.id,
            target_id=target.id,
            workflow_type=workflow_type,
            title=f"{project_name} {workflow_type.value}",
            prompt=prompt,
            status=ProjectRunStatus.queued,
            active_phase="queued",
            summary="queued",
            workspace_server_id=target.workspace_server_id,
            workdir=target.workdir,
            remote_workdir=target.remote_workdir,
            executor_model="smoke-executor",
            reviewer_model="smoke-reviewer",
            max_iterations=max_iterations,
            metadata={
                "orchestration": orchestration,
                "stage_trace": build_stage_trace(orchestration, reset=True),
                **(metadata or {}),
            },
        )
        _attach_project_fixtures(
            repo,
            paper_repo,
            project_id=project.id,
            workflow_type=workflow_type,
            project_name=project_name,
            workdir=workdir,
        )
        is_remote = bool(target.workspace_server_id)
        base_directory = target.remote_workdir if is_remote else target.workdir
        run_directory = build_run_directory(base_directory, run.id, remote=is_remote)
        log_path = build_run_log_path(run_directory, remote=is_remote)
        repo.update_run(run.id, run_directory=run_directory, log_path=log_path)
        return run.id


@contextmanager
def _patch_llm_summarize(fake: Callable[..., LLMResult]):
    original = llm_client_module.LLMClient.summarize_text
    llm_client_module.LLMClient.summarize_text = fake
    try:
        yield
    finally:
        llm_client_module.LLMClient.summarize_text = original


@contextmanager
def _patch_llm_complete_json(fake: Callable[..., LLMResult]):
    original = llm_client_module.LLMClient.complete_json
    llm_client_module.LLMClient.complete_json = fake
    try:
        yield
    finally:
        llm_client_module.LLMClient.complete_json = original


@contextmanager
def _patch_assignments(assignments: list[tuple[object, str, Any]]):
    originals: list[tuple[object, str, Any]] = []
    for target, attr, value in assignments:
        originals.append((target, attr, getattr(target, attr)))
    try:
        for target, attr, value in assignments:
            setattr(target, attr, value)
        yield
    finally:
        for target, attr, value in reversed(originals):
            setattr(target, attr, value)


def _load_run_summary(run_id: str) -> dict[str, Any]:
    with db.session_scope() as session:
        run = ProjectRepository(session).get_run(run_id)
        assert run is not None
        return projects_router._serialize_run_summary(run)


def _preview_primary_report(summary: dict[str, Any]) -> str:
    artifact_refs = list(summary.get("artifact_refs") or [])
    for item in artifact_refs:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        relative_path = str(item.get("relative_path") or "").strip()
        if not path or not relative_path:
            continue
        if not path.lower().endswith(".md"):
            continue
        content = "# placeholder artifact\n"
        if Path(path).exists():
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        return agent_workspace_router._resolve_workspace_preview_content(
            path,
            relative_path,
            content,
        )
    raise RuntimeError("未找到可预览的主报告文件")


def _artifact_labels(summary: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for item in summary.get("artifact_refs") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("relative_path") or item.get("path") or "").strip()
        if path:
            labels.append(path)
    return labels


def _assert_contains(text: str, expected: str, *, context: str) -> None:
    if expected not in text:
        raise AssertionError(f"{context} 缺少预期片段: {expected}")


def _assert_artifacts_include(summary: dict[str, Any], expected: list[str], *, context: str) -> None:
    labels = _artifact_labels(summary)
    for relative_path in expected:
        if relative_path not in labels:
            raise AssertionError(f"{context} 缺少产物: {relative_path}")


def _artifact_path(summary: dict[str, Any], relative_path: str) -> Path:
    for item in summary.get("artifact_refs") or []:
        if not isinstance(item, dict):
            continue
        current = str(item.get("relative_path") or "").strip()
        path = str(item.get("path") or "").strip()
        if current == relative_path and path:
            return Path(path)
    raise AssertionError(f"未找到产物路径: {relative_path}")


def _run_literature_review_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "literature-review-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.literature_review,
        project_name="ARIS Literature Review Smoke",
        workdir=workspace,
        prompt="围绕 AnchorCoT 的过程奖励与锚点对齐写一份结构化文献综述",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage != "project_literature_review":
            raise AssertionError(f"unexpected summarize stage: {stage}")
        return LLMResult(
            content=(
                "# 项目级文献综述\n\n"
                "## 主线\n"
                "- AnchorCoT 关注锚点驱动的推理稳定性。\n"
                "- 相关工作集中在过程奖励、轨迹筛选与长链路推理纠错。\n\n"
                "## 风险与机会\n"
                "- 当前最大风险是 evidence coverage 仍偏弱。\n"
                "- 可以补强 anchor ablation 与失败案例剖析。\n"
            )
        )

    with _patch_llm_summarize(_fake_summarize):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 文献综述报告", context="literature_review report")
    _assert_contains(preview, "AnchorCoT", context="literature_review report")
    _assert_artifacts_include(summary, ["reports/literature-review.md"], context="literature_review")

    with db.session_scope() as session:
        run = ProjectRepository(session).get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        if not metadata.get("generated_content_id"):
            raise AssertionError("literature_review smoke 未生成 generated_content_id")

    return SmokeResult(
        workflow=ProjectWorkflowType.literature_review.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 文献综述报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_local_experiment_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "run-experiment-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.run_experiment,
        project_name="ARIS Local Experiment Smoke",
        workdir=workspace,
        prompt="在本地工作区执行一个最小实验并汇总结果",
        metadata={
            "execution_command": "python ./scripts/run_smoke.py",
        },
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage != "project_run_experiment_summary":
            raise AssertionError(f"unexpected summarize stage: {stage}")
        return LLMResult(
            content=(
                "# 实验总结\n\n"
                "- 最小实验已执行成功。\n"
                "- 结果文件已写入 `outputs/results.json`。\n"
                "- 建议下一步扩展消融与误差分析。\n"
            )
        )

    def _fake_inspect_workspace(context, workspace_path_override=None):
        return {
            "workspace_path": str(workspace_path_override or workspace),
            "tree": "README.md\nsrc/\nscripts/\nnotes/\noutputs/\n",
            "files": [
                "README.md",
                "src/main.py",
                "scripts/run_smoke.py",
                "notes/context.md",
                "outputs/",
            ],
            "dirs": ["src", "scripts", "notes", "outputs"],
            "message": "workspace ready",
        }

    def _fake_run_workspace_command(context, command, *, timeout_sec, workspace_path_override=None):
        outputs_dir = Path(str(workspace_path_override or workspace)) / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        (outputs_dir / "results.json").write_text(
            json.dumps({"status": "completed", "accuracy": 0.88, "loss": 0.34}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {
            "command": command,
            "success": True,
            "exit_code": 0,
            "stdout": "smoke run ok\naccuracy=0.88",
            "stderr": "",
            "workspace_path": str(workspace_path_override or workspace),
        }

    assignments = [
        (llm_client_module.LLMClient, "summarize_text", _fake_summarize),
        (project_workflow_runner_module, "_inspect_workspace_payload", _fake_inspect_workspace),
        (project_workflow_runner_module, "_run_workspace_command_for_context", _fake_run_workspace_command),
    ]

    with _patch_assignments(assignments):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 实验运行报告", context="run_experiment report")
    _assert_contains(preview, "python ./scripts/run_smoke.py", context="run_experiment report")
    _assert_artifacts_include(summary, ["reports/experiment-summary.md"], context="run_experiment")
    if not (workspace / "outputs" / "results.json").exists():
        raise AssertionError("run_experiment smoke 未写出 outputs/results.json")

    return SmokeResult(
        workflow=ProjectWorkflowType.run_experiment.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 实验运行报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_experiment_audit_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "experiment-audit-workspace"
    (workspace / "results").mkdir(parents=True, exist_ok=True)
    (workspace / "configs").mkdir(parents=True, exist_ok=True)
    (workspace / "paper" / "sections").mkdir(parents=True, exist_ok=True)
    (workspace / "eval_metric.py").write_text(
        "from pathlib import Path\n"
        "\n"
        "def load_metrics():\n"
        "    return Path('results/metrics.json').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    (workspace / "results" / "metrics.json").write_text(
        json.dumps({"accuracy": 0.91, "normalized_score": 0.97}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (workspace / "EXPERIMENT_TRACKER.md").write_text(
        "# EXPERIMENT_TRACKER\n\n- main metric: accuracy=0.91\n- seeds: 1\n",
        encoding="utf-8",
    )
    (workspace / "paper" / "sections" / "experiments.tex").write_text(
        "\\section{Experiments}\nWe report 0.91 accuracy on the main setting.\n",
        encoding="utf-8",
    )
    (workspace / "configs" / "eval.yaml").write_text(
        "dataset: anchorcot-dev\nmetric: accuracy\nnormalization: per_task\n",
        encoding="utf-8",
    )

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.experiment_audit,
        project_name="ARIS Experiment Audit Smoke",
        workdir=workspace,
        prompt="审计 AnchorCoT 当前实验结果的真实性、归一化口径和证据链完整性",
    )

    def _fake_complete_json(
        self,
        prompt,
        stage,
        model_override=None,
        variant_override=None,
        max_tokens=None,
        max_retries=1,
        request_timeout=None,
    ):
        del model_override, variant_override, max_tokens, max_retries, request_timeout
        if stage != "project_experiment_audit_review":
            raise AssertionError(f"unexpected complete_json stage: {stage}")
        if "results/metrics.json" not in prompt:
            raise AssertionError("experiment_audit prompt missing results/metrics.json")
        payload = {
            "overall_verdict": "WARN",
            "integrity_status": "warn",
            "evaluation_type": "mixed",
            "summary": "主结果存在，但单 seed 和归一化口径仍需补充说明。",
            "checks": {
                "gt_provenance": {
                    "status": "PASS",
                    "evidence": ["configs/eval.yaml:1"],
                    "details": "评测数据来源可定位。",
                },
                "score_normalization": {
                    "status": "WARN",
                    "evidence": ["results/metrics.json:3"],
                    "details": "normalized_score 的分母定义未说明。",
                },
                "result_existence": {
                    "status": "PASS",
                    "evidence": ["results/metrics.json:2"],
                    "details": "accuracy 指标文件存在。",
                },
                "dead_code": {
                    "status": "PASS",
                    "evidence": ["eval_metric.py:3"],
                    "details": "评测脚本实际读取结果文件。",
                },
                "scope": {
                    "status": "WARN",
                    "evidence": ["EXPERIMENT_TRACKER.md:3"],
                    "details": "当前只记录了单 seed。",
                },
                "eval_type": {
                    "status": "PASS",
                    "evidence": ["paper/sections/experiments.tex:2"],
                    "details": "属于 mixed 类型评测。",
                },
            },
            "action_items": ["补充至少 3 个 seed 的结果", "解释 normalized_score 的定义"],
            "claims": [
                {
                    "id": "C1",
                    "impact": "needs_qualifier",
                    "details": "泛化表述需要收窄到当前设置。",
                }
            ],
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    with _patch_llm_complete_json(_fake_complete_json):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# Experiment Audit Report", context="experiment_audit report")
    _assert_contains(preview, "normalized_score", context="experiment_audit report")
    _assert_artifacts_include(summary, ["reports/experiment-audit.md"], context="experiment_audit")

    artifacts = _artifact_labels(summary)
    if not any(path.endswith("EXPERIMENT_AUDIT.md") for path in artifacts):
        raise AssertionError("experiment_audit 缺少 EXPERIMENT_AUDIT.md")
    if not any(path.endswith("EXPERIMENT_AUDIT.json") for path in artifacts):
        raise AssertionError("experiment_audit 缺少 EXPERIMENT_AUDIT.json")

    with db.session_scope() as session:
        run = ProjectRepository(session).get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        if metadata.get("overall_verdict") != "WARN":
            raise AssertionError("experiment_audit smoke 未写回 overall_verdict")
        if metadata.get("integrity_status") != "warn":
            raise AssertionError("experiment_audit smoke 未写回 integrity_status")
        if metadata.get("evaluation_type") != "mixed":
            raise AssertionError("experiment_audit smoke 未写回 evaluation_type")

    return SmokeResult(
        workflow=ProjectWorkflowType.experiment_audit.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# Experiment Audit Report",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=artifacts,
        details={
            "overall_verdict": "WARN",
            "integrity_status": "warn",
        },
    )


def _run_idea_discovery_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "idea-discovery-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.idea_discovery,
        project_name="ARIS Idea Discovery Smoke",
        workdir=workspace,
        prompt="围绕 AnchorCoT 生成可落地的新研究想法，并做查新和评审",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_idea_discovery_literature":
            return LLMResult(
                content=(
                    "# Literature Landscape\n\n"
                    "- 过程奖励与轨迹筛选是当前主线。\n"
                    "- 结构性空白在于 anchor-aware reward shaping 仍缺系统验证。\n"
                )
            )
        if stage == "project_idea_discovery_novelty":
            return LLMResult(
                content=(
                    "# Deep Novelty Verification\n\n"
                    "## Idea 1\n"
                    "- Closest prior work: process reward reranking.\n"
                    "- Delta: 显式锚点校准与失败轨迹重加权。\n"
                )
            )
        if stage == "project_idea_discovery_review":
            return LLMResult(
                content=(
                    "# External Critical Review\n\n"
                    "Score: 7.9/10\n"
                    "- 优先推进最小实现清晰、验证边界明确的方案。\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    def _fake_complete_json(self, prompt, stage, **kwargs):
        if stage != "project_idea_discovery_ideas":
            raise AssertionError(f"unexpected complete_json stage: {stage}")
        payload = {
            "ideas": [
                {
                    "title": "Anchor-aware Process Reward",
                    "content": "引入 anchor-aware reward shaping，稳定长链路推理轨迹。",
                    "paper_refs": ["P1"],
                },
                {
                    "title": "Trajectory Reweighting by Anchor Consistency",
                    "content": "按 anchor consistency 对候选轨迹重加权，比较筛选稳定性。",
                    "paper_refs": ["P1", "P2"],
                },
                {
                    "title": "Failure-driven Anchor Curriculum",
                    "content": "针对失败案例构建 anchor curriculum，降低错误传播。",
                    "paper_refs": ["P2"],
                },
            ]
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    with _patch_llm_summarize(_fake_summarize):
        with _patch_llm_complete_json(_fake_complete_json):
            run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# Idea Discovery Report", context="idea_discovery report")
    _assert_contains(preview, "Ranked Ideas", context="idea_discovery report")
    _assert_artifacts_include(summary, ["IDEA_REPORT.md"], context="idea_discovery")

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        run = project_repo.get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        if len(metadata.get("created_idea_ids") or []) != 3:
            raise AssertionError("idea_discovery smoke 未写回 3 条 idea")

    return SmokeResult(
        workflow=ProjectWorkflowType.idea_discovery.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# Idea Discovery Report",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
        details={
            "created_ideas": [
                "Anchor-aware Process Reward",
                "Trajectory Reweighting by Anchor Consistency",
                "Failure-driven Anchor Curriculum",
            ]
        },
    )


def _run_novelty_check_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "novelty-check-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.novelty_check,
        project_name="ARIS Novelty Check Smoke",
        workdir=workspace,
        prompt="检查 AnchorCoT 的核心主张与已有工作的差异和撞题风险",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_novelty_check_compare":
            return LLMResult(
                content=(
                    "# Prior Work Comparison\n\n"
                    "- Closest work: process reward reranking.\n"
                    "- 差异在于 AnchorCoT 引入显式 anchor consistency 控制。\n"
                )
            )
        if stage == "project_novelty_check_report":
            return LLMResult(
                content=(
                    "# Novelty Verdict\n\n"
                    "- 当前主张与最接近工作存在明确机制差异。\n"
                    "- 建议补充更强的定量证据以降低撞题风险。\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    with _patch_llm_summarize(_fake_summarize):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 查新评估报告", context="novelty_check report")
    _assert_contains(preview, "Closest work", context="novelty_check report")
    _assert_artifacts_include(summary, ["reports/novelty-check.md"], context="novelty_check")

    return SmokeResult(
        workflow=ProjectWorkflowType.novelty_check.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 查新评估报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_research_review_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "research-review-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.research_review,
        project_name="ARIS Research Review Smoke",
        workdir=workspace,
        prompt="从创新性、可信度和实验充分性三方面评审 AnchorCoT",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_research_review":
            return LLMResult(
                content=(
                    "# Review Notes\n\n"
                    "Score: 7.4/10\n"
                    "- 创新性清晰，但实验充分性仍有提升空间。\n"
                )
            )
        if stage == "project_research_review_verdict":
            return LLMResult(
                content=(
                    "# Verdict\n\n"
                    "Score: 7.8/10\n"
                    "- 建议优先补 anchor ablation 与鲁棒性评测后再推进投稿。\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    with _patch_llm_summarize(_fake_summarize):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 研究评审报告", context="research_review report")
    _assert_contains(preview, "7.8/10", context="research_review report")
    _assert_artifacts_include(summary, ["reports/research-review.md"], context="research_review")

    return SmokeResult(
        workflow=ProjectWorkflowType.research_review.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 研究评审报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_auto_review_loop_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "auto-review-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.auto_review_loop,
        project_name="ARIS Auto Review Smoke",
        workdir=workspace,
        prompt="围绕 AnchorCoT 做一轮自动执行与自我评审",
        max_iterations=1,
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_auto_review_loop_plan":
            return LLMResult(
                content=(
                    "# 自动评审循环计划\n\n"
                    "- 第 1 轮先生成最小执行摘要。\n"
                    "- 若 verdict 达到 ready 则停止循环。\n"
                )
            )
        if stage == "project_auto_review_loop_execute_1":
            return LLMResult(
                content=(
                    "## 第 1 轮执行\n"
                    "- 已完成最小实验检查。\n"
                    "- 当前输出表明 anchor consistency 策略有效。\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    def _fake_complete_json(self, prompt, stage, **kwargs):
        if stage != "project_auto_review_loop_review_1":
            raise AssertionError(f"unexpected complete_json stage: {stage}")
        payload = {
            "score": 8.2,
            "continue": False,
            "summary": "最小闭环已完成，当前结果可以停止自动评审。",
            "verdict": "ready",
            "issues": ["还可补更强消融，但不阻塞当前 smoke"],
            "next_actions": ["进入下一阶段实验扩展"],
            "raw_review": "score=8.2, ready",
            "pending_experiments": [],
        }
        return LLMResult(content=json.dumps(payload, ensure_ascii=False), parsed_json=payload)

    with _patch_llm_summarize(_fake_summarize):
        with _patch_llm_complete_json(_fake_complete_json):
            run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 自动评审循环报告", context="auto_review_loop report")
    _assert_contains(preview, "verdict: `ready`", context="auto_review_loop report")
    _assert_artifacts_include(
        summary,
        ["reports/auto-review-loop.md", "AUTO_REVIEW.md", "REVIEW_STATE.json"],
        context="auto_review_loop",
    )

    return SmokeResult(
        workflow=ProjectWorkflowType.auto_review_loop.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 自动评审循环报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_paper_plan_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "paper-plan-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.paper_plan,
        project_name="ARIS Paper Plan Smoke",
        workdir=workspace,
        prompt="为 AnchorCoT 论文生成完整提纲与 claims-evidence matrix",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_paper_plan_collect_materials":
            return LLMResult(
                content=(
                    "# Material Pack\n\n"
                    "- 已整理研究背景、实验结果与卖点。\n"
                    "- 当前主卖点是 anchor-aware reward shaping。\n"
                )
            )
        if stage == "project_paper_plan_outline_manuscript":
            return LLMResult(
                content=(
                    "# Outline\n\n"
                    "- Claims-evidence matrix 已具备雏形。\n"
                    "- 需要补一张方法图与一张主结果表。\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    with _patch_llm_summarize(_fake_summarize):
        run_multi_agent_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    _assert_artifacts_include(
        summary,
        ["reports/PAPER_PLAN.md", "reports/paper-plan-metadata.json"],
        context="paper_plan",
    )
    report_text = _artifact_path(summary, "reports/PAPER_PLAN.md").read_text(encoding="utf-8", errors="replace")
    _assert_contains(report_text, "# PAPER_PLAN", context="paper_plan artifact")
    _assert_contains(report_text, "Claims-Evidence Matrix", context="paper_plan artifact")

    return SmokeResult(
        workflow=ProjectWorkflowType.paper_plan.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# PAPER_PLAN",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_paper_figure_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "paper-figure-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.paper_figure,
        project_name="ARIS Paper Figure Smoke",
        workdir=workspace,
        prompt="为 AnchorCoT 论文规划图表与表格资产",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_paper_figure_collect_results":
            return LLMResult(
                content=(
                    "# Result Pack\n\n"
                    "- 已整理主结果、消融和错误案例素材。\n"
                )
            )
        if stage == "project_paper_figure_design_figures":
            return LLMResult(
                content=(
                    "# Figure Design\n\n"
                    "- 建议至少产出方法图、主结果表和消融表。\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    with _patch_llm_summarize(_fake_summarize):
        run_multi_agent_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    _assert_artifacts_include(
        summary,
        [
            "figures/FIGURE_PLAN.md",
            "figures/latex_includes.tex",
            "figures/table_main_results.tex",
            "figures/table_ablation.tex",
            "figures/figure_manifest.json",
        ],
        context="paper_figure",
    )
    report_text = _artifact_path(summary, "figures/FIGURE_PLAN.md").read_text(encoding="utf-8", errors="replace")
    _assert_contains(report_text, "# FIGURE_PLAN", context="paper_figure artifact")
    _assert_contains(report_text, "Figure Inventory", context="paper_figure artifact")

    return SmokeResult(
        workflow=ProjectWorkflowType.paper_figure.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# FIGURE_PLAN",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_paper_write_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "paper-write-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.paper_write,
        project_name="ARIS Paper Write Smoke",
        workdir=workspace,
        prompt="为 AnchorCoT 论文生成正文草稿与 LaTeX 工作区",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_paper_write_gather_materials":
            return LLMResult(
                content=(
                    "# Writing Materials\n\n"
                    "- 已具备问题定义、相关工作与方法主线。\n"
                )
            )
        if stage == "project_paper_write_draft_sections":
            return LLMResult(
                content=(
                    "# Draft Sections\n\n"
                    "- Abstract: 概述 AnchorCoT 的目标与结果。\n"
                    "- Method: 强调 anchor-aware reward shaping。\n"
                    "- Experiments: 主结果、消融、错误分析。\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    with _patch_llm_summarize(_fake_summarize):
        run_multi_agent_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    _assert_artifacts_include(
        summary,
        [
            "reports/PAPER_WRITE.md",
            "paper/README.md",
            "paper/main.tex",
            "paper/references.bib",
            "paper/sections/abstract.tex",
            "paper/sections/method.tex",
        ],
        context="paper_write",
    )
    report_text = _artifact_path(summary, "reports/PAPER_WRITE.md").read_text(encoding="utf-8", errors="replace")
    _assert_contains(report_text, "# PAPER_WRITE", context="paper_write artifact")
    _assert_contains(report_text, "paper/main.tex", context="paper_write artifact")

    return SmokeResult(
        workflow=ProjectWorkflowType.paper_write.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# PAPER_WRITE",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_sync_smoke(base_dir: Path) -> SmokeResult:
    source_root = base_dir / "sync-source"
    target_root = base_dir / "sync-target"
    (source_root / "src").mkdir(parents=True, exist_ok=True)
    (source_root / "src" / "main.py").write_text("print('sync smoke')\n", encoding="utf-8")
    (source_root / "README.md").write_text("# Sync Smoke\n", encoding="utf-8")

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.sync_workspace,
        project_name="ARIS Sync Smoke",
        workdir=source_root,
        prompt="同步本地项目工作区",
        metadata={
            "project_workspace_path": str(source_root),
            "target_workspace_path": str(target_root),
            "sync_strategy": "incremental_copy",
        },
    )
    run_multi_agent_project_workflow(run_id)
    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)

    _assert_contains(preview, "# 工作区同步报告", context="sync report")
    _assert_contains(preview, "incremental_copy", context="sync report")
    if not (target_root / "src" / "main.py").exists():
        raise AssertionError("sync smoke 未复制 src/main.py")

    return SmokeResult(
        workflow=ProjectWorkflowType.sync_workspace.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 工作区同步报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_monitor_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "monitor-workspace"
    (workspace / "outputs" / "baseline").mkdir(parents=True, exist_ok=True)
    (workspace / "outputs" / "improved").mkdir(parents=True, exist_ok=True)
    (workspace / "checkpoints").mkdir(parents=True, exist_ok=True)
    (workspace / "tensorboard").mkdir(parents=True, exist_ok=True)
    (workspace / "outputs" / "baseline" / "results.json").write_text(
        json.dumps({"status": "done", "accuracy": 0.82, "loss": 0.43}),
        encoding="utf-8",
    )
    (workspace / "outputs" / "improved" / "results.json").write_text(
        json.dumps({"status": "done", "accuracy": 0.87, "loss": 0.39}),
        encoding="utf-8",
    )
    (workspace / "checkpoints" / "epoch-3.ckpt").write_text("checkpoint", encoding="utf-8")
    (workspace / "tensorboard" / "events.out.tfevents.123").write_text("tensorboard", encoding="utf-8")

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.monitor_experiment,
        project_name="ARIS Monitor Smoke",
        workdir=workspace,
        prompt="监控本地实验产物",
        metadata={
            "experiment_matrix": [
                {"name": "baseline"},
                {"name": "improved"},
            ]
        },
    )
    with db.session_scope() as session:
        run = ProjectRepository(session).get_run(run_id)
        assert run is not None
        assert run.log_path
        Path(str(run.log_path)).parent.mkdir(parents=True, exist_ok=True)
        Path(str(run.log_path)).write_text(
            "epoch=3 accuracy=0.87\nwarning: metric drift observed\n",
            encoding="utf-8",
        )

    run_multi_agent_project_workflow(run_id)
    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)

    _assert_contains(preview, "# 实验监控报告", context="monitor report")
    _assert_contains(preview, "accuracy", context="monitor report")
    _assert_contains(preview, "0.05", context="monitor report")

    return SmokeResult(
        workflow=ProjectWorkflowType.monitor_experiment.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 实验监控报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_paper_compile_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "paper-compile-workspace"
    (workspace / "paper").mkdir(parents=True, exist_ok=True)
    (workspace / "paper" / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nAnchorCoT smoke\n\\end{document}\n",
        encoding="utf-8",
    )

    compile_command = (
        "New-Item -ItemType Directory -Force -Path paper | Out-Null; "
        "Set-Content -Path paper/main.pdf -Value \"pdf stub\"; "
        "Write-Output \"compile-ok\""
    )
    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.paper_compile,
        project_name="ARIS Paper Compile Smoke",
        workdir=workspace,
        prompt="编译当前论文稿件",
        metadata={
            "compile_command": compile_command,
        },
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if str(stage).endswith("_summarize_compile"):
            return LLMResult(
                content=(
                    "# PAPER_COMPILE_SUMMARY\n\n"
                    "- 编译成功\n"
                    "- 已生成 `paper/main.pdf`\n"
                    "- 下一步可继续进入 paper_improvement\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    with _patch_llm_summarize(_fake_summarize):
        run_multi_agent_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 论文编译报告", context="paper_compile report")
    _assert_contains(preview, "paper/main.pdf", context="paper_compile report")
    if not (workspace / "paper" / "main.pdf").exists():
        raise AssertionError("paper_compile smoke 未生成 paper/main.pdf")

    return SmokeResult(
        workflow=ProjectWorkflowType.paper_compile.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 论文编译报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_paper_improvement_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "paper-improvement-workspace"
    (workspace / "paper").mkdir(parents=True, exist_ok=True)
    (workspace / "paper" / "main.tex").write_text(
        "\\section{Method}\nAnchorCoT aligns anchors and process rewards.\n",
        encoding="utf-8",
    )

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.paper_improvement,
        project_name="ARIS Paper Improvement Smoke",
        workdir=workspace,
        prompt="对当前论文做两轮评审与修订",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        stage_name = str(stage)
        if stage_name.endswith("_diagnose_draft"):
            return LLMResult(
                content=(
                    "# Review Round 1\n\n"
                    "Score: 6.8/10\n"
                    "Verdict: ALMOST\n\n"
                    "1. 补充 anchor ablation。\n"
                    "2. 强化失败案例分析。\n"
                )
            )
        if stage_name.endswith("_revise_sections"):
            return LLMResult(
                content=(
                    "# Revision Notes\n\n"
                    "- 增加 anchor ablation 设计。\n"
                    "- 补充典型失败案例讨论。\n"
                )
            )
        if stage_name.endswith("_final_check"):
            return LLMResult(
                content=(
                    "# Review Round 2\n\n"
                    "Score: 7.6/10\n"
                    "Verdict: READY\n\n"
                    "1. 只需再做少量语言润色。\n"
                )
            )
        raise AssertionError(f"unexpected summarize stage: {stage}")

    with _patch_llm_summarize(_fake_summarize):
        run_multi_agent_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 论文改稿报告", context="paper_improvement report")
    _assert_contains(preview, "score=`7.6`, verdict=`ready`", context="paper_improvement report")

    return SmokeResult(
        workflow=ProjectWorkflowType.paper_improvement.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 论文改稿报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_remote_batch_experiment_smoke(base_dir: Path) -> SmokeResult:
    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.run_experiment,
        project_name="ARIS Remote Batch Smoke",
        workdir=None,
        prompt="启动远程批量实验",
        workspace_server_id="ssh-main",
        remote_workdir="/srv/research/anchorcot-batch",
        metadata={
            "parallel_experiments": [
                {"name": "baseline", "command": "python train.py --config baseline.yaml"},
                {"name": "improved", "command": "python train.py --config improved.yaml"},
            ],
        },
    )

    active_sessions: set[str] = set()
    launch_records: list[dict[str, Any]] = []

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage != "project_run_experiment_summary":
            raise AssertionError(f"unexpected summarize stage: {stage}")
        return LLMResult(content="# 远程批量实验摘要\n\n- 两个实验都已通过 screen 在后台启动\n")

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- train.py\n- configs/\n",
            "files": ["train.py", "configs/default.yaml"],
            "exists": True,
            "git": {"available": True, "is_repo": True},
        }

    def _fake_remote_terminal_result(server_entry, *, path, command, timeout_sec):
        stdout = ""
        if command == "python --version":
            stdout = "Python 3.11.9"
        elif command == "git --version":
            stdout = "git version 2.45.0"
        elif command == "uv --version":
            stdout = "uv 0.6.0"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_prepare_run_environment(server_entry, *, path, run_directory, session_name):
        return {
            "workspace_path": path,
            "run_directory": run_directory,
            "execution_workspace": f"{run_directory}/workspace",
            "session_name": session_name,
            "isolation_mode": "git_worktree",
            "git_available": True,
            "git_branch": "main",
            "git_head": "abc123",
            "created_worktree": True,
            "prepare_steps": [],
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {"index": 0, "name": "A100", "memory_used_mb": 220, "memory_total_mb": 81920, "utilization_gpu_pct": 9},
                {"index": 1, "name": "A100", "memory_used_mb": 120, "memory_total_mb": 81920, "utilization_gpu_pct": 3},
            ],
            "reason": None,
        }

    def _fake_launch_screen_job(
        server_entry,
        *,
        path,
        session_name,
        command,
        log_path,
        env_vars=None,
        timeout_sec=30,
    ):
        active_sessions.add(session_name)
        launch_records.append(
            {
                "session_name": session_name,
                "gpu": dict(env_vars or {}).get("CUDA_VISIBLE_DEVICES"),
                "path": path,
                "command": command,
            }
        )
        return {
            "workspace_path": path,
            "session_name": session_name,
            "log_path": log_path,
            "command": command,
            "env_vars": dict(env_vars or {}),
            "launch_command": f"screen -dmS {session_name} bash -lc 'cd {path} && {command}'",
            "already_running": False,
            "launched": True,
            "success": True,
            "stdout": "",
            "stderr": "",
            "sessions": [{"pid": 7000 + len(active_sessions), "name": session_name, "state": "Detached"}],
            "screen_list_stdout": "",
            "screen_list_stderr": "",
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        sessions = []
        for idx, name in enumerate(sorted(active_sessions), start=1):
            if session_name and name != session_name:
                continue
            if session_prefix and not name.startswith(session_prefix):
                continue
            sessions.append({"pid": 7100 + idx, "name": name, "state": "Detached"})
        return {
            "command": "screen -ls",
            "stdout": "\n".join(f"{item['pid']}.{item['name']} (Detached)" for item in sessions),
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": sessions,
            "session_count": len(sessions),
        }

    def _fake_capture_screen_session(server_entry, *, session_name, lines=80):
        return {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": f"{session_name}: step=12",
            "stderr": "",
            "success": True,
        }

    assignments = [
        (llm_client_module.LLMClient, "summarize_text", _fake_summarize),
        (project_workflow_runner_module, "get_workspace_server_entry", lambda server_id: {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        }),
        (project_workflow_runner_module, "build_remote_overview", _fake_build_remote_overview),
        (project_workflow_runner_module, "remote_terminal_result", _fake_remote_terminal_result),
        (project_workflow_runner_module, "remote_prepare_run_environment", _fake_prepare_run_environment),
        (project_workflow_runner_module, "remote_probe_gpus", _fake_probe_gpus),
        (project_workflow_runner_module, "remote_launch_screen_job", _fake_launch_screen_job),
        (project_workflow_runner_module, "remote_list_screen_sessions", _fake_list_screen_sessions),
        (project_workflow_runner_module, "remote_capture_screen_session", _fake_capture_screen_session),
        (project_workflow_runner_module, "remote_write_file", lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode("utf-8")),
        }),
    ]

    with _patch_assignments(assignments):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 实验运行报告", context="remote batch report")

    with db.session_scope() as session:
        run = ProjectRepository(session).get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        execution = dict(metadata.get("execution_result") or {})
        remote_experiments = list(metadata.get("remote_experiments") or [])
        if execution.get("mode") != "remote_screen_batch_launch":
            raise AssertionError("remote batch smoke 未生成 remote_screen_batch_launch")
        if len(remote_experiments) != 2:
            raise AssertionError("remote batch smoke 远程实验数不为 2")
        gpu_indexes = [int(item.get("selected_gpu", {}).get("index")) for item in remote_experiments]
        if gpu_indexes != [1, 0]:
            raise AssertionError(f"remote batch smoke GPU 分配异常: {gpu_indexes}")

    return SmokeResult(
        workflow="run_experiment_remote_batch",
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 实验运行报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
        details={
            "remote_session_names": [item["session_name"] for item in launch_records],
            "gpu_order": [item["gpu"] for item in launch_records],
            "launch_paths": [item["path"] for item in launch_records],
        },
    )


def _run_remote_monitor_smoke(base_dir: Path) -> SmokeResult:
    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.monitor_experiment,
        project_name="ARIS Remote Monitor Smoke",
        workdir=None,
        prompt="监控远程后台实验",
        workspace_server_id="ssh-main",
        remote_workdir="/srv/research/remote-monitor",
        metadata={
            "remote_session_name": "aris-remote-monitor-baseline",
            "remote_session_names": [
                "aris-remote-monitor-baseline",
                "aris-remote-monitor-improved",
            ],
            "remote_experiments": [
                {"name": "baseline", "remote_session_name": "aris-remote-monitor-baseline"},
                {"name": "improved", "remote_session_name": "aris-remote-monitor-improved"},
            ],
        },
    )

    file_payloads = {
        "outputs/baseline/results.json": json.dumps({"status": "done", "accuracy": 0.82, "loss": 0.43}),
        "outputs/improved/results.json": json.dumps({"status": "done", "accuracy": 0.87, "loss": 0.39}),
        "wandb/run-123/files/wandb-summary.json": json.dumps({"best_accuracy": 0.87, "best_loss": 0.39}),
    }

    def _fake_server(server_id):
        return {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        }

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": (
                f"{requested_path}\n"
                "- outputs/\n"
                "  - baseline/\n"
                "    - results.json\n"
                "  - improved/\n"
                "    - results.json\n"
                "- checkpoints/\n"
                "  - epoch-3.ckpt\n"
                "- tensorboard/\n"
                "  - events.out.tfevents.123\n"
                "- wandb/\n"
                "  - run-123/files/wandb-summary.json\n"
            ),
            "files": [
                "outputs/baseline/results.json",
                "outputs/improved/results.json",
                "checkpoints/epoch-3.ckpt",
                "tensorboard/events.out.tfevents.123",
                "wandb/run-123/files/wandb-summary.json",
                "run.log",
            ],
            "exists": True,
            "git": {"available": True, "is_repo": True},
        }

    def _fake_remote_terminal_result(server_entry, *, path, command, timeout_sec):
        if command == "python --version":
            stdout = "Python 3.11.9"
        elif command == "git --version":
            stdout = "git version 2.45.0"
        elif command == "uv --version":
            stdout = "uv 0.6.0"
        else:
            stdout = "tail: step=88 val_acc=0.93"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        sessions = [
            {"pid": 9101, "name": "aris-remote-monitor-baseline", "state": "Detached"},
            {"pid": 9102, "name": "aris-remote-monitor-improved", "state": "Detached"},
        ]
        if session_name:
            sessions = [item for item in sessions if item["name"] == session_name]
        return {
            "command": "screen -ls",
            "stdout": "\n".join(f"{item['pid']}.{item['name']} (Detached)" for item in sessions),
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": sessions,
            "session_count": len(sessions),
        }

    def _fake_capture_screen_session(server_entry, *, session_name, lines=80):
        return {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": f"{session_name}: accuracy=0.87",
            "stderr": "",
            "success": True,
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {"index": 0, "name": "A100", "memory_used_mb": 220, "memory_total_mb": 81920, "utilization_gpu_pct": 8},
            ],
            "reason": None,
            "active_leases": [],
            "released_leases": [],
        }

    assignments = [
        (project_multi_agent_runner_module, "get_workspace_server_entry", _fake_server),
        (project_workflow_runner_module, "get_workspace_server_entry", _fake_server),
        (project_multi_agent_runner_module, "build_remote_overview", _fake_build_remote_overview),
        (project_workflow_runner_module, "build_remote_overview", _fake_build_remote_overview),
        (project_multi_agent_runner_module, "remote_terminal_result", _fake_remote_terminal_result),
        (project_workflow_runner_module, "remote_terminal_result", _fake_remote_terminal_result),
        (project_multi_agent_runner_module, "remote_list_screen_sessions", _fake_list_screen_sessions),
        (project_multi_agent_runner_module, "remote_capture_screen_session", _fake_capture_screen_session),
        (project_multi_agent_runner_module, "remote_probe_gpus", _fake_probe_gpus),
        (project_multi_agent_runner_module, "remote_read_file", lambda server_entry, requested_path, relative_path, *, max_chars: {
            "workspace_path": requested_path,
            "relative_path": relative_path,
            "content": file_payloads[str(relative_path)][:max_chars],
        }),
        (project_workflow_runner_module, "remote_write_file", lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode("utf-8")),
        }),
    ]

    with _patch_assignments(assignments):
        run_multi_agent_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 实验监控报告", context="remote monitor report")
    _assert_contains(preview, "Detached", context="remote monitor report")
    _assert_contains(preview, "accuracy", context="remote monitor report")

    return SmokeResult(
        workflow="monitor_experiment_remote",
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 实验监控报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
        details={
            "tracked_sessions": ["aris-remote-monitor-baseline", "aris-remote-monitor-improved"],
        },
    )


def _run_remote_gpu_lease_smoke(base_dir: Path) -> SmokeResult:
    run_ids = [
        _seed_run(
            workflow_type=ProjectWorkflowType.run_experiment,
            project_name="ARIS Remote Lease Smoke A",
            workdir=None,
            prompt="启动第一个远程实验",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/lease-a",
            metadata={"execution_command": "python train.py --epochs 1"},
        ),
        _seed_run(
            workflow_type=ProjectWorkflowType.run_experiment,
            project_name="ARIS Remote Lease Smoke B",
            workdir=None,
            prompt="启动第二个远程实验",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/lease-b",
            metadata={"execution_command": "python train.py --epochs 1"},
        ),
    ]

    active_sessions: set[str] = set()
    launch_records: list[dict[str, Any]] = []

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage != "project_run_experiment_summary":
            raise AssertionError(f"unexpected summarize stage: {stage}")
        return LLMResult(content="# 远程实验摘要\n\n- 远程实验已启动\n")

    def _fake_server(server_id):
        return {
            "id": server_id,
            "host": "gpu.example.com",
            "workspace_root": "/srv/research",
            "username": "tester",
            "enabled": True,
        }

    def _fake_build_remote_overview(server_entry, requested_path, *, depth, max_entries):
        return {
            "workspace_path": requested_path,
            "tree": f"{requested_path}\n- train.py\n",
            "files": ["train.py"],
            "exists": True,
            "git": {"available": True, "is_repo": True},
        }

    def _fake_remote_terminal_result(server_entry, *, path, command, timeout_sec):
        stdout = ""
        if command == "python --version":
            stdout = "Python 3.11.9"
        elif command == "git --version":
            stdout = "git version 2.45.0"
        elif command == "uv --version":
            stdout = "uv 0.6.0"
        return {
            "workspace_path": path,
            "command": command,
            "shell_command": ["ssh", "user@host", command],
            "exit_code": 0,
            "stdout": stdout,
            "stderr": "",
            "success": True,
        }

    def _fake_prepare_run_environment(server_entry, *, path, run_directory, session_name):
        return {
            "workspace_path": path,
            "run_directory": run_directory,
            "execution_workspace": f"{run_directory}/workspace",
            "session_name": session_name,
            "isolation_mode": "git_worktree",
            "git_available": True,
            "git_branch": "main",
            "git_head": "abc123",
            "created_worktree": True,
            "prepare_steps": [],
        }

    def _fake_probe_gpus(server_entry, *, path):
        return {
            "workspace_path": path,
            "available": True,
            "success": True,
            "gpus": [
                {"index": 0, "name": "A100", "memory_used_mb": 200, "memory_total_mb": 81920, "utilization_gpu_pct": 9},
                {"index": 1, "name": "A100", "memory_used_mb": 120, "memory_total_mb": 81920, "utilization_gpu_pct": 3},
            ],
            "reason": None,
        }

    def _fake_launch_screen_job(
        server_entry,
        *,
        path,
        session_name,
        command,
        log_path,
        env_vars=None,
        timeout_sec=30,
    ):
        active_sessions.add(session_name)
        launch_records.append(
            {
                "session_name": session_name,
                "gpu": dict(env_vars or {}).get("CUDA_VISIBLE_DEVICES"),
                "path": path,
            }
        )
        return {
            "workspace_path": path,
            "session_name": session_name,
            "log_path": log_path,
            "command": command,
            "env_vars": dict(env_vars or {}),
            "launch_command": f"screen -dmS {session_name} bash -lc 'cd {path} && {command}'",
            "already_running": False,
            "launched": True,
            "success": True,
            "stdout": "",
            "stderr": "",
            "sessions": [{"pid": 5000 + len(active_sessions), "name": session_name, "state": "Detached"}],
            "screen_list_stdout": "",
            "screen_list_stderr": "",
        }

    def _fake_list_screen_sessions(server_entry, *, session_name=None, session_prefix=None):
        sessions = []
        for idx, name in enumerate(sorted(active_sessions), start=1):
            if session_name and name != session_name:
                continue
            if session_prefix and not name.startswith(session_prefix):
                continue
            sessions.append({"pid": 6000 + idx, "name": name, "state": "Detached"})
        return {
            "command": "screen -ls",
            "stdout": "\n".join(f"{item['pid']}.{item['name']} (Detached)" for item in sessions),
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "sessions": sessions,
            "session_count": len(sessions),
        }

    def _fake_capture_screen_session(server_entry, *, session_name, lines=80):
        return {
            "session_name": session_name,
            "hardcopy_path": f"/tmp/{session_name}.txt",
            "command": "screen -X hardcopy",
            "exit_code": 0,
            "stdout": "running",
            "stderr": "",
            "success": True,
        }

    assignments = [
        (llm_client_module.LLMClient, "summarize_text", _fake_summarize),
        (project_workflow_runner_module, "get_workspace_server_entry", _fake_server),
        (project_workflow_runner_module, "build_remote_overview", _fake_build_remote_overview),
        (project_workflow_runner_module, "remote_terminal_result", _fake_remote_terminal_result),
        (project_workflow_runner_module, "remote_prepare_run_environment", _fake_prepare_run_environment),
        (project_workflow_runner_module, "remote_probe_gpus", _fake_probe_gpus),
        (project_workflow_runner_module, "remote_launch_screen_job", _fake_launch_screen_job),
        (project_workflow_runner_module, "remote_list_screen_sessions", _fake_list_screen_sessions),
        (project_workflow_runner_module, "remote_capture_screen_session", _fake_capture_screen_session),
        (project_workflow_runner_module, "remote_write_file", lambda server_entry, *, path, relative_path, content, create_dirs=True, overwrite=True: {
            "workspace_path": path,
            "relative_path": relative_path,
            "size_bytes": len(content.encode("utf-8")),
        }),
    ]

    with _patch_assignments(assignments):
        run_project_workflow(run_ids[0])
        run_project_workflow(run_ids[1])

    second_summary = _load_run_summary(run_ids[1])
    preview = _preview_primary_report(second_summary)
    _assert_contains(preview, "# 实验运行报告", context="remote lease report")

    gpu_order = [item["gpu"] for item in launch_records]
    if gpu_order != ["1", "0"]:
        raise AssertionError(f"remote lease smoke GPU 顺序异常: {gpu_order}")

    return SmokeResult(
        workflow="run_experiment_remote_lease",
        run_id=",".join(run_ids),
        status="succeeded",
        report_title="# 实验运行报告",
        excerpt=str(second_summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(second_summary),
        details={
            "gpu_order": gpu_order,
            "sessions": [item["session_name"] for item in launch_records],
        },
    )


def _run_paper_writing_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "paper-writing-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.paper_writing,
        project_name="ARIS Paper Writing Smoke",
        workdir=workspace,
        prompt="生成论文完整工作区并完成两轮改稿",
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_paper_writing_plan":
            return LLMResult(content="# PAPER_PLAN\n\n## Claims-Evidence Matrix\n- Claim 1 -> Experiment A\n")
        if stage == "project_paper_writing_figure":
            return LLMResult(content="# FIGURE_PLAN\n\n- Fig 1: Main comparison\n- Fig 2: Ablation\n")
        if stage == "project_paper_writing_write":
            return LLMResult(content="# Draft\n\n## Method\nDraft body.\n\n## Experiments\nDraft plan.\n")
        if stage.startswith("project_paper_writing_improve_review_"):
            return LLMResult(content="# Review\n\nScore: 7.8\nVerdict: READY\n\n- Improve framing.\n")
        if stage.startswith("project_paper_writing_improve_revise_"):
            return LLMResult(content="# Final Draft\n\n## Method\nRevised body.\n\n## Conclusion\nReady.\n")
        raise AssertionError(f"unexpected stage: {stage}")

    def _fake_run_workspace_command(context, command, *, timeout_sec, workspace_path_override=None):
        if command == "latexmk --version":
            return {
                "command": command,
                "success": True,
                "exit_code": 0,
                "stdout": "Latexmk, John Collins",
                "stderr": "",
            }
        if command in {"pdflatex --version", "bibtex --version"}:
            return {
                "command": command,
                "success": False,
                "exit_code": 1,
                "stdout": "",
                "stderr": "not needed",
            }
        if "latexmk -pdf" in command:
            pdf_path = workspace / ".auto-researcher" / "aris-runs" / run_id / "paper" / "build" / "main.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.4 smoke")
            return {
                "command": command,
                "success": True,
                "exit_code": 0,
                "stdout": "compiled",
                "stderr": "",
            }
        raise AssertionError(f"unexpected workspace command: {command}")

    assignments = [
        (llm_client_module.LLMClient, "summarize_text", _fake_summarize),
        (project_workflow_runner_module, "_run_workspace_command_for_context", _fake_run_workspace_command),
    ]

    with _patch_assignments(assignments):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 论文写作报告", context="paper_writing report")
    run_root = workspace / ".auto-researcher" / "aris-runs" / run_id
    for relative in ("paper/main_round0_original.pdf", "paper/main_round1.pdf", "paper/main_round2.pdf"):
        if not (run_root / relative).exists():
            raise AssertionError(f"paper_writing smoke 缺少 {relative}")

    return SmokeResult(
        workflow=ProjectWorkflowType.paper_writing.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 论文写作报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
        details={
            "round_pdfs": [
                "paper/main_round0_original.pdf",
                "paper/main_round1.pdf",
                "paper/main_round2.pdf",
            ],
        },
    )


def _run_rebuttal_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "rebuttal-workspace"
    _prepare_local_workspace(workspace)

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.rebuttal,
        project_name="ARIS Rebuttal Smoke",
        workdir=workspace,
        prompt="基于当前稿件和 reviews 生成 ICML rebuttal",
        metadata={
            "rebuttal_review_bundle": (
                "Reviewer 1:\n"
                "- Novelty is not clearly separated from the closest prior work.\n\n"
                "Reviewer 2:\n"
                "- The empirical evidence is promising but still limited.\n"
            ),
            "rebuttal_venue": "ICML",
            "rebuttal_character_limit": 5000,
            "rebuttal_round": "initial",
        },
    )

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_rebuttal_issue_board":
            return LLMResult(
                content=(
                    "# ISSUE_BOARD\n\n"
                    "- R1-C1: novelty clarification\n"
                    "- R2-C1: empirical support\n"
                )
            )
        if stage == "project_rebuttal_strategy":
            return LLMResult(
                content=(
                    "# STRATEGY_PLAN\n\n"
                    "- shared theme: clarify exact delta to prior work\n"
                    "- shared theme: narrow the empirical claim and cite existing evidence\n"
                )
            )
        if stage == "project_rebuttal_draft":
            return LLMResult(
                content=(
                    "# REBUTTAL_DRAFT\n\n"
                    "We thank the reviewers and respond to each concern below.\n\n"
                    "## Reviewer 1\n"
                    "- We clarify the novelty boundary against the closest baseline.\n"
                )
            )
        if stage == "project_rebuttal_stress":
            return LLMResult(
                content=(
                    "# MCP_STRESS_TEST\n\n"
                    "- tighten the baseline delta wording\n"
                    "- avoid overclaiming on empirical generality\n"
                )
            )
        if stage == "project_rebuttal_finalize":
            return LLMResult(
                content=(
                    "# REBUTTAL_DRAFT\n\n"
                    "We thank the reviewers and provide a grounded response for novelty and empirical support.\n"
                )
            )
        raise AssertionError(f"unexpected stage: {stage}")

    with _patch_llm_summarize(_fake_summarize):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    _assert_artifacts_include(
        summary,
        [
            "rebuttal/ISSUE_BOARD.md",
            "rebuttal/STRATEGY_PLAN.md",
            "rebuttal/REBUTTAL_DRAFT_rich.md",
            "rebuttal/PASTE_READY.txt",
            "reports/rebuttal.md",
        ],
        context="rebuttal",
    )
    report_text = _artifact_path(summary, "reports/rebuttal.md").read_text(encoding="utf-8", errors="replace")
    _assert_contains(report_text, "# Rebuttal 报告", context="rebuttal artifact")
    _assert_contains(report_text, "ICML", context="rebuttal artifact")
    _assert_contains(report_text, "Issue Board", context="rebuttal artifact")

    return SmokeResult(
        workflow=ProjectWorkflowType.rebuttal.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# Rebuttal 报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
    )


def _run_full_pipeline_smoke(base_dir: Path) -> SmokeResult:
    workspace = base_dir / "full-pipeline-workspace"
    code_dir = workspace / "pipeline-src"
    code_dir.mkdir(parents=True, exist_ok=True)
    (workspace / "CLAUDE.md").write_text(
        "## Local Environment\n"
        "- Activate: `conda activate pipeline-env`\n"
        "- Code dir: `pipeline-src`\n",
        encoding="utf-8",
    )

    run_id = _seed_run(
        workflow_type=ProjectWorkflowType.full_pipeline,
        project_name="ARIS Full Pipeline Smoke",
        workdir=workspace,
        prompt="完成想法筛选、实验执行和最终交付",
        metadata={
            "execution_command": "python train.py --epochs 1",
        },
    )

    captured: dict[str, Any] = {}

    def _fake_summarize(self, prompt, stage, **kwargs):
        if stage == "project_full_pipeline_gate":
            return LLMResult(content="# IDEA_REPORT\n\n## Recommended Idea\n\nAnchorCoT should be prioritized.\n")
        if stage == "project_full_pipeline_auto_review":
            return LLMResult(content="# AUTO_REVIEW\n\n- score: 7/10\n- verdict: ready\n")
        if stage == "project_full_pipeline_handoff":
            return LLMResult(content="# Final Report\n\nEverything is ready.\n")
        raise AssertionError(f"unexpected summarize stage: {stage}")

    def _fake_inspect_workspace(context, workspace_path_override=None):
        return {
            "workspace_path": workspace_path_override or str(workspace),
            "tree": "workspace\n- pipeline-src/\n",
            "message": None,
        }

    def _fake_run_workspace_command(context, command, *, timeout_sec, workspace_path_override=None):
        captured["command"] = command
        captured["workspace_path_override"] = str(workspace_path_override or "")
        return {
            "command": command,
            "success": True,
            "exit_code": 0,
            "stdout": "pipeline ok",
            "stderr": "",
        }

    assignments = [
        (llm_client_module.LLMClient, "summarize_text", _fake_summarize),
        (project_workflow_runner_module, "_inspect_workspace_payload", _fake_inspect_workspace),
        (project_workflow_runner_module, "_run_workspace_command_for_context", _fake_run_workspace_command),
    ]

    with _patch_assignments(assignments):
        run_project_workflow(run_id)

    summary = _load_run_summary(run_id)
    preview = _preview_primary_report(summary)
    _assert_contains(preview, "# 科研流程交付报告", context="full_pipeline report")

    with db.session_scope() as session:
        run = ProjectRepository(session).get_run(run_id)
        assert run is not None
        metadata = dict(run.metadata_json or {})
        effective_command = str(metadata.get("effective_execution_command") or "")
        execution_workspace = str(metadata.get("execution_workspace") or "")
        if effective_command != "conda activate pipeline-env && python train.py --epochs 1":
            raise AssertionError(f"full_pipeline smoke effective command 异常: {effective_command}")
        if Path(execution_workspace) != code_dir:
            raise AssertionError(f"full_pipeline smoke execution workspace 异常: {execution_workspace}")

    return SmokeResult(
        workflow=ProjectWorkflowType.full_pipeline.value,
        run_id=run_id,
        status=str(summary.get("status") or ""),
        report_title="# 科研流程交付报告",
        excerpt=str(summary.get("metadata", {}).get("workflow_output_excerpt") or ""),
        artifacts=_artifact_labels(summary),
        details={
            "effective_command": captured.get("command"),
            "execution_workspace": captured.get("workspace_path_override"),
        },
    )


def main() -> int:
    print("ARIS workflow smoke")
    print(f"Root: {ROOT}")
    active_workflows = sorted(
        workflow_type.value
        for workflow_type in ProjectWorkflowType
        if is_active_project_workflow(workflow_type)
    )
    tmp_root = ROOT / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)

    scenarios: list[tuple[str, Callable[[Path], SmokeResult]]] = [
        (ProjectWorkflowType.literature_review.value, _run_literature_review_smoke),
        (ProjectWorkflowType.idea_discovery.value, _run_idea_discovery_smoke),
        (ProjectWorkflowType.novelty_check.value, _run_novelty_check_smoke),
        (ProjectWorkflowType.research_review.value, _run_research_review_smoke),
        (ProjectWorkflowType.run_experiment.value, _run_local_experiment_smoke),
        (ProjectWorkflowType.experiment_audit.value, _run_experiment_audit_smoke),
        (ProjectWorkflowType.auto_review_loop.value, _run_auto_review_loop_smoke),
        (ProjectWorkflowType.paper_plan.value, _run_paper_plan_smoke),
        (ProjectWorkflowType.paper_figure.value, _run_paper_figure_smoke),
        (ProjectWorkflowType.paper_write.value, _run_paper_write_smoke),
        (ProjectWorkflowType.paper_compile.value, _run_paper_compile_smoke),
        (ProjectWorkflowType.paper_writing.value, _run_paper_writing_smoke),
        (ProjectWorkflowType.rebuttal.value, _run_rebuttal_smoke),
        (ProjectWorkflowType.paper_improvement.value, _run_paper_improvement_smoke),
        (ProjectWorkflowType.full_pipeline.value, _run_full_pipeline_smoke),
        (ProjectWorkflowType.monitor_experiment.value, _run_monitor_smoke),
        (ProjectWorkflowType.sync_workspace.value, _run_sync_smoke),
        ("run_experiment_remote_batch", _run_remote_batch_experiment_smoke),
        ("run_experiment_remote_lease", _run_remote_gpu_lease_smoke),
        ("monitor_experiment_remote", _run_remote_monitor_smoke),
    ]

    results: list[SmokeResult] = []
    failures: list[SmokeFailure] = []

    with tempfile.TemporaryDirectory(prefix="aris-workflow-smoke-", dir=str(tmp_root)) as temp_dir:
        base_dir = Path(temp_dir)
        for workflow_name, handler in scenarios:
            print(f"[ARIS smoke] START {workflow_name}")
            _configure_test_db()
            scenario_dir = base_dir / workflow_name
            scenario_dir.mkdir(parents=True, exist_ok=True)
            try:
                result = handler(scenario_dir)
                results.append(result)
                print(f"[ARIS smoke] PASS  {workflow_name}")
            except Exception as exc:
                failures.append(SmokeFailure(workflow=workflow_name, error=str(exc)))
                print(f"[ARIS smoke] FAIL  {workflow_name}: {exc}")
                traceback.print_exc()

    covered_active = sorted(
        result.workflow
        for result in results
        if result.workflow in active_workflows
    )
    missing_active = [workflow for workflow in active_workflows if workflow not in covered_active]

    payload = {
        "active_workflows": active_workflows,
        "covered_active_workflows": covered_active,
        "missing_active_workflows": missing_active,
        "results": [result.__dict__ for result in results],
        "failures": [failure.__dict__ for failure in failures],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if failures or missing_active:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
