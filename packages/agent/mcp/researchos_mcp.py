from __future__ import annotations

import anyio
import copy
import logging
import re
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import mcp.types as mcp_types
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage

from packages.ai.paper.paper_ops_service import (
    FigureExtractionEmptyError,
    PaperPdfUnavailableError,
    extract_paper_figures_payload as _extract_paper_figures_payload,
    extract_uploaded_pdf_metadata as _extract_uploaded_pdf_metadata,
    normalize_manual_paper_id as _normalize_manual_paper_id,
    safe_uploaded_filename as _safe_uploaded_filename,
)
from packages.agent import research_tool_runtime as research_runtime
from packages.agent.mcp.researchos_mcp_registry import RESEARCHOS_MCP_SERVER_NAME
from packages.agent.mcp.researchos_mcp_runtime import register_dynamic_bridge_tools
from packages.ai.paper.figure_service import FigureService
from packages.ai.paper.pipelines import PaperPipelines
from packages.ai.research.reasoning_service import ReasoningService
from packages.agent.tools.tool_runtime import ToolProgress, ToolResult
from packages.ai.research.web_search_service import search_web as run_web_search
from packages.config import get_settings
from packages.domain.schemas import PaperCreate
from packages.domain.task_tracker import global_tracker
from packages.integrations.arxiv_client import ArxivClient
from packages.storage.db import session_scope
from packages.storage.models import AnalysisReport, Paper, TopicSubscription
from packages.storage.repositories import PaperRepository

logger = logging.getLogger(__name__)

server = FastMCP(
    name=RESEARCHOS_MCP_SERVER_NAME,
    instructions=(
        "ResearchOS 内置论文工作流工具。"
        "也提供网页搜索工具，可用于官网、项目、新闻和通用资料检索。"
        "优先先用 search_papers 或 search_literature / search_arxiv 确认 paper_id，再执行粗读、精读、推理链、图表提取等分析工具。"
        "如果论文还没入库但用户只是想快速判断值不值得读，优先用 preview_external_paper_head / preview_external_paper_section 做外部预读。"
        "如果用户是在问速览、贡献、创新点或是否值得继续看，优先使用 skim_paper。"
        "如果用户是在问方法、模块、训练流程和实现细节，优先使用 deep_read_paper。"
        "如果用户是在问实验解读、优缺点、证据充分性或综合判断，优先使用 get_paper_analysis / analyze_paper_rounds。"
        "如果用户是在问架构图、框架图、原图引用或图表解读，优先使用 analyze_figures。"
        "如果用户需要精确公式、变量、表格数值、超参数或原句，优先核对原文 Markdown/PDF/图表，不要只依赖摘要型分析。"
        "不要为了图表问题额外调用 get_paper_analysis，除非用户明确要求三轮分析、结构化笔记或整篇论文总结。"
        "兼容旧版的 paper_* / task_* 工具仍保留在执行层，但默认主链路优先使用 skim_paper、deep_read_paper、analyze_paper_rounds、analyze_figures 这类直接返回结果的工具。"
    ),
)



def _safe_pdf_stem(value: str | None) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return stem[:120] or uuid4().hex


def _paper_citation_count(paper: Paper) -> int:
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    for key in ("citationCount", "citation_count"):
        raw = metadata.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return 0


def _paper_keywords(paper: Paper) -> list[str]:
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    raw = metadata.get("keywords") or []
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        value = str(item or "").strip()
        if value:
            result.append(value)
    return result


def _paper_title_zh(paper: Paper) -> str | None:
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    value = str(metadata.get("title_zh") or "").strip()
    return value or None


def _paper_abstract_zh(paper: Paper) -> str | None:
    metadata = dict(getattr(paper, "metadata_json", None) or {})
    value = str(metadata.get("abstract_zh") or "").strip()
    return value or None


def _paper_read_status(paper: Paper) -> str:
    status = getattr(paper, "read_status", None)
    return getattr(status, "value", str(status or "unread"))


def _paper_summary(paper: Paper) -> dict[str, Any]:
    return {
        "id": str(paper.id),
        "title": paper.title,
        "title_zh": _paper_title_zh(paper),
        "arxiv_id": paper.arxiv_id,
        "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
        "read_status": _paper_read_status(paper),
        "citation_count": _paper_citation_count(paper),
        "keywords": _paper_keywords(paper),
        "has_pdf": bool(paper.pdf_path and Path(paper.pdf_path).exists()),
        "pdf_path": paper.pdf_path,
    }


