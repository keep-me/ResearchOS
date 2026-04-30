from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.ai.paper import pipelines as pipelines_module
from packages.ai.research import keyword_service
from packages.agent import research_tool_runtime
from packages.agent import researchos_mcp
from packages.agent.tools.tool_runtime import AgentToolContext
from packages.agent.tools.tool_runtime import ToolResult
from packages.storage import db
from packages.storage.db import Base, session_scope
from packages.storage.models import ImageAnalysis
from packages.storage.repositories import PaperRepository, ProjectRepository, ProjectResearchWikiRepository
from packages.domain.schemas import PaperCreate


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _seed_local_paper() -> tuple[str, str]:
    with session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.upsert_paper(
            PaperCreate(
                arxiv_id="2401.12345",
                title="Research Agent Memory",
                abstract="A paper about research assistants.",
                publication_date=date(2024, 1, 2),
                metadata={
                    "title_zh": "研究助手记忆",
                    "abstract_zh": "一篇关于研究助手的论文。",
                    "venue": "Conference on Neural Information Processing Systems",
                    "venue_type": "conference",
                    "venue_tier": "ccf_a",
                    "analysis_rounds": {
                        "detail_level": "medium",
                        "reasoning_level": "medium",
                        "final_notes": {
                            "title": "最终结构化笔记",
                            "markdown": "## 总结\n\n这是一篇测试用分析。",
                        },
                    },
                },
            )
        )
        return str(paper.id), paper.title


def _attach_figure(paper_id: str, image_path: str) -> None:
    with session_scope() as session:
        session.add(
            ImageAnalysis(
                id="fig-1",
                paper_id=paper_id,
                page_number=3,
                image_index=0,
                image_type="figure",
                caption="Figure 1: Memory pipeline",
                description="展示了研究助手的记忆模块与检索路径。",
                image_path=image_path,
            )
        )


def _seed_project_workspace(project_root: str, paper_id: str) -> str:
    with session_scope() as session:
        repo = ProjectRepository(session)
        project = repo.create_project(
            name="Research Wiki Test",
            description="Project for research wiki tool tests.",
            workdir=project_root,
        )
        repo.add_paper_to_project(project_id=project.id, paper_id=paper_id)
        repo.create_idea(
            project_id=project.id,
            title="记忆路由想法",
            content="## 假设\n为研究助手增加显式记忆路由。",
            paper_ids=[paper_id],
        )
        return str(project.id)


def test_get_paper_detail_and_analysis_expose_saved_research_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id, title = _seed_local_paper()
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(b"fake-image")
    _attach_figure(paper_id, str(image_path))

    detail = research_tool_runtime._get_paper_detail(paper_id)
    analysis = research_tool_runtime._get_paper_analysis(paper_id)

    assert detail.success is True
    assert detail.data["title"] == title
    assert detail.data["venue_tier"] == "ccf_a"
    assert detail.data["has_analysis_rounds"] is True
    assert detail.data["figure_count"] == 1
    assert "figures" not in detail.data
    assert detail.data["figure_refs"][0]["caption"] == "Figure 1: Memory pipeline"
    assert detail.internal_data["display_data"]["figures"][0]["caption"] == "Figure 1: Memory pipeline"
    assert detail.internal_data["display_data"]["figures"][0]["image_url"].endswith(f"/papers/{paper_id}/figures/fig-1/image")

    assert analysis.success is True
    assert analysis.data["paper_id"] == paper_id
    assert analysis.data["analysis_rounds"]["detail_level"] == "medium"
    assert analysis.data["figure_count"] == 1
    assert "figures" not in analysis.data
    assert analysis.data["figure_refs"][0]["id"] == "fig-1"
    assert analysis.internal_data["display_data"]["figures"][0]["id"] == "fig-1"


def test_search_papers_returns_compact_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    paper_id, title = _seed_local_paper()

    result = research_tool_runtime._search_papers("Research Agent", limit=5)

    assert result.success is True
    assert result.data["count"] == 1
    item = result.data["papers"][0]
    assert item["id"] == paper_id
    assert item["title"] == title
    assert item["abstract_preview"] == "A paper about research assistants."
    assert item["abstract_zh_preview"] == "一篇关于研究助手的论文。"
    assert item["has_analysis_rounds"] is True
    assert "abstract" not in item
    assert "abstract_zh" not in item
    assert "analysis_rounds" not in item
    assert "skim_report" not in item
    assert "deep_report" not in item
    assert "mineru_ocr" not in item
    assert "embedding_status" not in item


