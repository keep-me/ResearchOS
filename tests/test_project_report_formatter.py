from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import agent_workspace as agent_workspace_router
from apps.api.routers import projects as projects_router
from packages.ai.project.report_formatter import build_workflow_report_markdown
from packages.domain.enums import ProjectRunStatus, ProjectWorkflowType
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import ProjectRepository


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    import packages.storage.models  # noqa: F401

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def test_build_workflow_report_markdown_formats_idea_discovery_output():
    metadata = {
        "created_ideas": [
            {
                "title": "AnchorCoT",
                "content": "通过锚点定位奖励、方位一致性奖励和目标定位奖励实现可验证空间推理。",
            }
        ],
        "stage_outputs": {
            "collect_context": {
                "content": "# Checkpoint\n如果你不回复，我将默认继续。\n\n## 文献地形\n- 现有方法多依赖最终答案监督。\n",
            },
            "verify_novelty": {
                "content": "to=mcp codex tool code syntax error: boom\n\n## 查新判断\n- 过程奖励与空间认知锚点的显式对齐仍较少见。\n",
            },
            "external_review": {
                "content": "## 外部评审\n- 需要补充更强的相近工作对比。\n",
            },
        },
        "workflow_output_markdown": "原始输出占位",
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.idea_discovery.value,
        project_label="AnchorCot",
        prompt="验证 AnchorCoT 作为空间推理过程奖励框架的研究可行性",
        metadata=metadata,
    )

    assert report is not None
    assert "# Idea Discovery Report" in report
    assert "## Executive Summary" in report
    assert "## Ranked Ideas" in report
    assert "AnchorCoT" in report
    assert "to=mcp" not in report
    assert "# Checkpoint" not in report


def test_build_workflow_report_markdown_formats_run_experiment_output():
    metadata = {
        "execution_command": "python train.py --config base",
        "execution_workspace": "/srv/research/anchorcot",
        "remote_launch_status": "running",
        "remote_session_name": "aris-anchorcot-01",
        "remote_execution_workspace": "/srv/research/anchorcot/worktrees/run-1",
        "remote_isolation_mode": "git_worktree",
        "selected_gpu": {
            "index": 1,
            "name": "NVIDIA A100",
            "memory_used_mb": 2048,
        },
        "execution_result": {
            "command": "python train.py --config base",
            "stdout": "epoch=1 acc=0.72\nsaved checkpoint\n",
            "stderr": "",
            "exit_code": 0,
            "success": True,
            "workspace_path": "/srv/research/anchorcot",
        },
        "stage_outputs": {
            "summarize_results": {
                "content": "## 结果概览\n- 主实验已成功启动并输出初始 checkpoint。\n- 建议持续观察验证集曲线。\n",
            }
        },
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.run_experiment.value,
        project_label="AnchorCot Experiment",
        prompt="在远程 A100 上运行主实验",
        metadata=metadata,
    )

    assert report is not None
    assert "# 实验运行报告" in report
    assert "## 执行配置" in report
    assert "python train.py --config base" in report
    assert "aris-anchorcot-01" in report
    assert "NVIDIA A100" in report
    assert "saved checkpoint" in report