def _paper_figure_refs(items: list[dict[str, Any]], *, limit: int | None = 6) -> list[dict[str, Any]]:
    visible = items if limit is None or limit <= 0 else items[:limit]
    refs: list[dict[str, Any]] = []
    for item in visible:
        refs.append(
            {
                "id": item.get("id"),
                "figure_label": item.get("figure_label"),
                "page_number": item.get("page_number"),
                "image_type": item.get("image_type"),
                "caption": item.get("caption"),
                "image_url": item.get("image_url"),
            }
        )
    return refs


def _paper_detail_payload(paper: Paper) -> dict[str, Any]:
    skim_report: dict[str, Any] | None = None
    deep_report: dict[str, Any] | None = None
    with session_scope() as session:
        report = session.query(AnalysisReport).filter(AnalysisReport.paper_id == str(paper.id)).one_or_none()
        if report and report.summary_md:
            skim_report = {
                "summary_md": report.summary_md,
                "skim_score": report.skim_score,
                "key_insights": copy.deepcopy(report.key_insights or {}),
            }
        if report and report.deep_dive_md:
            deep_report = {
                "deep_dive_md": report.deep_dive_md,
                "key_insights": copy.deepcopy(report.key_insights or {}),
            }

    metadata = dict(getattr(paper, "metadata_json", None) or {})
    reasoning = metadata.get("reasoning_chain")
    if not isinstance(reasoning, dict):
        reasoning = None
    figure_items = FigureService.get_paper_analyses(UUID(str(paper.id)))
    paper_id = str(paper.id)
    normalized_figures: list[dict[str, Any]] = []
    for item in figure_items:
        payload = dict(item)
        if payload.get("has_image") and payload.get("id"):
            payload["image_url"] = f"/papers/{paper_id}/figures/{payload['id']}/image"
        else:
            payload["image_url"] = None
        normalized_figures.append(payload)

    result = _paper_summary(paper)
    result.update(
        {
            "abstract": paper.abstract,
            "abstract_zh": _paper_abstract_zh(paper),
            "skim_report": skim_report,
            "deep_report": deep_report,
            "reasoning_chain": reasoning,
            "analysis_rounds": metadata.get("analysis_rounds") if isinstance(metadata.get("analysis_rounds"), dict) else None,
            "figure_count": len(normalized_figures),
            "figure_refs": _paper_figure_refs(normalized_figures),
            "metadata": metadata,
        }
    )
    return result


def _search_candidates(query: str, limit: int) -> list[dict[str, Any]]:
    with session_scope() as session:
        repo = PaperRepository(session)
        papers, _ = repo.list_paginated(
            page=1,
            page_size=max(1, min(limit, 20)),
            search=query.strip() or None,
            sort_by="impact",
            sort_order="desc",
        )
        return [_paper_summary(paper) for paper in papers]


def _resolve_paper(paper_ref: str) -> Paper:
    raw = str(paper_ref or "").strip()
    if not raw:
        raise ValueError("paper_ref 不能为空")

    normalized_arxiv = _normalize_manual_paper_id(raw)
    with session_scope() as session:
        repo = PaperRepository(session)

        def _detach(paper: Paper) -> Paper:
            # Materialize scalar fields before leaving the session so MCP tools
            # can safely read them after session_scope() closes.
            _ = (
                paper.id,
                paper.title,
                paper.arxiv_id,
                paper.publication_date,
                paper.abstract,
                paper.pdf_path,
                paper.read_status,
                paper.metadata_json,
            )
            session.expunge(paper)
            return paper

        if normalized_arxiv:
            paper = repo.get_by_arxiv_id(normalized_arxiv)
            if paper is not None:
                return _detach(paper)

        try:
            paper_uuid = UUID(raw)
        except ValueError:
            paper_uuid = None

        if paper_uuid is not None:
            return _detach(repo.get_by_id(paper_uuid))

        candidates = repo.full_text_candidates(raw, limit=5)
        if len(candidates) == 1:
            return _detach(candidates[0])
        if not candidates:
            raise ValueError(f"未找到论文: {raw}")
        raise ValueError(
            "查询到多篇候选论文，请先使用 search_papers 确认具体 paper_id。候选: "
            + ", ".join(f"{paper.title} ({paper.id})" for paper in candidates[:5])
        )


def _paper_title_short(paper_id: UUID) -> str:
    with session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.get_by_id(paper_id)
        return (paper.title or str(paper_id)[:8])[:30]


def _resolve_paper_id_value(paper_ref: str) -> UUID:
    paper = _resolve_paper(paper_ref)
    return UUID(str(paper.id))