def test_search_papers_expands_chinese_research_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    with session_scope() as session:
        paper = PaperRepository(session).upsert_paper(
            PaperCreate(
                arxiv_id="2603.14941",
                title="RS-WorldModel: a Unified Model for Remote Sensing Understanding",
                abstract="A remote sensing world model for geospatial image understanding.",
                publication_date=date(2026, 3, 20),
                metadata={
                    "title_zh": "遥感世界模型",
                    "abstract_zh": "面向遥感影像理解的大模型方法。",
                },
            )
        )
        paper_id = str(paper.id)

    result = research_tool_runtime._search_papers("帮我找找遥感大模型相关论文", limit=5)

    assert result.success is True
    assert result.data["count"] >= 1
    assert result.data["papers"][0]["id"] == paper_id
    assert "remote sensing" in result.data["expanded_queries"]


def test_graph_rag_tools_build_status_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    paper_id, _ = _seed_local_paper()

    def fake_extract(self, ctx):  # noqa: ANN001
        return {
            "nodes": [
                {
                    "id": "n1",
                    "type": "method",
                    "name": "Research Agent Memory",
                    "summary": "显式记忆路由方法。",
                },
                {
                    "id": "n2",
                    "type": "task",
                    "name": "research assistant context reuse",
                    "summary": "复用研究助手上下文。",
                },
                {
                    "id": "n3",
                    "type": "limitation",
                    "name": "memory routing evaluation gap",
                    "summary": "缺少记忆命中率评测。",
                },
            ],
            "edges": [
                {
                    "source": "n1",
                    "target": "n2",
                    "type": "addresses",
                    "evidence": "A paper about research assistants.",
                    "weight": 1.0,
                },
                {
                    "source": "n1",
                    "target": "n3",
                    "type": "has_limitation",
                    "evidence": "需要把记忆命中率和回答质量一起评估。",
                    "weight": 0.8,
                },
            ],
        }

    monkeypatch.setattr(research_tool_runtime.GraphRAGService, "extract_paper_kg", fake_extract)

    build = research_tool_runtime._build_research_kg(paper_ids=[paper_id], limit=5, force=True)
    status = research_tool_runtime._research_kg_status()
    query = research_tool_runtime._graph_rag_query("memory route evaluation gap", top_k=5)

    assert build.success is True
    assert build.data["built"] == 1
    assert build.data["items"][0]["node_count"] == 3
    assert build.data["items"][0]["edge_count"] == 2

    assert status.success is True
    assert status.data["node_count"] == 3
    assert status.data["edge_count"] == 2
    assert status.data["complete_paper_count"] == 1

    assert query.success is True
    assert query.data["used_graph"] is True
    assert query.data["coverage"]["node_count"] >= 1
    assert query.data["coverage"]["edge_count"] >= 1
    assert "GraphRAG Evidence Pack" in query.data["evidence_pack"]
    assert any(edge["type"] == "has_limitation" for edge in query.data["edges"])


def test_research_wiki_tools_seed_query_and_update_by_workspace_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id, _ = _seed_local_paper()
    project_id = _seed_project_workspace(str(tmp_path), paper_id)
    context = AgentToolContext(mode="build", workspace_path=str(tmp_path))

    init_result = research_tool_runtime._research_wiki_init(context=context)
    stats_result = research_tool_runtime._research_wiki_stats(context=context)
    query_result = research_tool_runtime._research_wiki_query(query="memory route", context=context)
    update_result = research_tool_runtime._research_wiki_update_node(
        node_key="gap:memory-routing-eval",
        node_type="gap",
        title="记忆路由评测缺口",
        summary="当前缺少专门的评测切片。",
        body_md="## Gap\n需要把记忆命中率和回答质量一起评估。",
        metadata={"priority": "high"},
        context=context,
    )

    assert init_result.success is True
    assert init_result.data["project_id"] == project_id

    assert stats_result.success is True
    assert stats_result.data["node_type_counts"]["paper"] == 1
    assert stats_result.data["node_type_counts"]["idea"] == 1

    assert query_result.success is True
    assert "Research Wiki Snapshot" in query_result.data["query_pack"]
    assert query_result.data["project_id"] == project_id

    assert update_result.success is True
    assert update_result.data["node_key"] == "gap:memory-routing-eval"
    assert update_result.data["node_type"] == "gap"
    assert update_result.data["metadata"]["priority"] == "high"

    with session_scope() as session:
        wiki_repo = ProjectResearchWikiRepository(session)
        nodes = wiki_repo.list_nodes(project_id)
        assert any(node.node_key == "paper:" + paper_id for node in nodes)
        assert any(node.node_key == "gap:memory-routing-eval" for node in nodes)


