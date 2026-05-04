"""Literature review workflow handler extracted from the main runner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from packages.ai.project.report_formatter import format_literature_review_report

if TYPE_CHECKING:
    from packages.ai.project.workflow_runner import ProgressCallback, WorkflowContext


def execute_literature_review(
    context: WorkflowContext,
    progress_callback: ProgressCallback | None,
    *,
    resume_stage_id: str | None = None,
    runtime: Any,
) -> dict[str, Any]:
    run = context.run
    project = context.project
    runtime._set_stage_state(
        run.id,
        "collect_context",
        status="completed",
        message="项目、论文与仓库上下文已完成整理。",
        progress_pct=24,
    )
    runtime._patch_run(
        run.id,
        active_phase="synthesize_evidence",
        summary="正在生成项目级文献综述。",
    )
    runtime._set_stage_state(
        run.id,
        "synthesize_evidence",
        status="running",
        message="正在生成项目级文献综述。",
        progress_pct=36,
    )
    runtime._emit_progress(progress_callback, "正在生成项目级文献综述。", 36)

    synthesize_payload = runtime._stage_output_payload(context, "synthesize_evidence")
    if resume_stage_id == "deliver_review":
        markdown = runtime._stage_output_content(context, "synthesize_evidence")
        if not markdown:
            raise RuntimeError("恢复文献综述失败：缺少阅读分析阶段产物。")
        review_execution = synthesize_payload
        llm_result = runtime.LLMResult(content=markdown)
    else:
        prompt = runtime._build_literature_review_prompt(context)
        review_execution = runtime._invoke_role_markdown(
            context,
            "synthesize_evidence",
            prompt,
            stage="project_literature_review",
            max_tokens=2400,
            request_timeout=180,
        )
        llm_result = review_execution["result"]
        markdown = runtime._resolve_literature_markdown(context, llm_result)
        runtime._record_stage_output(
            run.id,
            "synthesize_evidence",
            {
                "summary": runtime._markdown_excerpt(markdown),
                "content": markdown,
                "provider": review_execution.get("provider"),
                "model": review_execution.get("model"),
                "variant": review_execution.get("variant"),
                "model_role": review_execution.get("model_role"),
                "model_source": review_execution.get("model_source"),
                "role_template_id": review_execution.get("role_template_id"),
            },
        )
    markdown = format_literature_review_report(project.name, run.prompt, markdown)
    excerpt = runtime._markdown_excerpt(markdown)
    runtime._set_stage_state(
        run.id,
        "synthesize_evidence",
        status="completed",
        message="文献综述主体已生成，准备写回结果。",
        progress_pct=64,
    )
    if resume_stage_id != "deliver_review":
        runtime._maybe_pause_after_stage(
            context,
            "synthesize_evidence",
            "deliver_review",
            stage_summary=excerpt,
        )

    runtime._patch_run(
        run.id,
        active_phase="deliver_review",
        summary="正在写回综述结果。",
    )
    runtime._set_stage_state(
        run.id,
        "deliver_review",
        status="running",
        message="正在写回综述结果。",
        progress_pct=78,
    )
    runtime._emit_progress(progress_callback, "正在写回综述结果。", 78)

    generated_content_id = None
    if context.selected_papers:
        with runtime.session_scope() as session:
            generated = runtime.GeneratedContentRepository(session).create(
                content_type="project_literature_review",
                title=f"{project.name} 文献综述",
                markdown=markdown,
                keyword=project.name,
                paper_id=context.selected_papers[0].id,
                metadata_json={
                    "project_id": project.id,
                    "run_id": run.id,
                    "workflow_type": run.workflow_type.value,
                },
            )
            generated_content_id = generated.id

    artifact_refs: list[dict[str, Any]] = []
    report_artifact = runtime._write_run_artifact(
        context, "reports/literature-review.md", markdown, kind="report"
    )
    if report_artifact:
        artifact_refs.append(report_artifact)
    log_artifact = runtime._write_run_log(
        context,
        "\n".join(
            [
                f"# {project.name} 文献综述运行日志",
                "",
                f"- run_id: {run.id}",
                f"- workflow: {run.workflow_type.value}",
                "- status: succeeded",
                f"- completed_at: {runtime._iso_now()}",
                f"- generated_content_id: {generated_content_id or 'N/A'}",
                "",
                "## 摘要",
                excerpt or "已生成项目级文献综述。",
            ]
        ).strip()
        + "\n",
    )
    if log_artifact:
        artifact_refs.insert(0, log_artifact)

    metadata_updates = {
        "workflow_output_markdown": markdown,
        "workflow_output_excerpt": excerpt,
        "paper_ids": [paper.id for paper in context.selected_papers],
        "repo_ids": [repo.id for repo in context.selected_repos],
        "generated_content_id": generated_content_id,
        "llm_mode": runtime._llm_mode(llm_result),
        "artifact_refs": artifact_refs,
        "completed_at": runtime._iso_now(),
    }
    runtime._patch_run(
        run.id,
        status=runtime.ProjectRunStatus.succeeded,
        active_phase="completed",
        summary=excerpt or "已生成项目级文献综述。",
        finished_at=runtime.datetime.now(runtime.UTC),
        metadata_updates=metadata_updates,
    )
    runtime._set_stage_state(
        run.id,
        "deliver_review",
        status="completed",
        message="文献综述结果已写回项目。",
        progress_pct=100,
    )
    runtime._record_stage_output(
        run.id,
        "deliver_review",
        {
            "summary": excerpt,
            "content": markdown,
            "provider": review_execution.get("provider"),
            "model": review_execution.get("model"),
            "variant": review_execution.get("variant"),
            "model_role": review_execution.get("model_role"),
            "model_source": review_execution.get("model_source"),
            "role_template_id": review_execution.get("role_template_id"),
            "generated_content_id": generated_content_id,
            "artifact_refs": artifact_refs,
        },
    )
    runtime._emit_progress(progress_callback, "文献综述已完成。", 100)

    result = {
        "run_id": run.id,
        "workflow_type": run.workflow_type.value,
        "summary": excerpt,
        "markdown": markdown,
        "generated_content_id": generated_content_id,
        "artifact_refs": artifact_refs,
    }
    if run.task_id:
        runtime.global_tracker.set_metadata(run.task_id, {"artifact_refs": artifact_refs})
        runtime.global_tracker.set_result(run.task_id, result)
    return result