def _tool_result_payload(result: ToolResult) -> dict[str, Any]:
    payload = {
        "success": bool(result.success),
        "summary": str(result.summary or ""),
        "data": dict(result.data or {}),
    }
    internal_data = dict(result.internal_data or {})
    if internal_data:
        payload["internal_data"] = internal_data
        display_data = internal_data.get("display_data")
        if isinstance(display_data, dict):
            payload["display_data"] = display_data
    return payload


@asynccontextmanager
async def _agent_stdio_server():
    stdin = anyio.wrap_file(sys.stdin.buffer)
    stdout = anyio.wrap_file(sys.stdout.buffer)
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def _stdin_reader() -> None:
        try:
            async with read_stream_writer:
                while True:
                    headers: dict[str, str] = {}
                    while True:
                        line = await stdin.readline()
                        if not line:
                            return
                        if line in {b"\r\n", b"\n"}:
                            break
                        decoded = line.decode("utf-8", errors="replace").strip()
                        if ":" not in decoded:
                            continue
                        name, value = decoded.split(":", 1)
                        headers[name.strip().lower()] = value.strip()

                    content_length = int(headers.get("content-length") or 0)
                    if content_length <= 0:
                        continue
                    body = await stdin.read(content_length)
                    if not body:
                        return
                    try:
                        message = mcp_types.JSONRPCMessage.model_validate_json(body.decode("utf-8"))
                    except Exception as exc:  # pragma: no cover
                        await read_stream_writer.send(exc)
                        continue
                    await read_stream_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def _stdout_writer() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    body = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    ).encode("utf-8")
                    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                    await stdout.write(header + body)
                    await stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        tg.start_soon(_stdin_reader)
        tg.start_soon(_stdout_writer)
        yield read_stream, write_stream