def test_build_workflow_report_markdown_formats_paper_writing_output():
    metadata = {
        "venue": "ICLR",
        "paper_improvement_scores": {
            "round_1": 6.5,
            "round_2": 7.2,
        },
        "paper_improvement_verdicts": {
            "round_1": "almost",
            "round_2": "ready",
        },
        "stage_outputs": {
            "gather_materials": {
                "content": "# PAPER_PLAN\n\n## Claims-Evidence Matrix\n- 强化贡献与证据映射。\n",
            },
            "design_figures": {
                "content": "# FIGURE_PLAN\n\n- Fig 1: AnchorCoT pipeline\n",
            },
            "compile_manuscript": {
                "content": "# PAPER_COMPILE\n\n- latexmk success\n",
            },
            "polish_manuscript": {
                "content": "# Paper Draft\n\n## Method\nAnchorCoT aligns anchor selection and geometric reasoning.\n",
                "action_items_round_one": ["补充最接近工作的差异分析"],
                "action_items_round_two": ["压缩摘要并强化实验结论"],
                "score_round_one": 6.5,
                "score_round_two": 7.2,
                "verdict_round_one": "almost",
                "verdict_round_two": "ready",
            },
        },
        "workflow_output_markdown": "# Paper Draft\n\n## Method\nAnchorCoT aligns anchor selection and geometric reasoning.\n",
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.paper_writing.value,
        project_label="AnchorCot Writing",
        prompt="生成论文初稿并完成两轮修改",
        metadata=metadata,
    )

    assert report is not None
    assert "# 论文写作报告" in report
    assert "## 写作规划" in report
    assert "## 图表计划" in report
    assert "## 编译状态" in report
    assert "Round 2: score=`7.2`, verdict=`ready`" in report
    assert "AnchorCoT aligns anchor selection and geometric reasoning." in report


def test_build_workflow_report_markdown_formats_rebuttal_output():
    metadata = {
        "rebuttal_venue": "ICML",
        "rebuttal_round": "initial",
        "rebuttal_character_limit": 5000,
        "rebuttal_character_count": 4280,
        "paste_ready_text": "We thank the reviewers and clarify the novelty and empirical scope of AnchorCoT.",
        "stage_outputs": {
            "normalize_reviews": {
                "content": "# REVIEWS_RAW\n\nReviewer 1: novelty is unclear.\nReviewer 2: empirical support is limited.\n",
            },
            "issue_board": {
                "content": "# ISSUE_BOARD\n\n- R1-C1: novelty clarification\n- R2-C1: empirical support\n",
            },
            "strategy_plan": {
                "content": "# STRATEGY_PLAN\n\n- opener covers shared concerns\n- reserve budget for reviewer-specific evidence\n",
            },
            "stress_test": {
                "content": "# MCP_STRESS_TEST\n\n- needs tighter grounding around baseline deltas\n",
            },
            "finalize_package": {
                "content": "# REBUTTAL_DRAFT\n\nWe thank the reviewers and respond to each concern below.\n",
            },
        },
        "workflow_output_markdown": "# REBUTTAL_DRAFT\n\nWe thank the reviewers and respond to each concern below.\n",
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.rebuttal.value,
        project_label="AnchorCot Rebuttal",
        prompt="回复 ICML 审稿意见",
        metadata=metadata,
    )

    assert report is not None
    assert "# Rebuttal 报告" in report
    assert "## Issue Board" in report
    assert "## Strategy Plan" in report
    assert "ICML" in report
    assert "4280" in report
    assert "We thank the reviewers" in report


@pytest.mark.parametrize(
    ("workflow_type", "metadata", "expected_title", "expected_snippet"),
    [
        (
            ProjectWorkflowType.paper_plan.value,
            {
                "paper_venue": "NeurIPS",
                "paper_template": "neurips",
                "stage_outputs": {
                    "outline_manuscript": {
                        "content": "# PAPER_PLAN\n\n## Claims-Evidence Matrix\n- Anchor selection improves spatial grounding.\n",
                    }
                },
            },
            "# 论文规划报告",
            "Claims-Evidence Matrix",
        ),
        (
            ProjectWorkflowType.paper_figure.value,
            {
                "paper_venue": "ICLR",
                "stage_outputs": {
                    "design_figures": {
                        "content": "# FIGURE_PLAN\n\n- Fig 1: AnchorCoT overview\n- Table 1: benchmark comparison\n",
                    }
                },
            },
            "# 图表规划报告",
            "AnchorCoT overview",
        ),
        (
            ProjectWorkflowType.paper_write.value,
            {
                "paper_venue": "ICLR",
                "paper_template": "iclr",
                "stage_outputs": {
                    "draft_sections": {
                        "content": "# PAPER_WRITE\n\n## Abstract\nAnchorCoT aligns anchors and process rewards.\n",
                    }
                },
            },
            "# 论文初稿报告",
            "AnchorCoT aligns anchors and process rewards.",
        ),
    ],
)
def test_build_workflow_report_markdown_formats_paper_subworkflow_output(
    workflow_type: str,
    metadata: dict,
    expected_title: str,
    expected_snippet: str,
):
    report = build_workflow_report_markdown(
        workflow_type=workflow_type,
        project_label="AnchorCot Paper",
        prompt="生成论文子流程产物",
        metadata=metadata,
    )

    assert report is not None
    assert expected_title in report
    assert expected_snippet in report