def test_ingest_external_literature_tool_imports_openalex_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_test_db(monkeypatch)
    monkeypatch.setattr(pipelines_module, "_bg_auto_link", lambda paper_ids: None)
    monkeypatch.setattr(pipelines_module, "LLMClient", lambda: object())
    monkeypatch.setattr(pipelines_module, "VisionPdfReader", lambda: object())
    monkeypatch.setattr(pipelines_module, "PdfTextExtractor", lambda: object())

    result = research_tool_runtime._ingest_external_literature(
        [
            {
                "title": "Hybrid Retrieval for Research Agents",
                "abstract": "External candidate.",
                "publication_year": 2025,
                "publication_date": "2025-02-10",
                "citation_count": 42,
                "venue": "Conference on Neural Information Processing Systems",
                "venue_type": "conference",
                "venue_tier": "ccf_a",
                "authors": ["Alice", "Bob"],
                "openalex_id": "https://openalex.org/W123",
                "source_url": "https://openalex.org/W123",
                "source": "openalex",
            }
        ],
        query="research agents",
    )

    assert result.success is True
    assert result.data["requested"] == 1
    assert result.data["ingested"] == 1


def test_preview_external_paper_tools_return_head_and_section(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        research_tool_runtime.ExternalPaperPreviewService,
        "fetch_head",
        lambda self, arxiv_id: {  # noqa: ARG005
            "arxiv_id": "1706.03762",
            "title": "Attention Is All You Need",
            "abstract": "Transformer paper.",
            "section_count": 4,
            "sections": [
                {"title": "1 Introduction", "anchor": "S1", "level": 2},
                {"title": "3 Model Architecture", "anchor": "S3", "level": 2},
            ],
            "ar5iv_available": True,
        },
    )
    monkeypatch.setattr(
        research_tool_runtime.ExternalPaperPreviewService,
        "fetch_section",
        lambda self, arxiv_id, section_name: {  # noqa: ARG005
            "arxiv_id": "1706.03762",
            "requested_section": "Introduction",
            "matched_section": "1 Introduction",
            "markdown": "Transformer replaces recurrence with attention.",
            "child_sections": ["Motivation"],
        },
    )

    head = research_tool_runtime._preview_external_paper_head("1706.03762")
    section = research_tool_runtime._preview_external_paper_section("1706.03762", "Introduction")

    assert head.success is True
    assert head.data["title"] == "Attention Is All You Need"
    assert head.data["section_count"] == 4
    assert "章节标题" in head.summary

    assert section.success is True
    assert section.data["matched_section"] == "1 Introduction"
    assert "已预读章节" in section.summary