def _submit_runtime_iterator_task(
    *,
    task_type: str,
    title: str,
    message: str,
    iterator_factory,
) -> dict[str, Any]:
    def _fn(progress_callback=None):
        final_result: ToolResult | None = None
        for item in iterator_factory():
            if isinstance(item, ToolProgress):
                if progress_callback:
                    progress_callback(
                        item.message,
                        max(0, int(item.current or 0)),
                        max(1, int(item.total or 100)),
                    )
                continue
            if isinstance(item, ToolResult):
                final_result = item
        if final_result is None:
            return {
                "success": False,
                "summary": "工具没有返回最终结果",
                "data": {},
            }
        return _tool_result_payload(final_result)

    task_id = global_tracker.submit(
        task_type=task_type,
        title=title,
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": message}


def _resolve_runtime_iterator_payload(iterator_factory) -> dict[str, Any]:
    final_result: ToolResult | None = None
    progress_messages: list[dict[str, Any]] = []
    for item in iterator_factory():
        if isinstance(item, ToolProgress):
            progress_messages.append(
                {
                    "message": item.message,
                    "current": max(0, int(item.current or 0)),
                    "total": max(1, int(item.total or 100)),
                }
            )
            continue
        if isinstance(item, ToolResult):
            final_result = item
    if final_result is None:
        payload = {"success": False, "summary": "工具没有返回最终结果", "data": {}}
    else:
        payload = _tool_result_payload(final_result)
    if progress_messages:
        payload["progress"] = progress_messages
    return payload


def _submit_skim_task(paper_id: UUID) -> dict[str, Any]:
    title = _paper_title_short(paper_id)

    def _fn(progress_callback=None):
        skim = PaperPipelines().skim(paper_id, progress_callback=progress_callback)
        return skim.model_dump()

    task_id = global_tracker.submit(
        task_type="skim",
        title=f"粗读: {title}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "粗读任务已启动"}


def _submit_deep_task(paper_id: UUID) -> dict[str, Any]:
    title = _paper_title_short(paper_id)

    def _fn(progress_callback=None):
        deep = PaperPipelines().deep_dive(paper_id, progress_callback=progress_callback)
        return deep.model_dump()

    task_id = global_tracker.submit(
        task_type="deep_read",
        title=f"精读: {title}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "精读任务已启动"}


def _submit_reasoning_task(paper_id: UUID) -> dict[str, Any]:
    title = _paper_title_short(paper_id)

    def _fn(progress_callback=None):
        return ReasoningService().analyze(paper_id, progress_callback=progress_callback)

    task_id = global_tracker.submit(
        task_type="reasoning",
        title=f"推理链: {title}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "推理链分析任务已启动"}


def _submit_embed_task(paper_id: UUID) -> dict[str, Any]:
    title = _paper_title_short(paper_id)

    def _fn(progress_callback=None):
        PaperPipelines().embed_paper(paper_id, progress_callback=progress_callback)
        return {"status": "embedded", "paper_id": str(paper_id)}

    task_id = global_tracker.submit(
        task_type="embed",
        title=f"嵌入: {title}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "向量化任务已启动"}


def _submit_figure_task(
    paper_id: UUID,
    *,
    max_figures: int,
    extract_mode: str | None,
) -> dict[str, Any]:
    title = _paper_title_short(paper_id)

    def _fn(progress_callback=None):
        try:
            return _extract_paper_figures_payload(
                paper_id=paper_id,
                max_figures=max_figures,
                extract_mode=extract_mode,
                progress_callback=progress_callback,
            )
        except (PaperPdfUnavailableError, FigureExtractionEmptyError) as exc:
            raise RuntimeError(str(exc)) from exc

    task_id = global_tracker.submit(
        task_type="figure_extract",
        title=f"图表提取: {title}",
        fn=_fn,
        total=100,
    )
    return {"task_id": task_id, "status": "running", "message": "图表提取任务已启动"}


def _copy_local_pdf(pdf_path: str, title: str | None, arxiv_id: str | None, topic_id: str | None) -> dict[str, Any]:
    source = Path(pdf_path).expanduser().resolve()
    if not source.exists():
        raise ValueError(f"PDF 文件不存在: {source}")
    if not source.is_file():
        raise ValueError(f"不是文件: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError("仅支持导入 PDF 文件")

    settings = get_settings()
    upload_dir = settings.pdf_storage_root / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_path = upload_dir / f"{_safe_uploaded_filename(source.name)}-{uuid4().hex[:8]}.pdf"
    shutil.copy2(source, stored_path)

    normalized_id = _normalize_manual_paper_id(arxiv_id)
    fallback_title = str(title or "").strip() or source.stem.replace("_", " ").replace("-", " ").strip()
    extracted_title, extracted_abstract = _extract_uploaded_pdf_metadata(stored_path, fallback_title or source.stem)
    resolved_title = (str(title or "").strip() or extracted_title or source.stem).strip()[:300]
    resolved_abstract = extracted_abstract.strip()
    metadata = {
        "source": "mcp_pdf_import",
        "original_filename": source.name,
        "original_local_path": str(source),
    }

    old_pdf_path: str | None = None
    created = False
    try:
        with session_scope() as session:
            repo = PaperRepository(session)
            paper = repo.get_by_arxiv_id(normalized_id) if normalized_id else None
            if paper is None:
                created = True
                paper_key = normalized_id or f"upload-{uuid4().hex[:12]}"
                paper = repo.upsert_paper(
                    PaperCreate(
                        arxiv_id=paper_key,
                        title=resolved_title,
                        abstract=resolved_abstract,
                        metadata=metadata,
                    )
                )
            else:
                old_pdf_path = paper.pdf_path
                current_meta = dict(paper.metadata_json or {})
                current_meta.update(metadata)
                if resolved_title:
                    paper.title = resolved_title
                if resolved_abstract and not (paper.abstract or "").strip():
                    paper.abstract = resolved_abstract
                paper.metadata_json = current_meta

            repo.set_pdf_path(UUID(str(paper.id)), str(stored_path))

            linked_topic_id = str(topic_id or "").strip() or None
            if linked_topic_id:
                topic = session.get(TopicSubscription, linked_topic_id)
                if topic is None:
                    raise ValueError(f"topic {linked_topic_id} 不存在")
                if getattr(topic, "kind", "subscription") != "folder":
                    raise ValueError("只能关联到文件夹类型 topic")
                repo.link_to_topic(str(paper.id), linked_topic_id)

            result = {
                "status": "created" if created else "updated",
                "paper": _paper_summary(paper),
                "pdf_path": str(stored_path),
                "topic_id": linked_topic_id,
            }
    except Exception:
        stored_path.unlink(missing_ok=True)
        raise

    if old_pdf_path and old_pdf_path != str(stored_path):
        Path(old_pdf_path).unlink(missing_ok=True)
    return result


@server.tool(
    name="web_search",
    description="搜索网页资料，适合官网、项目文档、新闻和通用概念说明。",
)
def web_search(query: str, limit: int = 8) -> dict[str, Any]:
    return run_web_search(query, max_results=limit)


@server.tool(
    name="search_arxiv",
    description="按标题或关键词搜索 arXiv 候选论文，适合导入前确认 arXiv ID。",
)
def search_arxiv(query: str, limit: int = 8) -> dict[str, Any]:
    papers = ArxivClient().search_candidates(query, max_results=max(1, min(limit, 50)))
    items = [
        {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "abstract": paper.abstract,
            "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
            "categories": (paper.metadata or {}).get("categories", []),
            "authors": (paper.metadata or {}).get("authors", []),
        }
        for paper in papers
    ]
    return {
        "query": query,
        "count": len(items),
        "items": items,
    }


@server.tool(
    name="paper_search",
    description="搜索 ResearchOS 论文库中的论文，返回 paper_id、标题、引用数和基础状态。",
)
def paper_search(query: str, limit: int = 8) -> dict[str, Any]:
    items = _search_candidates(query, limit)
    return {
        "query": query,
        "count": len(items),
        "items": items,
    }


@server.tool(
    name="paper_detail",
    description="获取单篇论文的完整上下文，包括摘要、粗读、精读和推理链结果。",
)
def paper_detail(paper_ref: str) -> dict[str, Any]:
    paper = _resolve_paper(paper_ref)
    return _paper_detail_payload(paper)


@server.tool(
    name="paper_import_arxiv",
    description="通过 arXiv ID 导入论文到 ResearchOS，可选自动下载 PDF。",
)
def paper_import_arxiv(
    arxiv_ids: list[str],
    download_pdf: bool = True,
    topic_id: str | None = None,
) -> dict[str, Any]:
    clean_ids = [str(item or "").strip() for item in arxiv_ids if str(item or "").strip()]
    if not clean_ids:
        raise ValueError("arxiv_ids 不能为空")
    result = PaperPipelines().ingest_arxiv_ids(
        clean_ids,
        topic_id=topic_id,
        download_pdf=download_pdf,
    )
    return result


@server.tool(
    name="paper_import_pdf",
    description="导入本地 PDF 到 ResearchOS 论文库，可选指定标题、arXiv ID 和文件夹 topic_id。",
)
def paper_import_pdf(
    pdf_path: str,
    title: str | None = None,
    arxiv_id: str | None = None,
    topic_id: str | None = None,
) -> dict[str, Any]:
    return _copy_local_pdf(pdf_path, title, arxiv_id, topic_id)


@server.tool(
    name="paper_skim",
    description="为指定论文启动粗读后台任务，返回 task_id。",
)
def paper_skim(paper_ref: str) -> dict[str, Any]:
    paper = _resolve_paper(paper_ref)
    return _submit_skim_task(UUID(str(paper.id)))


@server.tool(
    name="paper_deep_read",
    description="为指定论文启动精读后台任务，返回 task_id。",
)
def paper_deep_read(paper_ref: str) -> dict[str, Any]:
    paper = _resolve_paper(paper_ref)
    return _submit_deep_task(UUID(str(paper.id)))


@server.tool(
    name="paper_reasoning",
    description="为指定论文启动推理链后台任务，返回 task_id。",
)
def paper_reasoning(paper_ref: str) -> dict[str, Any]:
    paper = _resolve_paper(paper_ref)
    return _submit_reasoning_task(UUID(str(paper.id)))


@server.tool(
    name="paper_embed",
    description="为指定论文启动向量化后台任务，返回 task_id。",
)
def paper_embed(paper_ref: str) -> dict[str, Any]:
    paper = _resolve_paper(paper_ref)
    return _submit_embed_task(UUID(str(paper.id)))


@server.tool(
    name="paper_extract_figures",
    description="为指定论文启动图表提取后台任务。默认 arxiv_source 会提取 arXiv 图片，并在 OCR 已就绪时自动补充表格；mineru 为兼容旧链路。",
)
def paper_extract_figures(
    paper_ref: str,
    extract_mode: str = "arxiv_source",
    max_figures: int = 80,
) -> dict[str, Any]:
    paper = _resolve_paper(paper_ref)
    mode = str(extract_mode or "arxiv_source").strip().lower()
    if mode in {"pdf_direct", "magic_pdf", "magic-pdf"}:
        mode = "mineru"
    if mode not in {"arxiv_source", "mineru"}:
        raise ValueError("extract_mode 仅支持 arxiv_source 或 mineru")
    return _submit_figure_task(
        UUID(str(paper.id)),
        max_figures=max(1, min(int(max_figures), 200)),
        extract_mode=mode,
    )


@server.tool(
    name="paper_figures",
    description="读取已提取的图表候选和分析结果。",
)
def paper_figures(paper_ref: str) -> dict[str, Any]:
    paper = _resolve_paper(paper_ref)
    paper_id = UUID(str(paper.id))
    items = FigureService.get_paper_analyses(UUID(str(paper.id)))
    normalized_items = []
    for item in items:
        normalized_items.append(
            {
                "id": item.get("id"),
                "figure_label": item.get("figure_label"),
                "page_number": item.get("page_number"),
                "image_type": item.get("image_type"),
                "caption": item.get("caption"),
                "description": item.get("description"),
                "has_image": bool(item.get("has_image")),
                "analyzed": bool(item.get("analyzed")),
                "image_url": (
                    f"/papers/{paper_id}/figures/{item.get('id')}/image"
                    if item.get("has_image") and item.get("id")
                    else None
                ),
            }
        )
    return {
        "paper_id": str(paper_id),
        "title": paper.title,
        "paper": _paper_summary(paper),
        "count": len(normalized_items),
        "figure_refs": _paper_figure_refs(normalized_items, limit=None),
        "items": normalized_items,
    }


@server.tool(
    name="task_list",
    description="查看 ResearchOS 最近后台任务，可选只看运行中任务。",
)
def task_list(limit: int = 20, active_only: bool = False) -> dict[str, Any]:
    if active_only:
        tasks = global_tracker.get_active()
        tasks = [task for task in tasks if not task.get("finished")]
        tasks = tasks[: max(1, min(limit, 100))]
    else:
        tasks = global_tracker.list_tasks(limit=max(1, min(limit, 100)))
    return {
        "count": len(tasks),
        "items": tasks,
    }


@server.tool(
    name="task_status",
    description="查看后台任务状态；任务完成后可附带返回 result。",
)
def task_status(task_id: str, include_result: bool = True) -> dict[str, Any]:
    status = global_tracker.get_task(task_id)
    if not status:
        raise ValueError(f"未找到任务: {task_id}")
    result = global_tracker.get_result(task_id) if include_result and status.get("finished") else None
    return {
        "status": status,
        "result": result,
    }


@server.tool(
    name="paper_library_overview",
    description="查看当前论文库的概览信息，包括数量、最近任务和高影响力论文。",
)
def paper_library_overview() -> dict[str, Any]:
    with session_scope() as session:
        repo = PaperRepository(session)
        latest, _ = repo.list_paginated(page=1, page_size=6, sort_by="created_at", sort_order="desc")
        influential, _ = repo.list_paginated(page=1, page_size=6, sort_by="impact", sort_order="desc")
        recent_items = [_paper_summary(paper) for paper in latest]
        top_items = [_paper_summary(paper) for paper in influential]

    return {
        "recent_papers": recent_items,
        "top_impact_papers": top_items,
        "tasks": global_tracker.list_tasks(limit=8),
    }


@server.tool(
    name="search_papers",
    description="按关键词在本地论文库中搜索论文，等价于 ResearchOS 原生 search_papers 工具。",
)
def search_papers(keyword: str, limit: int = 20) -> dict[str, Any]:
    result = research_runtime._search_papers(keyword, limit=max(1, min(limit, 50)))
    payload = _tool_result_payload(result)
    payload["query"] = keyword
    return payload


@server.tool(
    name="get_paper_detail",
    description="读取单篇论文的详细信息，等价于 ResearchOS 原生 get_paper_detail 工具。",
)
def get_paper_detail(paper_id: str) -> dict[str, Any]:
    resolved_id = str(_resolve_paper_id_value(paper_id))
    return _tool_result_payload(research_runtime._get_paper_detail(resolved_id))


@server.tool(
    name="get_paper_analysis",
    description="读取论文已有的三轮分析和最终结构化笔记。",
)
def get_paper_analysis(paper_id: str) -> dict[str, Any]:
    resolved_id = str(_resolve_paper_id_value(paper_id))
    return _tool_result_payload(research_runtime._get_paper_analysis(resolved_id))


@server.tool(
    name="get_similar_papers",
    description="基于向量相似度获取相似论文。",
)
def get_similar_papers(paper_id: str, top_k: int = 5) -> dict[str, Any]:
    resolved_id = str(_resolve_paper_id_value(paper_id))
    return _tool_result_payload(
        research_runtime._get_similar_papers(
            resolved_id,
            top_k=max(1, min(int(top_k), 20)),
        )
    )


@server.tool(
    name="get_citation_tree",
    description="生成论文引用树结构。",
)
def get_citation_tree(paper_id: str, depth: int = 2) -> dict[str, Any]:
    resolved_id = str(_resolve_paper_id_value(paper_id))
    return _tool_result_payload(
        research_runtime._get_citation_tree(
            resolved_id,
            depth=max(1, min(int(depth), 4)),
        )
    )


@server.tool(
    name="get_timeline",
    description="按关键词生成论文时间线与里程碑。",
)
def get_timeline(keyword: str, limit: int = 100) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._get_timeline(
            keyword,
            limit=max(5, min(int(limit), 200)),
        )
    )


@server.tool(
    name="research_kg_status",
    description="查看论文库级 Research KG / GraphRAG 构建状态。",
)
def research_kg_status() -> dict[str, Any]:
    return _tool_result_payload(research_runtime._research_kg_status())


@server.tool(
    name="build_research_kg",
    description="为本地论文库构建或刷新 GraphRAG 实体关系图。",
)
def build_research_kg(
    paper_ids: list[str] | None = None,
    limit: int = 12,
    force: bool = False,
) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._build_research_kg(
            paper_ids=paper_ids,
            limit=max(1, min(int(limit), 200)),
            force=bool(force),
        )
    )


@server.tool(
    name="graph_rag_query",
    description="基于本地论文库 Research KG 查询实体、关系、论文、引用和已有分析证据包。",
)
def graph_rag_query(
    query: str,
    top_k: int = 6,
    paper_ids: list[str] | None = None,
) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._graph_rag_query(
            query=query,
            top_k=max(1, min(int(top_k), 20)),
            paper_ids=paper_ids,
        )
    )


@server.tool(
    name="list_topics",
    description="列出当前研究工作区与订阅。",
)
def list_topics() -> dict[str, Any]:
    return _tool_result_payload(research_runtime._list_topics())


@server.tool(
    name="get_system_status",
    description="查看数据库、论文数量、任务与流水线运行概况。",
)
def get_system_status() -> dict[str, Any]:
    return _tool_result_payload(research_runtime._get_system_status())


@server.tool(
    name="search_literature",
    description="统一检索外部文献，可覆盖 OpenAlex 与 arXiv，并支持 venue 过滤。",
)
def search_literature(
    query: str,
    max_results: int = 20,
    source_scope: str = "hybrid",
    venue_tier: str = "all",
    venue_type: str = "all",
    venue_names: list[str] | None = None,
    from_year: int | None = None,
) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._search_literature(
            query=query,
            max_results=max(1, min(int(max_results), 50)),
            source_scope=source_scope,
            venue_tier=venue_tier,
            venue_type=venue_type,
            venue_names=venue_names,
            from_year=from_year,
        )
    )


@server.tool(
    name="ingest_external_literature",
    description="将外部检索结果导入本地论文库。",
)
def ingest_external_literature(
    entries: list[dict[str, Any]],
    topic_id: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._ingest_external_literature(
            entries=entries,
            topic_id=topic_id,
            query=query,
        )
    )


@server.tool(
    name="preview_external_paper_head",
    description="读取未入库 arXiv 论文的摘要元数据和章节目录。",
)
def preview_external_paper_head(arxiv_id: str) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._preview_external_paper_head(arxiv_id=arxiv_id)
    )


@server.tool(
    name="preview_external_paper_section",
    description="读取未入库 arXiv 论文的指定章节正文预览。",
)
def preview_external_paper_section(arxiv_id: str, section_name: str) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._preview_external_paper_section(
            arxiv_id=arxiv_id,
            section_name=section_name,
        )
    )