def test_build_workflow_report_markdown_formats_paper_compile_output():
    metadata = {
        "compiled_pdf_paths": ["/srv/research/anchorcot/paper/main.pdf"],
        "stage_outputs": {
            "run_compile": {
                "content": "# PAPER_COMPILE\n\n- latexmk success\n",
                "command": "latexmk -pdf paper/main.tex",
                "exit_code": 0,
                "stdout": "Latexmk: All targets up-to-date\n",
                "stderr": "",
            }
        },
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.paper_compile.value,
        project_label="AnchorCot Compile",
        prompt="编译论文草稿",
        metadata=metadata,
    )

    assert report is not None
    assert "# 论文编译报告" in report
    assert "latexmk -pdf paper/main.tex" in report
    assert "/srv/research/anchorcot/paper/main.pdf" in report
    assert "Latexmk: All targets up-to-date" in report


def test_build_workflow_report_markdown_formats_paper_improvement_output():
    metadata = {
        "paper_improvement_scores": {
            "round_1": 6.8,
            "round_2": 7.5,
        },
        "paper_improvement_verdicts": {
            "round_1": "almost",
            "round_2": "ready",
        },
        "paper_improvement_action_items": {
            "round_1": ["补充 anchor ablation"],
            "round_2": ["压缩摘要并强化贡献表述"],
        },
        "stage_outputs": {
            "diagnose_draft": {
                "content": "# Review Round 1\n\nScore: 6.8\n- 主实验还需要更强对比。\n",
            },
            "revise_sections": {
                "content": "# Revision Notes\n\n- 增加 anchor ablation 与误差分析。\n",
            },
            "final_check": {
                "content": "# Review Round 2\n\nScore: 7.5\n- 可进入下一轮投稿准备。\n",
            },
        },
        "workflow_output_markdown": "# paper-score-progression\n\n- Round 1: 6.8\n- Round 2: 7.5\n",
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.paper_improvement.value,
        project_label="AnchorCot Improvement",
        prompt="完成两轮改稿",
        metadata=metadata,
    )

    assert report is not None
    assert "# 论文改稿报告" in report
    assert "Round 1: score=`6.8`, verdict=`almost`" in report
    assert "Round 2: score=`7.5`, verdict=`ready`" in report
    assert "补充 anchor ablation" in report


def test_build_workflow_report_markdown_formats_full_pipeline_output():
    metadata = {
        "execution_command": "python train.py --epochs 1",
        "effective_execution_command": "conda run -n anchorcot python train.py --epochs 1",
        "execution_workspace": "/srv/research/anchorcot",
        "execution_result": {
            "command": "python train.py --epochs 1",
            "effective_command": "conda run -n anchorcot python train.py --epochs 1",
            "workspace_path": "/srv/research/anchorcot",
            "exit_code": 0,
            "stdout": "epoch=1 ok\n",
            "stderr": "",
            "success": True,
        },
        "stage_outputs": {
            "review_prior_work": {
                "content": "# IDEA_REPORT\n\n- AnchorCoT should be the primary direction.\n",
            },
            "synthesize_findings": {
                "content": "# AUTO_REVIEW\n\n- verdict: almost\n- next: tighten evaluation narrative\n",
            },
            "handoff_output": {
                "content": "# Final Report\n\n- Ready for the next implementation round.\n",
            },
        },
        "workflow_output_markdown": "# Final Report\n\n- Ready for the next implementation round.\n",
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.full_pipeline.value,
        project_label="AnchorCot Pipeline",
        prompt="完成从想法筛选到实验验证的全流程",
        metadata=metadata,
    )

    assert report is not None
    assert "# 科研流程交付报告" in report
    assert "## 想法关口" in report
    assert "## 自动评审总结" in report
    assert "conda run -n anchorcot python train.py --epochs 1" in report
    assert "Ready for the next implementation round." in report