def test_keyword_service_filters_zero_hit_suggestions(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        parsed_json = {
            "suggestions": [
                {"name": "方向 A", "query": "good query", "reason": "核心方向"},
                {"name": "方向 B", "query": "empty query", "reason": "噪声方向"},
            ]
        }

    monkeypatch.setattr(keyword_service.LLMClient, "complete_json", lambda self, *args, **kwargs: _FakeResult())
    monkeypatch.setattr(keyword_service.LLMClient, "trace_result", lambda self, *args, **kwargs: None)

    def _fake_search(query: str, **_kwargs) -> ToolResult:
        hit_count = 3 if query == "good query" else 0
        return ToolResult(
            success=True,
            data={"papers": [{"title": "match"}] * hit_count, "count": hit_count},
            summary="ok",
        )

    monkeypatch.setattr(research_tool_runtime, "_search_literature", _fake_search)

    suggestions = keyword_service.KeywordService().suggest(
        "多智能体科研流程自动化",
        source_scope="hybrid",
        search_field="all",
    )

    assert len(suggestions) == 1
    assert suggestions[0]["query"] == "good query"
    assert "验证命中 3 篇" in suggestions[0]["reason"]


def test_analyze_paper_rounds_streams_completed_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id, _ = _seed_local_paper()
    image_path = tmp_path / "figure-round.png"
    image_path.write_bytes(b"fake-image")
    _attach_figure(paper_id, str(image_path))

    def fake_analyze(
        self,  # noqa: ANN001
        paper_id_value: UUID,
        *,
        detail_level: str = "medium",
        reasoning_level: str = "default",
        progress_callback=None,
    ) -> dict:
        if progress_callback:
            progress_callback("round-1", 25, 100)
            progress_callback("round-2", 75, 100)
        return {
            "paper_id": str(paper_id_value),
            "analysis_rounds": {
                "detail_level": detail_level,
                "reasoning_level": reasoning_level,
                "final_notes": {
                    "title": "最终结构化笔记",
                    "markdown": "## 最终\n\n分析完成。",
                },
            },
        }

    monkeypatch.setattr(research_tool_runtime.PaperAnalysisService, "analyze", fake_analyze)

    stream = list(research_tool_runtime._analyze_paper_rounds(paper_id, detail_level="high", reasoning_level="medium"))

    assert any(getattr(item, "message", "") == "round-1" for item in stream)
    final_result = next(item for item in stream if getattr(item, "summary", "").startswith("论文三轮分析完成"))
    assert final_result.data["analysis_rounds"]["detail_level"] == "high"
    assert final_result.data["figure_count"] == 1
    assert "figures" not in final_result.data
    assert final_result.data["figure_refs"][0]["id"] == "fig-1"
    assert final_result.internal_data["display_data"]["figures"][0]["id"] == "fig-1"


def test_analyze_paper_rounds_returns_failure_when_bundle_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id, _ = _seed_local_paper()

    def fake_analyze(
        self,  # noqa: ANN001
        paper_id_value: UUID,
        *,
        detail_level: str = "medium",
        reasoning_level: str = "default",
        progress_callback=None,
    ) -> dict:
        del detail_level, reasoning_level, progress_callback
        return {
            "paper_id": str(paper_id_value),
            "analysis_rounds": {
                "final_notes": {
                    "title": "最终结构化笔记",
                    "markdown": "模型服务暂不可用。(stage=paper_round_final_notes)",
                },
            },
        }

    monkeypatch.setattr(research_tool_runtime.PaperAnalysisService, "analyze", fake_analyze)

    stream = list(research_tool_runtime._analyze_paper_rounds(paper_id))
    final_result = next(item for item in stream if hasattr(item, "success"))
    assert final_result.success is False
    assert "模型未返回有效内容" in final_result.summary


def test_analyze_figures_returns_normalized_items_with_image_refs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id, _ = _seed_local_paper()
    image_path = tmp_path / "figure-analysis.png"
    image_path.write_bytes(b"fake-image")
    _attach_figure(paper_id, str(image_path))

    with session_scope() as session:
        paper = PaperRepository(session).get_by_id(UUID(paper_id))
        paper.pdf_path = str(tmp_path / "paper.pdf")

    monkeypatch.setattr(
        research_tool_runtime.FigureService,
        "extract_paper_figure_candidates",
        lambda self, **_kwargs: [object()],
    )
    monkeypatch.setattr(
        research_tool_runtime.FigureService,
        "analyze_paper_figures",
        lambda self, **_kwargs: [object()],
    )

    stream = list(research_tool_runtime._analyze_figures(paper_id))
    final_result = next(item for item in stream if hasattr(item, "success"))

    assert final_result.success is True
    assert final_result.data["count"] == 1
    assert final_result.data["analyzed_count"] == 1
    assert final_result.data["items"][0]["id"] == "fig-1"
    assert final_result.data["items"][0]["image_url"].endswith(f"/papers/{paper_id}/figures/fig-1/image")
    assert final_result.data["figure_refs"][0]["id"] == "fig-1"


def test_researchos_mcp_paper_tools_can_use_detached_paper_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _configure_test_db(monkeypatch)
    paper_id, title = _seed_local_paper()
    image_path = tmp_path / "figure-mcp.png"
    image_path.write_bytes(b"fake-image")
    _attach_figure(paper_id, str(image_path))

    detail = researchos_mcp.paper_detail(paper_id)
    figures = researchos_mcp.paper_figures(paper_id)
    runtime_detail = researchos_mcp.get_paper_detail(paper_id)
    analysis = researchos_mcp.get_paper_analysis(paper_id)

    assert detail["id"] == paper_id
    assert detail["title"] == title
    assert detail["analysis_rounds"]["final_notes"]["title"] == "最终结构化笔记"
    assert detail["figure_count"] == 1
    assert detail["figure_refs"][0]["figure_label"] == "Figure 1"
    assert figures["paper"]["id"] == paper_id
    assert figures["paper_id"] == paper_id
    assert figures["count"] == 1
    assert figures["figure_refs"][0]["figure_label"] == "Figure 1"
    assert figures["items"][0]["id"] == "fig-1"
    assert figures["items"][0]["figure_label"] == "Figure 1"
    assert figures["items"][0]["image_url"].endswith(f"/papers/{paper_id}/figures/fig-1/image")
    assert runtime_detail["success"] is True
    assert runtime_detail["data"]["id"] == paper_id
    assert runtime_detail["data"]["figure_refs"][0]["figure_label"] == "Figure 1"
    assert analysis["success"] is True
    assert analysis["data"]["paper_id"] == paper_id
    assert analysis["data"]["figure_refs"][0]["figure_label"] == "Figure 1"