@server.tool(
    name="ingest_arxiv",
    description="将选中的 arXiv 论文导入本地库。",
)
def ingest_arxiv(query: str, arxiv_ids: list[str]) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._ingest_arxiv(query=query, arxiv_ids=arxiv_ids)
    )


@server.tool(
    name="skim_paper",
    description="对论文执行粗读分析，并直接返回可渲染结果。",
)
def skim_paper(paper_id: str) -> dict[str, Any]:
    resolved_id = _resolve_paper_id_value(paper_id)
    return _resolve_runtime_iterator_payload(
        lambda: research_runtime._skim_paper(str(resolved_id))
    )


@server.tool(
    name="deep_read_paper",
    description="对论文执行精读分析，并直接返回可渲染结果。",
)
def deep_read_paper(paper_id: str) -> dict[str, Any]:
    resolved_id = _resolve_paper_id_value(paper_id)
    return _resolve_runtime_iterator_payload(
        lambda: research_runtime._deep_read_paper(str(resolved_id))
    )


@server.tool(
    name="analyze_paper_rounds",
    description="对论文执行三轮分析，并直接返回最终结构化笔记。",
)
def analyze_paper_rounds(
    paper_id: str,
    detail_level: str = "medium",
    reasoning_level: str = "default",
) -> dict[str, Any]:
    resolved_id = _resolve_paper_id_value(paper_id)
    return _resolve_runtime_iterator_payload(
        lambda: research_runtime._analyze_paper_rounds(
            str(resolved_id),
            detail_level=detail_level,
            reasoning_level=reasoning_level,
        )
    )


