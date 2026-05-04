from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from packages.agent.tools.tool_runtime import AgentToolContext
from packages.storage.db import session_scope
from packages.storage.models import (
    Paper,
    Project,
    ProjectIdea,
    ProjectResearchWikiEdge,
    ProjectResearchWikiNode,
)
from packages.storage.repositories import ProjectRepository, ProjectResearchWikiRepository

logger = logging.getLogger(__name__)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clip_text(value: Any, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", _clean_text(value))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _tokenize_query(value: str | None) -> list[str]:
    text = _clean_text(value).lower()
    return [token for token in re.split(r"[\s,;:|/\\()\[\]{}<>+*?!.，。；：、]+", text) if token]


def _serialize_node(node: ProjectResearchWikiNode) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "project_id": str(node.project_id),
        "node_key": node.node_key,
        "node_type": node.node_type,
        "title": node.title,
        "summary": node.summary,
        "body_md": node.body_md,
        "status": node.status,
        "source_paper_id": node.source_paper_id,
        "source_run_id": node.source_run_id,
        "metadata": dict(node.metadata_json or {}),
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


def _serialize_edge(edge: ProjectResearchWikiEdge) -> dict[str, Any]:
    return {
        "id": str(edge.id),
        "project_id": str(edge.project_id),
        "source_node_id": str(edge.source_node_id),
        "target_node_id": str(edge.target_node_id),
        "edge_type": edge.edge_type,
        "metadata": dict(edge.metadata_json or {}),
        "created_at": edge.created_at.isoformat() if edge.created_at else None,
    }


def _paper_analysis_markdown(paper: Paper) -> str:
    metadata = dict(paper.metadata_json or {})
    analysis_rounds = metadata.get("analysis_rounds")
    if isinstance(analysis_rounds, dict):
        final_notes = analysis_rounds.get("final_notes")
        if isinstance(final_notes, dict):
            markdown = _clean_text(final_notes.get("markdown"))
            if markdown:
                return markdown
    deep_report = metadata.get("deep_report")
    if isinstance(deep_report, dict):
        markdown = _clean_text(deep_report.get("markdown"))
        if markdown:
            return markdown
    skim_report = metadata.get("skim_report")
    if isinstance(skim_report, dict):
        markdown = _clean_text(skim_report.get("markdown"))
        if markdown:
            return markdown
    return ""


def _paper_body_markdown(paper: Paper) -> str:
    parts: list[str] = []
    abstract = _clean_text(paper.abstract)
    if abstract:
        parts.extend(["## Abstract", abstract])
    analysis_md = _paper_analysis_markdown(paper)
    if analysis_md:
        parts.extend(["", "## Saved Analysis", analysis_md])
    return "\n".join(parts).strip()


def _paper_node_summary(paper: Paper) -> str:
    analysis_md = _paper_analysis_markdown(paper)
    if analysis_md:
        return _clip_text(analysis_md, 280)
    return _clip_text(paper.abstract, 280)


def _paper_node_metadata(paper: Paper) -> dict[str, Any]:
    metadata = dict(paper.metadata_json or {})
    return {
        "paper_id": str(paper.id),
        "arxiv_id": paper.arxiv_id,
        "publication_date": paper.publication_date.isoformat() if paper.publication_date else None,
        "authors": list(metadata.get("authors") or []),
        "venue": metadata.get("venue") or metadata.get("citation_venue") or "",
        "venue_type": metadata.get("venue_type") or "",
        "venue_tier": metadata.get("venue_tier") or "",
        "categories": list(metadata.get("categories") or []),
    }


def _idea_node_summary(idea: ProjectIdea) -> str:
    return _clip_text(idea.content, 240)


def _idea_status_from_signal(signal: str | None) -> str:
    normalized = _clean_text(signal).upper()
    if normalized == "NEGATIVE":
        return "failed"
    if normalized in {"WEAK_POSITIVE", "SKIPPED"}:
        return "proposed"
    return "active"


def _score_node(node: ProjectResearchWikiNode | dict[str, Any], query_tokens: list[str]) -> int:
    if not query_tokens:
        return 0
    if isinstance(node, dict):
        title = node.get("title")
        summary = node.get("summary")
        body_md = node.get("body_md")
        metadata = dict(node.get("metadata") or {})
    else:
        title = node.title
        summary = node.summary
        body_md = node.body_md
        metadata = dict(node.metadata_json or {})
    haystacks = [
        _clean_text(title).lower(),
        _clean_text(summary).lower(),
        _clean_text(body_md).lower(),
        _clean_text(metadata.get("ranking_reason")).lower(),
    ]
    score = 0
    for token in query_tokens:
        for haystack in haystacks:
            if token and token in haystack:
                score += 1
    return score


class ResearchWikiService:
    """Project-scoped research wiki orchestration."""

    def resolve_project_id(
        self,
        *,
        project_id: str | None = None,
        context: AgentToolContext | None = None,
    ) -> str:
        normalized_project_id = _clean_text(project_id)
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            if normalized_project_id:
                project = project_repo.get_project(normalized_project_id)
                if project is not None:
                    return str(project.id)
            workspace_path = _clean_text(getattr(context, "workspace_path", None))
            workspace_server_id = _clean_text(getattr(context, "workspace_server_id", None)) or None
            if workspace_path:
                project = project_repo.find_project_by_workspace_path(
                    workspace_path,
                    workspace_server_id=workspace_server_id,
                )
                if project is not None:
                    return str(project.id)
        raise ValueError("无法确定当前项目，请提供 project_id 或在项目工作区内调用")

    def initialize_project_wiki(self, project_id: str) -> dict[str, Any]:
        sync_payload = self.sync_project_materials(project_id)
        stats_payload = self.stats(project_id)
        return {
            "project_id": project_id,
            "synced": sync_payload,
            "stats": stats_payload,
        }

    def _ensure_project(self, project_repo: ProjectRepository, project_id: str) -> Project:
        project = project_repo.get_project(project_id)
        if project is None:
            raise ValueError(f"项目不存在：{project_id}")
        return project

    def _ensure_paper_nodes(
        self,
        *,
        project_id: str,
        project_repo: ProjectRepository,
        wiki_repo: ProjectResearchWikiRepository,
    ) -> dict[str, ProjectResearchWikiNode]:
        paper_nodes: dict[str, ProjectResearchWikiNode] = {}
        for _binding, paper in project_repo.list_project_papers(project_id):
            node = wiki_repo.upsert_node(
                project_id=project_id,
                node_key=f"paper:{paper.id}",
                node_type="paper",
                title=paper.title,
                summary=_paper_node_summary(paper),
                body_md=_paper_body_markdown(paper),
                status="active",
                source_paper_id=str(paper.id),
                metadata=_paper_node_metadata(paper),
            )
            paper_nodes[str(paper.id)] = node
        return paper_nodes

    def _upsert_project_idea_node(
        self,
        *,
        project_id: str,
        wiki_repo: ProjectResearchWikiRepository,
        paper_nodes: dict[str, ProjectResearchWikiNode],
        idea_id: str,
        title: str,
        content: str,
        paper_ids: list[str] | None = None,
        source_run_id: str | None = None,
        pilot_signal: str | None = None,
        ranking_reason: str | None = None,
        origin_skill: str | None = None,
    ) -> ProjectResearchWikiNode:
        existing = wiki_repo.get_node_by_key(project_id, f"idea:{idea_id}")
        existing_metadata = dict(existing.metadata_json or {}) if existing is not None else {}
        normalized_paper_ids = [
            _clean_text(item) for item in (paper_ids or []) if _clean_text(item)
        ]
        if not normalized_paper_ids:
            normalized_paper_ids = [
                _clean_text(item)
                for item in (existing_metadata.get("paper_ids") or [])
                if _clean_text(item)
            ]
        resolved_source_run_id = _clean_text(source_run_id) or _clean_text(
            existing.source_run_id if existing is not None else None
        )
        resolved_pilot_signal = _clean_text(pilot_signal) or _clean_text(
            existing_metadata.get("pilot_signal")
        )
        resolved_ranking_reason = _clean_text(ranking_reason) or _clean_text(
            existing_metadata.get("ranking_reason")
        )
        resolved_origin_skill = (
            _clean_text(origin_skill)
            or _clean_text(existing_metadata.get("origin_skill"))
            or "project_ideas"
        )
        node = wiki_repo.upsert_node(
            project_id=project_id,
            node_key=f"idea:{idea_id}",
            node_type="idea",
            title=_clean_text(title)[:512] or f"项目想法 {idea_id}",
            summary=_clip_text(content, 240),
            body_md=_clean_text(content),
            status=_idea_status_from_signal(resolved_pilot_signal),
            source_run_id=resolved_source_run_id or None,
            metadata={
                "idea_id": idea_id,
                "paper_ids": normalized_paper_ids,
                "pilot_signal": resolved_pilot_signal or None,
                "ranking_reason": resolved_ranking_reason,
                "origin_skill": resolved_origin_skill,
            },
        )
        wiki_repo.delete_edges_for_source(
            project_id=project_id,
            source_node_id=str(node.id),
            edge_type="inspired_by",
        )
        for paper_id in normalized_paper_ids:
            target = paper_nodes.get(paper_id)
            if target is None:
                continue
            wiki_repo.upsert_edge(
                project_id=project_id,
                source_node_id=str(node.id),
                target_node_id=str(target.id),
                edge_type="inspired_by",
                metadata={"paper_id": paper_id},
            )
        return node

    def sync_project_materials(self, project_id: str) -> dict[str, Any]:
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            wiki_repo = ProjectResearchWikiRepository(session)
            self._ensure_project(project_repo, project_id)
            paper_nodes = self._ensure_paper_nodes(
                project_id=project_id,
                project_repo=project_repo,
                wiki_repo=wiki_repo,
            )
            idea_nodes: list[ProjectResearchWikiNode] = []
            for idea in project_repo.list_ideas(project_id):
                idea_nodes.append(
                    self._upsert_project_idea_node(
                        project_id=project_id,
                        wiki_repo=wiki_repo,
                        paper_nodes=paper_nodes,
                        idea_id=str(idea.id),
                        title=idea.title,
                        content=idea.content,
                        paper_ids=list(idea.paper_ids_json or []),
                    )
                )
            edge_count = len(wiki_repo.list_edges(project_id))
            return {
                "project_id": project_id,
                "paper_nodes": len(paper_nodes),
                "idea_nodes": len(idea_nodes),
                "edge_count": edge_count,
            }

    def upsert_idea_nodes(
        self,
        *,
        project_id: str,
        ideas: list[dict[str, Any]],
        source_run_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_ideas = [
            dict(item) for item in (ideas or []) if _clean_text((item or {}).get("title"))
        ]
        if not normalized_ideas:
            return {"project_id": project_id, "idea_nodes": [], "edge_count": 0}

        with session_scope() as session:
            project_repo = ProjectRepository(session)
            wiki_repo = ProjectResearchWikiRepository(session)
            self._ensure_project(project_repo, project_id)
            paper_nodes = self._ensure_paper_nodes(
                project_id=project_id,
                project_repo=project_repo,
                wiki_repo=wiki_repo,
            )
            nodes: list[dict[str, Any]] = []
            for item in normalized_ideas:
                idea_id = (
                    _clean_text(item.get("id"))
                    or re.sub(r"[^a-z0-9]+", "-", _clean_text(item.get("title")).lower()).strip("-")
                    or "idea"
                )
                node = self._upsert_project_idea_node(
                    project_id=project_id,
                    wiki_repo=wiki_repo,
                    paper_nodes=paper_nodes,
                    idea_id=idea_id,
                    title=_clean_text(item.get("title")),
                    content=_clean_text(item.get("content")),
                    paper_ids=list(item.get("paper_ids") or []),
                    source_run_id=source_run_id,
                    pilot_signal=_clean_text(item.get("pilot_signal")) or None,
                    ranking_reason=_clean_text(item.get("ranking_reason")) or None,
                    origin_skill=_clean_text(item.get("origin_skill")) or "idea-discovery",
                )
                nodes.append(_serialize_node(node))
            edge_count = len(wiki_repo.list_edges(project_id))
            return {
                "project_id": project_id,
                "idea_nodes": nodes,
                "edge_count": edge_count,
            }

    def update_node(
        self,
        *,
        project_id: str,
        node_id: str | None = None,
        node_key: str | None = None,
        node_type: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        body_md: str | None = None,
        status: str | None = None,
        source_paper_id: str | None = None,
        source_run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            wiki_repo = ProjectResearchWikiRepository(session)
            self._ensure_project(project_repo, project_id)
            node = wiki_repo.get_node(_clean_text(node_id)) if _clean_text(node_id) else None
            if node is not None and str(node.project_id) != project_id:
                raise ValueError("wiki 节点不属于当前项目")
            if node is None and _clean_text(node_key):
                node = wiki_repo.get_node_by_key(project_id, _clean_text(node_key))
            if node is None:
                if not (_clean_text(node_key) and _clean_text(node_type) and _clean_text(title)):
                    raise ValueError("创建 wiki 节点至少需要 node_key、node_type、title")
                node = wiki_repo.upsert_node(
                    project_id=project_id,
                    node_key=_clean_text(node_key),
                    node_type=_clean_text(node_type),
                    title=_clean_text(title),
                    summary=_clean_text(summary),
                    body_md=_clean_text(body_md),
                    status=_clean_text(status) or "active",
                    source_paper_id=_clean_text(source_paper_id) or None,
                    source_run_id=_clean_text(source_run_id) or None,
                    metadata=dict(metadata or {}),
                )
            else:
                merged_metadata = dict(node.metadata_json or {})
                if isinstance(metadata, dict):
                    merged_metadata.update(metadata)
                wiki_repo.update_node(
                    str(node.id),
                    node_key=_clean_text(node_key) or node.node_key,
                    node_type=_clean_text(node_type) or node.node_type,
                    title=_clean_text(title) or node.title,
                    summary=_clean_text(summary) if summary is not None else node.summary,
                    body_md=_clean_text(body_md) if body_md is not None else node.body_md,
                    status=_clean_text(status) or node.status,
                    source_paper_id=_clean_text(source_paper_id) or node.source_paper_id,
                    source_run_id=_clean_text(source_run_id) or node.source_run_id,
                    metadata=merged_metadata,
                )
                node = wiki_repo.get_node(str(node.id))
            assert node is not None
            return _serialize_node(node)

    def _ensure_seeded(self, project_id: str) -> None:
        with session_scope() as session:
            wiki_repo = ProjectResearchWikiRepository(session)
            if wiki_repo.list_nodes(project_id, limit=1):
                return
        self.sync_project_materials(project_id)

    def stats(self, project_id: str) -> dict[str, Any]:
        self._ensure_seeded(project_id)
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            wiki_repo = ProjectResearchWikiRepository(session)
            project = self._ensure_project(project_repo, project_id)
            nodes = wiki_repo.list_nodes(project_id)
            edges = wiki_repo.list_edges(project_id)
            node_payloads = [_serialize_node(node) for node in nodes]
            edge_payloads = [_serialize_edge(edge) for edge in edges]
            node_type_counts = Counter(str(node.get("node_type") or "") for node in node_payloads)
            status_counts = Counter(str(node.get("status") or "") for node in node_payloads)
            edge_type_counts = Counter(str(edge.get("edge_type") or "") for edge in edge_payloads)
            return {
                "project_id": project_id,
                "project_name": project.name,
                "node_count": len(node_payloads),
                "edge_count": len(edge_payloads),
                "node_type_counts": dict(node_type_counts),
                "status_counts": dict(status_counts),
                "edge_type_counts": dict(edge_type_counts),
                "recent_nodes": node_payloads[:8],
                "recent_edges": edge_payloads[:8],
            }

    def build_query_pack(
        self,
        *,
        project_id: str,
        query: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        self._ensure_seeded(project_id)
        with session_scope() as session:
            project_repo = ProjectRepository(session)
            wiki_repo = ProjectResearchWikiRepository(session)
            project = self._ensure_project(project_repo, project_id)
            nodes = wiki_repo.list_nodes(project_id)
            edges = wiki_repo.list_edges(project_id)
            node_payloads = [_serialize_node(node) for node in nodes]
            edge_payloads = [_serialize_edge(edge) for edge in edges]
            project_name = project.name
            project_description = project.description or "未填写项目描述。"

        query_tokens = _tokenize_query(query)
        sorted_nodes = sorted(
            node_payloads,
            key=lambda node: (
                _score_node(node, query_tokens),
                str(node.get("updated_at") or ""),
            ),
            reverse=True,
        )
        normalized_limit = max(1, min(int(limit or 5), 12))
        if query_tokens:
            matched_nodes = [node for node in sorted_nodes if _score_node(node, query_tokens) > 0]
            if matched_nodes:
                sorted_nodes = matched_nodes

        top_papers = [node for node in sorted_nodes if str(node.get("node_type") or "") == "paper"][
            :normalized_limit
        ]
        active_ideas = [
            node
            for node in sorted_nodes
            if str(node.get("node_type") or "") == "idea"
            and str(node.get("status") or "") in {"active", "proposed"}
        ][:normalized_limit]
        failed_ideas = [
            node
            for node in sorted_nodes
            if str(node.get("node_type") or "") == "idea"
            and str(node.get("status") or "") == "failed"
        ][: max(1, min(3, normalized_limit))]
        node_type_counts = Counter(str(node.get("node_type") or "") for node in node_payloads)
        edge_type_counts = Counter(str(edge.get("edge_type") or "") for edge in edge_payloads)

        lines = [
            f"# {project_name} Research Wiki Snapshot",
            "",
            "## Project Direction",
            f"- Project: {project_name}",
            f"- Description: {_clip_text(project_description, 220)}",
        ]
        if query_tokens:
            lines.append(f"- Focus Query: {_clean_text(query)}")

        lines.extend(["", "## Top Papers"])
        if top_papers:
            for node in top_papers:
                meta = dict(node.get("metadata") or {})
                venue = _clean_text(meta.get("venue"))
                suffix = f" | {venue}" if venue else ""
                lines.append(
                    f"- {node.get('title')}{suffix}: {_clip_text(node.get('summary'), 180)}"
                )
        else:
            lines.append("- 暂无论文节点。")

        lines.extend(["", "## Active Ideas"])
        if active_ideas:
            for node in active_ideas:
                meta = dict(node.get("metadata") or {})
                signal = _clean_text(meta.get("pilot_signal"))
                signal_suffix = f" | pilot={signal}" if signal else ""
                lines.append(
                    f"- {node.get('title')}{signal_suffix}: {_clip_text(node.get('summary'), 180)}"
                )
        else:
            lines.append("- 暂无活跃想法。")

        lines.extend(["", "## Failed Ideas"])
        if failed_ideas:
            for node in failed_ideas:
                lines.append(f"- {node.get('title')}: {_clip_text(node.get('summary'), 160)}")
        else:
            lines.append("- 暂无失败想法记录。")

        lines.extend(
            [
                "",
                "## Network Snapshot",
                f"- Total Nodes: {len(node_payloads)}",
                f"- Total Edges: {len(edge_payloads)}",
                f"- Node Types: {dict(node_type_counts)}",
                f"- Edge Types: {dict(edge_type_counts)}",
            ]
        )

        return {
            "project_id": project_id,
            "project_name": project_name,
            "query": _clean_text(query) or None,
            "query_pack": "\n".join(lines).strip(),
            "top_papers": top_papers,
            "active_ideas": active_ideas,
            "failed_ideas": failed_ideas,
            "matched_nodes": sorted_nodes[:normalized_limit],
            "node_count": len(node_payloads),
            "edge_count": len(edge_payloads),
        }