def test_build_workflow_report_markdown_formats_auto_review_loop_output():
    metadata = {
        "execution_command": "python loop.py --round 1",
        "effective_execution_command": "conda run -n review python loop.py --round 1",
        "execution_workspace": "/srv/research/anchorcot/review",
        "stage_outputs": {
            "plan_cycle": {
                "content": "# Plan\n\n- improve error analysis and strengthen claims\n",
            }
        },
        "iterations": [
            {
                "iteration": 1,
                "execution": {
                    "command": "python loop.py --round 1",
                    "effective_command": "conda run -n review python loop.py --round 1",
                    "command_workspace_path": "/srv/research/anchorcot/review",
                },
                "execution_summary": "# Execute\n\n- generated revised note bundle\n",
                "review": {
                    "score": 6.8,
                    "verdict": "almost",
                    "summary": "need stronger empirical grounding",
                    "issues": ["主结果论证还不够强"],
                    "next_actions": ["补充对比实验"],
                    "pending_experiments": ["增加 anchor ablation"],
                },
            },
            {
                "iteration": 2,
                "execution": {
                    "command": "python loop.py --round 2",
                    "command_workspace_path": "/srv/research/anchorcot/review",
                },
                "execution_summary": "# Execute\n\n- incorporated ablation feedback\n",
                "review": {
                    "score": 7.4,
                    "verdict": "ready",
                    "summary": "sufficient for next submission step",
                    "issues": [],
                    "next_actions": ["准备最终提交材料"],
                    "pending_experiments": [],
                },
            },
        ],
        "workflow_output_markdown": "# 自动评审循环报告\n\nFull reviewer raw response\n",
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.auto_review_loop.value,
        project_label="AnchorCot Auto Review",
        prompt="循环评审并逐轮收敛论文质量",
        metadata=metadata,
    )

    assert report is not None
    assert "# 自动评审循环报告" in report
    assert "## 循环计划" in report
    assert "### Round 1" in report
    assert "score=`7.4`, verdict=`ready`" in report
    assert "Full reviewer raw response" in report


def test_build_workflow_report_markdown_formats_monitor_experiment_output():
    metadata = {
        "remote_session_name": "aris-anchorcot-monitor",
        "remote_session_names": ["aris-anchorcot-monitor", "aris-anchorcot-monitor-ablation"],
        "stage_outputs": {
            "inspect_runs": {
                "content": (
                    "# MONITOR_INSPECTION\n\n"
                    "- Workspace: `/srv/research/anchorcot`\n"
                    "- Server: `ssh-main`\n"
                    "- Status: `ok`\n"
                ),
            },
            "collect_signals": {
                "content": (
                    "# MONITOR_SIGNALS\n\n"
                    "- Workspace: `/srv/research/anchorcot`\n"
                    "- Server: `ssh-main`\n"
                    "- Tracked Session: `aris-anchorcot-monitor`\n\n"
                    "## Alerts\n- 日志检测到 `error` 迹象\n\n"
                    "## Result Comparison\n"
                    "- Metric: `accuracy`\n"
                    "- Baseline: `baseline`\n"
                ),
            },
        },
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.monitor_experiment.value,
        project_label="AnchorCot Monitor",
        prompt="监控主实验和消融实验",
        metadata=metadata,
    )

    assert report is not None
    assert "# 实验监控报告" in report
    assert "正在追踪 2 个后台会话" in report
    assert "`accuracy`" in report
    assert "日志检测到 `error` 迹象" in report