@server.tool(
    name="embed_paper",
    description="为论文生成向量嵌入，并直接返回结果。",
)
def embed_paper(paper_id: str) -> dict[str, Any]:
    resolved_id = _resolve_paper_id_value(paper_id)
    return _resolve_runtime_iterator_payload(
        lambda: research_runtime._embed_paper(str(resolved_id))
    )


@server.tool(
    name="generate_wiki",
    description="生成专题综述或单篇论文综述，并直接返回结果。",
)
def generate_wiki(type: str, keyword_or_id: str) -> dict[str, Any]:
    normalized_mode = str(type or "").strip().lower()
    target = keyword_or_id
    if normalized_mode == "paper":
        resolved_id = _resolve_paper_id_value(keyword_or_id)
        target = str(resolved_id)
    return _resolve_runtime_iterator_payload(
        lambda: research_runtime._generate_wiki(normalized_mode, target)
    )


@server.tool(
    name="generate_daily_brief",
    description="生成研究简报并保存结果，直接返回结果。",
)
def generate_daily_brief(recipient: str = "") -> dict[str, Any]:
    return _resolve_runtime_iterator_payload(
        lambda: research_runtime._generate_daily_brief(recipient)
    )


@server.tool(
    name="manage_subscription",
    description="启用或关闭订阅，并调整频率与时间。",
)
def manage_subscription(
    topic_name: str,
    enabled: bool,
    schedule_frequency: str | None = None,
    schedule_time_beijing: int | None = None,
) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._manage_subscription(
            topic_name=topic_name,
            enabled=enabled,
            schedule_frequency=schedule_frequency,
            schedule_time_beijing=schedule_time_beijing,
        )
    )


@server.tool(
    name="suggest_keywords",
    description="根据研究描述和当前检索源生成更适合实际检索的关键词建议。",
)
def suggest_keywords(
    description: str,
    source_scope: str = "hybrid",
    search_field: str = "all",
) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._suggest_keywords(
            description,
            source_scope=source_scope,
            search_field=search_field,
        )
    )


@server.tool(
    name="reasoning_analysis",
    description="生成论文推理链分析，并直接返回结果。",
)
def reasoning_analysis(paper_id: str) -> dict[str, Any]:
    resolved_id = _resolve_paper_id_value(paper_id)
    return _resolve_runtime_iterator_payload(
        lambda: research_runtime._reasoning_analysis(str(resolved_id))
    )


@server.tool(
    name="identify_research_gaps",
    description="分析某个方向的研究空白与趋势。",
)
def identify_research_gaps(keyword: str, limit: int = 120) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._identify_research_gaps(
            keyword=keyword,
            limit=max(10, min(int(limit), 200)),
        )
    )


@server.tool(
    name="writing_assist",
    description="执行学术写作辅助，例如翻译、润色、压缩、扩写和图表说明。",
)
def writing_assist(action: str, text: str) -> dict[str, Any]:
    return _tool_result_payload(
        research_runtime._writing_assist(action=action, text=text)
    )


@server.tool(
    name="analyze_figures",
    description="提取并分析论文中的图片与图表，并直接返回结果。",
)
def analyze_figures(paper_id: str, max_figures: int = 10) -> dict[str, Any]:
    resolved_id = _resolve_paper_id_value(paper_id)
    return _resolve_runtime_iterator_payload(
        lambda: research_runtime._analyze_figures(
            str(resolved_id),
            max_figures=max(1, min(int(max_figures), 40)),
        )
    )


register_dynamic_bridge_tools(server, logger)


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    logger.info("starting ResearchOS MCP server")
    anyio.run(_run_agent_stdio_async)


async def _run_agent_stdio_async() -> None:
    async with _agent_stdio_server() as (read_stream, write_stream):
        await server._mcp_server.run(  # type: ignore[attr-defined]
            read_stream,
            write_stream,
            server._mcp_server.create_initialization_options(),  # type: ignore[attr-defined]
        )


if __name__ == "__main__":
    main()