def test_build_workflow_report_markdown_formats_experiment_audit_output():
    metadata = {
        "execution_workspace": "/srv/research/anchorcot",
        "stage_outputs": {
            "review_integrity": {
                "content": (
                    "{\n"
                    '  "overall_verdict": "WARN",\n'
                    '  "integrity_status": "warn",\n'
                    '  "evaluation_type": "mixed",\n'
                    '  "summary": "主结果可复核，但 scope 和分数归一化需要补说明。",\n'
                    '  "checks": {\n'
                    '    "gt_provenance": {"status": "PASS", "evidence": ["data/test.json:1"], "details": "ground truth 来源清晰。"},\n'
                    '    "score_normalization": {"status": "WARN", "evidence": ["results/metrics.json:4"], "details": "归一化分母解释不足。"},\n'
                    '    "result_existence": {"status": "PASS", "evidence": ["results/metrics.json:2"], "details": "关键结果文件存在。"},\n'
                    '    "dead_code": {"status": "PASS", "evidence": ["eval_metric.py:12"], "details": "评测函数被主流程调用。"},\n'
                    '    "scope": {"status": "WARN", "evidence": ["EXPERIMENT_TRACKER.md:8"], "details": "只跑了单 seed。"},\n'
                    '    "eval_type": {"status": "PASS", "evidence": ["configs/eval.yaml:1"], "details": "属于 mixed。"}\n'
                    "  },\n"
                    '  "action_items": ["补充多 seed 结果", "解释归一化口径"],\n'
                    '  "claims": [{"id": "C1", "impact": "needs_qualifier", "details": "需要收窄泛化表述"}]\n'
                    "}\n"
                ),
            }
        },
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.experiment_audit.value,
        project_label="AnchorCot Audit",
        prompt="在进入 auto-review-loop 前检查当前实验完整性",
        metadata=metadata,
    )

    assert report is not None
    assert "# Experiment Audit Report" in report
    assert "## Checks" in report
    assert "Ground Truth Provenance" in report
    assert "/srv/research/anchorcot" in report
    assert "补充多 seed 结果" in report
    assert "C1: needs_qualifier" in report


def test_build_workflow_report_markdown_formats_sync_workspace_output():
    metadata = {
        "project_workspace_path": "/srv/research/source",
        "project_workspace_server_id": "ssh-source",
        "target_workspace_path": "/srv/research/target",
        "target_workspace_server_id": "ssh-target",
        "sync_strategy": "remote_bridge_copy",
        "stage_outputs": {
            "scan_diff": {
                "content": "# SYNC_PREVIEW\n\n- Candidate Files: 12\n",
            },
            "sync_paths": {
                "content": (
                    "# SYNC_RESULT\n\n"
                    "- Mode: `remote_to_remote`\n"
                    "- Status: `completed`\n"
                    "- Synced Files: 12\n"
                ),
            },
            "validate_state": {
                "content": "# SYNC_VALIDATION\n\n- Exists: True\n",
            },
        },
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.sync_workspace.value,
        project_label="AnchorCot Sync",
        prompt="同步远程代码工作区",
        metadata=metadata,
    )

    assert report is not None
    assert "# 工作区同步报告" in report
    assert "/srv/research/source" in report
    assert "/srv/research/target" in report
    assert "`remote_bridge_copy`" in report
    assert "`remote_to_remote`" in report


def test_build_workflow_report_markdown_formats_custom_run_output():
    metadata = {
        "execution_result": {
            "command": "python scripts/custom_pipeline.py",
            "workspace_path": "/srv/research/anchorcot",
            "exit_code": 0,
        },
        "workflow_output_markdown": "# Custom Output\n\n- generated implementation checklist\n",
    }

    report = build_workflow_report_markdown(
        workflow_type=ProjectWorkflowType.custom_run.value,
        project_label="AnchorCot Custom",
        prompt="运行自定义流水线",
        metadata=metadata,
    )

    assert report is not None
    assert "# 自定义工作流报告" in report
    assert "python scripts/custom_pipeline.py" in report
    assert "/srv/research/anchorcot" in report
    assert "generated implementation checklist" in report


def test_workspace_preview_content_rerenders_primary_run_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    workspace_dir = tmp_path / "anchorcot"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    with db.session_scope() as session:
        project_repo = ProjectRepository(session)
        project = project_repo.create_project(
            name="AnchorCot",
            description="preview formatter test",
            workdir=str(workspace_dir),
        )
        run = project_repo.create_run(
            project_id=project.id,
            workflow_type=ProjectWorkflowType.idea_discovery,
            prompt="评估 AnchorCoT",
            title="AnchorCot Idea Discovery",
            status=ProjectRunStatus.succeeded,
            active_phase="completed",
            summary="done",
            workdir=str(workspace_dir),
            metadata={
                "created_ideas": [
                    {
                        "title": "AnchorCoT",
                        "content": "使用多粒度过程奖励对齐人类空间认知过程。",
                    }
                ],
                "stage_outputs": {
                    "collect_context": {"content": "## 文献地形\n- 现有 PRM 缺少可验证中间奖励。"},
                    "verify_novelty": {"content": "## 查新判断\n- 与空间定位任务的锚点奖励结合较少。"},
                    "external_review": {"content": "## 外部评审\n- 建议增加更细的失败案例分析。"},
                },
                "workflow_output_markdown": "旧的短输出",
            },
        )
        run_directory = workspace_dir / ".auto-researcher" / "aris-runs" / run.id
        result_path = run_directory / "IDEA_REPORT.md"
        project_repo.update_run(
            run.id,
            run_directory=str(run_directory),
            result_path=str(result_path),
            metadata={
                "artifact_refs": [
                    {
                        "path": str(result_path),
                        "relative_path": f".auto-researcher/aris-runs/{run.id}/IDEA_REPORT.md",
                        "kind": "report",
                    }
                ],
                "created_ideas": [
                    {
                        "title": "AnchorCoT",
                        "content": "使用多粒度过程奖励对齐人类空间认知过程。",
                    }
                ],
                "stage_outputs": {
                    "collect_context": {"content": "## 文献地形\n- 现有 PRM 缺少可验证中间奖励。"},
                    "verify_novelty": {"content": "## 查新判断\n- 与空间定位任务的锚点奖励结合较少。"},
                    "external_review": {"content": "## 外部评审\n- 建议增加更细的失败案例分析。"},
                },
                "workflow_output_markdown": "旧的短输出",
            },
        )
        run_id = run.id

    preview = agent_workspace_router._resolve_workspace_preview_content(
        str(run_directory),
        "IDEA_REPORT.md",
        "# stale raw artifact",
    )

    assert "# Idea Discovery Report" in preview
    assert "## Ranked Ideas" in preview
    assert "AnchorCoT" in preview
    assert "stale raw artifact" not in preview

    persisted_preview = agent_workspace_router._resolve_workspace_preview_content(
        str(workspace_dir),
        f".auto-researcher/aris-runs/{run_id}/IDEA_REPORT.md",
        "# stale raw artifact",
    )

    assert "# Idea Discovery Report" in persisted_preview
    assert "## Ranked Ideas" in persisted_preview
    assert "AnchorCoT" in persisted_preview
    assert "stale raw artifact" not in persisted_preview


def test_merge_artifact_refs_dedupes_same_path_and_keeps_richer_kind():
    merged = projects_router._merge_artifact_refs(
        [
            {
                "path": "D:/Desktop/ResearchOS/projects/anchorcot/.auto-researcher/aris-runs/run-1/IDEA_REPORT.md",
                "relative_path": "IDEA_REPORT.md",
                "kind": "artifact",
                "updated_at": "2026-03-19T19:00:00+08:00",
            }
        ],
        [
            {
                "path": "D:/Desktop/ResearchOS/projects/anchorcot/.auto-researcher/aris-runs/run-1/IDEA_REPORT.md",
                "relative_path": ".auto-researcher/aris-runs/run-1/IDEA_REPORT.md",
                "kind": "report",
                "size_bytes": 1024,
            }
        ],
    )

    assert len(merged) == 1
    assert merged[0]["relative_path"] == "IDEA_REPORT.md"
    assert merged[0]["kind"] == "report"
    assert merged[0]["size_bytes"] == 1024
