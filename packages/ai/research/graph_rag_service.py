"""论文库级 GraphRAG 服务。"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from packages.ai.paper.mineru_runtime import MinerUOcrRuntime
from packages.integrations.llm_client import LLMClient
from packages.storage.db import session_scope
from packages.storage.models import Paper, ResearchKGEdge, ResearchKGNode
from packages.storage.repositories import (
    AnalysisRepository,
    CitationRepository,
    PaperRepository,
    ResearchKGRepository,
)

logger = logging.getLogger(__name__)

KG_NODE_TYPES = {
    "task",
    "method",
    "dataset",
    "metric",
    "finding",
    "limitation",
    "concept",
}
KG_EDGE_TYPES = {
    "addresses",
    "uses_method",
    "uses_dataset",
    "evaluates_with",
    "achieves",
    "has_limitation",
    "improves",
    "compares_with",
    "supports",
    "related_to",
}


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _clip(value: Any, limit: int) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _tokenize(value: str | None) -> list[str]:
    text = _clean_text(value).casefold()
    return [
        token
        for token in re.split(r"[\s,;:|/\\()\[\]{}<>+*?!.，。；：、\"'`]+", text)
        if len(token) >= 2
    ]


def _json_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _json_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class PaperKGContext:
    paper_id: str
    title: str
    abstract: str
    arxiv_id: str
    content: str
    content_hash: str


class GraphRAGService:
    """Builds and queries a paper-library knowledge graph for Agent evidence."""

    def __init__(self) -> None:
        self.llm = LLMClient()

    def status(self, paper_ids: list[str] | None = None) -> dict[str, Any]:
        with session_scope() as session:
            kg_repo = ResearchKGRepository(session)
            states = kg_repo.list_paper_states(paper_ids=paper_ids)
            stats = kg_repo.stats()
            return {
                **stats,
                "states": [
                    {
                        "paper_id": state.paper_id,
                        "status": state.status,
                        "node_count": state.node_count,
                        "edge_count": state.edge_count,
                        "error": state.error,
                        "built_at": state.built_at.isoformat() if state.built_at else None,
                        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
                    }
                    for state in states[:20]
                ],
            }

    def build_papers(
        self,
        *,
        paper_ids: list[str] | None = None,
        limit: int = 12,
        force: bool = False,
    ) -> dict[str, Any]:
        contexts = self._load_candidate_contexts(paper_ids=paper_ids, limit=limit)
        results: list[dict[str, Any]] = []
        built = 0
        skipped = 0
        failed = 0
        for ctx in contexts:
            if not force and self._is_context_current(ctx):
                skipped += 1
                results.append(
                    {
                        "paper_id": ctx.paper_id,
                        "title": ctx.title,
                        "status": "skipped",
                        "reason": "content_hash_unchanged",
                    }
                )
                continue
            self._mark_state(ctx, status="processing", node_count=0, edge_count=0)
            try:
                extraction = self.extract_paper_kg(ctx)
                write_result = self._write_extraction(ctx, extraction)
                built += 1
                results.append(
                    {
                        "paper_id": ctx.paper_id,
                        "title": ctx.title,
                        "status": "complete",
                        **write_result,
                    }
                )
            except Exception as exc:  # pragma: no cover - defensive runtime path
                failed += 1
                logger.exception("GraphRAG build failed for %s: %s", ctx.paper_id, exc)
                self._mark_state(
                    ctx,
                    status="failed",
                    node_count=0,
                    edge_count=0,
                    error=str(exc),
                )
                results.append(
                    {
                        "paper_id": ctx.paper_id,
                        "title": ctx.title,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        return {
            "requested": len(contexts),
            "built": built,
            "skipped": skipped,
            "failed": failed,
            "items": results,
            "stats": self.status(),
        }

    def extract_paper_kg(self, ctx: PaperKGContext) -> dict[str, Any]:
        prompt = self._build_extraction_prompt(ctx)
        parsed: dict[str, Any] | None = None
        try:
            result = self.llm.complete_json(
                prompt,
                stage="graph_rag_extract",
                max_tokens=2600,
                max_retries=1,
                request_timeout=90,
            )
            parsed = result.parsed_json
        except Exception as exc:
            logger.warning("GraphRAG LLM extraction failed for %s: %s", ctx.paper_id, exc)
        normalized = self._normalize_extraction(parsed or {})
        if not normalized["nodes"]:
            normalized = self._fallback_extract(ctx)
        return normalized

    def query(
        self,
        query: str,
        *,
        top_k: int = 6,
        paper_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_query = _clean_text(query)
        if not normalized_query:
            return {
                "query": "",
                "used_graph": False,
                "coverage": {"status": "empty_query"},
                "nodes": [],
                "edges": [],
                "papers": [],
                "citations": [],
                "evidence": [],
                "evidence_pack": "查询为空，未执行 GraphRAG。",
            }

        with session_scope() as session:
            paper_repo = PaperRepository(session)
            kg_repo = ResearchKGRepository(session)
            cit_repo = CitationRepository(session)
            analysis_repo = AnalysisRepository(session)

            seed_papers = self._retrieve_seed_papers(
                paper_repo,
                query=normalized_query,
                top_k=max(3, top_k),
                paper_ids=paper_ids,
            )
            seed_paper_ids = [str(p.id) for p in seed_papers]
            seed_nodes = kg_repo.search_nodes(normalized_query, limit=max(6, top_k * 3))
            node_ids = [str(node.id) for node in seed_nodes]
            edges_by_nodes = kg_repo.list_edges_for_node_ids(node_ids, limit=max(30, top_k * 10))
            edges_by_papers = kg_repo.list_edges_for_paper_ids(seed_paper_ids, limit=max(30, top_k * 10))

            edge_map: dict[str, ResearchKGEdge] = {}
            for edge in [*edges_by_nodes, *edges_by_papers]:
                edge_map[str(edge.id)] = edge
            edges = list(edge_map.values())

            all_node_ids = set(node_ids)
            for edge in edges:
                all_node_ids.add(str(edge.source_node_id))
                all_node_ids.add(str(edge.target_node_id))
            nodes = self._load_nodes_by_ids(kg_repo, list(all_node_ids))

            graph_paper_ids = set(seed_paper_ids)
            for node in nodes:
                graph_paper_ids.update(str(pid) for pid in _json_list((node.metadata_json or {}).get("paper_ids")))
            for edge in edges:
                graph_paper_ids.update(str(pid) for pid in _json_list((edge.metadata_json or {}).get("paper_ids")))
            if paper_ids:
                graph_paper_ids.update(str(pid) for pid in paper_ids if str(pid or "").strip())

            papers = paper_repo.list_by_ids(list(graph_paper_ids)) if graph_paper_ids else []
            paper_map = {str(p.id): p for p in papers}
            analysis_map = analysis_repo.contexts_for_papers(list(paper_map.keys()))
            citations = cit_repo.list_for_paper_ids(list(paper_map.keys()))

            serialized_nodes = self._rank_nodes(nodes, normalized_query)[: max(1, min(top_k * 3, 24))]
            serialized_edges = self._rank_edges(edges, normalized_query)[: max(1, min(top_k * 4, 32))]
            serialized_papers = self._serialize_papers(papers, analysis_map, limit=max(1, min(top_k * 2, 16)))
            serialized_citations = self._serialize_citations(citations, paper_map, limit=24)
            evidence = self._build_evidence_items(serialized_edges, serialized_papers)

        evidence_pack = self._build_evidence_pack(
            query=normalized_query,
            nodes=serialized_nodes,
            edges=serialized_edges,
            papers=serialized_papers,
            citations=serialized_citations,
        )
        return {
            "query": normalized_query,
            "used_graph": bool(serialized_nodes or serialized_edges),
            "coverage": {
                "status": "ok" if serialized_nodes or serialized_edges else "no_graph_hit",
                "node_count": len(serialized_nodes),
                "edge_count": len(serialized_edges),
                "paper_count": len(serialized_papers),
                "citation_count": len(serialized_citations),
            },
            "nodes": serialized_nodes,
            "edges": serialized_edges,
            "papers": serialized_papers,
            "citations": serialized_citations,
            "evidence": evidence,
            "evidence_pack": evidence_pack,
        }

    def _load_candidate_contexts(
        self,
        *,
        paper_ids: list[str] | None,
        limit: int,
    ) -> list[PaperKGContext]:
        with session_scope() as session:
            repo = PaperRepository(session)
            if paper_ids:
                papers = repo.list_by_ids([str(pid) for pid in paper_ids if str(pid or "").strip()])
            else:
                papers = repo.list_latest(limit=max(1, min(limit, 200)))
            return [self._paper_context_from_model(session, paper) for paper in papers[: max(1, limit)]]

    def _paper_context_from_model(self, session, paper: Paper) -> PaperKGContext:  # noqa: ANN001
        metadata = dict(paper.metadata_json or {})
        analysis_parts = [AnalysisRepository(session).contexts_for_papers([str(paper.id)]).get(str(paper.id), "")]
        for key in ("skim_report", "deep_report"):
            payload = metadata.get(key)
            if isinstance(payload, dict):
                analysis_parts.append(_clean_text(payload.get("markdown") or payload.get("summary") or payload))
        rounds = metadata.get("analysis_rounds")
        if isinstance(rounds, dict):
            final_notes = rounds.get("final_notes")
            if isinstance(final_notes, dict):
                analysis_parts.append(_clean_text(final_notes.get("markdown") or final_notes.get("summary")))
        ocr_excerpt = self._load_ocr_excerpt(str(paper.id), paper.pdf_path)
        content_blocks = [
            f"Title: {paper.title}",
            f"arXiv: {paper.arxiv_id}",
            f"Abstract: {paper.abstract}",
            "Saved Analysis:\n" + "\n\n".join(part for part in analysis_parts if _clean_text(part)),
            "OCR Markdown Excerpt:\n" + ocr_excerpt,
        ]
        content = "\n\n".join(block for block in content_blocks if _clean_text(block))
        content_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
        return PaperKGContext(
            paper_id=str(paper.id),
            title=paper.title or "",
            abstract=paper.abstract or "",
            arxiv_id=paper.arxiv_id or "",
            content=content,
            content_hash=content_hash,
        )

    def _load_ocr_excerpt(self, paper_id: str, pdf_path: str | None) -> str:
        if not pdf_path:
            return ""
        path = Path(str(pdf_path))
        if not path.exists():
            return ""
        try:
            bundle = MinerUOcrRuntime.get_cached_bundle(UUID(paper_id), str(path))
            if bundle is None:
                return ""
            if hasattr(bundle, "build_analysis_context"):
                return _clip(bundle.build_analysis_context(max_chars=6000), 6000)
        except Exception as exc:
            logger.debug("GraphRAG OCR excerpt skipped for %s: %s", paper_id, exc)
        return ""

    def _is_context_current(self, ctx: PaperKGContext) -> bool:
        with session_scope() as session:
            state = ResearchKGRepository(session).get_paper_state(ctx.paper_id)
            return bool(
                state
                and state.status == "complete"
                and state.content_hash == ctx.content_hash
            )

    def _mark_state(
        self,
        ctx: PaperKGContext,
        *,
        status: str,
        node_count: int,
        edge_count: int,
        error: str = "",
    ) -> None:
        with session_scope() as session:
            ResearchKGRepository(session).upsert_paper_state(
                paper_id=ctx.paper_id,
                content_hash=ctx.content_hash,
                status=status,
                node_count=node_count,
                edge_count=edge_count,
                error=error,
                built_at=datetime.now(UTC) if status == "complete" else None,
            )

    def _write_extraction(self, ctx: PaperKGContext, extraction: dict[str, Any]) -> dict[str, Any]:
        with session_scope() as session:
            kg_repo = ResearchKGRepository(session)
            local_node_map: dict[str, ResearchKGNode] = {}
            for item in extraction.get("nodes") or []:
                node = kg_repo.upsert_node(
                    node_type=item["type"],
                    name=item["name"],
                    summary=item.get("summary", ""),
                    paper_id=ctx.paper_id,
                    metadata={"sources": ["graphrag_extract"]},
                )
                local_node_map[str(item["id"])] = node
            edge_count = 0
            for item in extraction.get("edges") or []:
                source = local_node_map.get(str(item.get("source")))
                target = local_node_map.get(str(item.get("target")))
                if source is None or target is None or source.id == target.id:
                    continue
                kg_repo.upsert_edge(
                    source_node_id=str(source.id),
                    target_node_id=str(target.id),
                    edge_type=str(item.get("type") or "related_to"),
                    paper_id=ctx.paper_id,
                    evidence=str(item.get("evidence") or ""),
                    weight=float(item.get("weight") or 1.0),
                    metadata={"sources": ["graphrag_extract"]},
                )
                edge_count += 1
            kg_repo.upsert_paper_state(
                paper_id=ctx.paper_id,
                content_hash=ctx.content_hash,
                status="complete",
                node_count=len(local_node_map),
                edge_count=edge_count,
                built_at=datetime.now(UTC),
            )
            return {"node_count": len(local_node_map), "edge_count": edge_count}

    def _build_extraction_prompt(self, ctx: PaperKGContext) -> str:
        allowed_nodes = ", ".join(sorted(KG_NODE_TYPES))
        allowed_edges = ", ".join(sorted(KG_EDGE_TYPES))
        return (
            "你是科研论文知识图谱抽取器。请从单篇论文上下文中抽取面向 GraphRAG 问答的核心实体和关系。\n"
            f"允许的节点类型：{allowed_nodes}。\n"
            f"允许的关系类型：{allowed_edges}。\n"
            "要求：\n"
            "1. 节点最多 16 个，关系最多 24 条。\n"
            "2. 节点名称要短，优先保留英文术语、方法名、数据集名、指标名。\n"
            "3. 每条关系必须有来自上下文的 evidence 短句，不要编造。\n"
            "4. 如果信息不足，保守抽取 title/abstract/analysis 中明确出现的内容。\n"
            "输出 JSON 格式：\n"
            '{"nodes":[{"id":"n1","type":"method","name":"BLIP","summary":"..."}],'
            '"edges":[{"source":"n1","target":"n2","type":"addresses","evidence":"...","weight":1.0}]}'
            "\n\n"
            f"论文 ID：{ctx.paper_id}\n"
            f"标题：{ctx.title}\n"
            f"arXiv：{ctx.arxiv_id}\n"
            f"上下文：\n{ctx.content[:12000]}"
        )

    def _normalize_extraction(self, raw: dict[str, Any]) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        seen_names: set[tuple[str, str]] = set()
        for index, item in enumerate(_json_list(raw.get("nodes")), start=1):
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("name"))
            if not name:
                continue
            node_type = _clean_text(item.get("type")).lower()
            if node_type not in KG_NODE_TYPES:
                node_type = "concept"
            key = (node_type, ResearchKGRepository.normalize_name(name))
            if key in seen_names:
                continue
            seen_names.add(key)
            nodes.append(
                {
                    "id": _clean_text(item.get("id")) or f"n{index}",
                    "type": node_type,
                    "name": name[:512],
                    "summary": _clip(item.get("summary"), 500),
                }
            )
            if len(nodes) >= 16:
                break
        node_ids = {node["id"] for node in nodes}
        edges: list[dict[str, Any]] = []
        for item in _json_list(raw.get("edges")):
            if not isinstance(item, dict):
                continue
            source = _clean_text(item.get("source"))
            target = _clean_text(item.get("target"))
            if source not in node_ids or target not in node_ids or source == target:
                continue
            edge_type = _clean_text(item.get("type")).lower()
            if edge_type not in KG_EDGE_TYPES:
                edge_type = "related_to"
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "type": edge_type,
                    "evidence": _clip(item.get("evidence"), 600),
                    "weight": float(item.get("weight") or 1.0),
                }
            )
            if len(edges) >= 24:
                break
        return {"nodes": nodes, "edges": edges}

    def _fallback_extract(self, ctx: PaperKGContext) -> dict[str, Any]:
        title = _clean_text(ctx.title)
        abstract = _clean_text(ctx.abstract)
        candidate_names = [title]
        candidate_names.extend(
            match.group(0)
            for match in re.finditer(r"\b[A-Z][A-Za-z0-9-]{2,}(?:\s+[A-Z][A-Za-z0-9-]{2,}){0,2}\b", ctx.content[:3000])
        )
        nodes: list[dict[str, Any]] = []
        for name in candidate_names:
            cleaned = _clean_text(name)
            if not cleaned or any(node["name"].casefold() == cleaned.casefold() for node in nodes):
                continue
            node_type = "method" if len(nodes) == 0 else "concept"
            nodes.append(
                {
                    "id": f"n{len(nodes) + 1}",
                    "type": node_type,
                    "name": cleaned[:180],
                    "summary": _clip(abstract or ctx.content, 300),
                }
            )
            if len(nodes) >= 8:
                break
        if not nodes:
            nodes.append(
                {
                    "id": "n1",
                    "type": "concept",
                    "name": title or ctx.paper_id,
                    "summary": _clip(ctx.content, 300),
                }
            )
        edges = []
        if len(nodes) > 1:
            for node in nodes[1:]:
                edges.append(
                    {
                        "source": nodes[0]["id"],
                        "target": node["id"],
                        "type": "related_to",
                        "evidence": _clip(abstract or ctx.content, 400),
                        "weight": 0.4,
                    }
                )
        return {"nodes": nodes, "edges": edges}

    def _retrieve_seed_papers(
        self,
        paper_repo: PaperRepository,
        *,
        query: str,
        top_k: int,
        paper_ids: list[str] | None,
    ) -> list[Paper]:
        if paper_ids:
            return paper_repo.list_by_ids([str(pid) for pid in paper_ids if str(pid or "").strip()])[:top_k]
        lexical = paper_repo.full_text_candidates(query=query, limit=max(top_k, 8))
        semantic: list[Paper] = []
        try:
            qvec = self.llm.embed_text(query)
            semantic = paper_repo.semantic_candidates(qvec, limit=max(top_k, 8))
        except Exception as exc:
            logger.debug("GraphRAG semantic seed retrieval skipped: %s", exc)
        seen: set[str] = set()
        merged: list[Paper] = []
        for paper in [*lexical, *semantic]:
            pid = str(paper.id)
            if pid in seen:
                continue
            seen.add(pid)
            merged.append(paper)
            if len(merged) >= top_k:
                break
        return merged

    def _load_nodes_by_ids(self, kg_repo: ResearchKGRepository, node_ids: list[str]) -> list[ResearchKGNode]:
        nodes: list[ResearchKGNode] = []
        seen: set[str] = set()
        for node_id in node_ids:
            if node_id in seen:
                continue
            seen.add(node_id)
            node = kg_repo.get_node(node_id)
            if node is not None:
                nodes.append(node)
        return nodes

    def _rank_nodes(self, nodes: list[ResearchKGNode], query: str) -> list[dict[str, Any]]:
        tokens = _tokenize(query)
        serialized: list[tuple[int, dict[str, Any]]] = []
        for node in nodes:
            metadata = dict(node.metadata_json or {})
            haystack = f"{node.name} {node.summary} {node.normalized_name}".casefold()
            score = sum(1 for token in tokens if token in haystack)
            score += len(_json_list(metadata.get("paper_ids")))
            serialized.append(
                (
                    score,
                    {
                        "id": str(node.id),
                        "type": node.node_type,
                        "name": node.name,
                        "summary": node.summary,
                        "paper_ids": _json_list(metadata.get("paper_ids"))[:12],
                    },
                )
            )
        serialized.sort(key=lambda item: item[0], reverse=True)
        return [item for _score, item in serialized]

    def _rank_edges(self, edges: list[ResearchKGEdge], query: str) -> list[dict[str, Any]]:
        tokens = _tokenize(query)
        node_ids = list({str(edge.source_node_id) for edge in edges} | {str(edge.target_node_id) for edge in edges})
        with session_scope() as session:
            kg_repo = ResearchKGRepository(session)
            node_map = {
                str(node.id): {"name": node.name, "type": node.node_type}
                for node in self._load_nodes_by_ids(kg_repo, node_ids)
            }
        serialized: list[tuple[float, dict[str, Any]]] = []
        for edge in edges:
            source = node_map.get(str(edge.source_node_id))
            target = node_map.get(str(edge.target_node_id))
            metadata = dict(edge.metadata_json or {})
            source_name = str((source or {}).get("name") or str(edge.source_node_id))
            target_name = str((target or {}).get("name") or str(edge.target_node_id))
            source_type = str((source or {}).get("type") or "")
            target_type = str((target or {}).get("type") or "")
            haystack = f"{edge.edge_type} {edge.evidence} {source_name} {target_name}".casefold()
            score = float(edge.weight or 1.0) + sum(1 for token in tokens if token in haystack)
            serialized.append(
                (
                    score,
                    {
                        "id": str(edge.id),
                        "source_node_id": str(edge.source_node_id),
                        "source_name": source_name,
                        "source_type": source_type,
                        "target_node_id": str(edge.target_node_id),
                        "target_name": target_name,
                        "target_type": target_type,
                        "type": edge.edge_type,
                        "evidence": edge.evidence,
                        "weight": edge.weight,
                        "paper_ids": _json_list(metadata.get("paper_ids"))[:12],
                        "evidence_items": _json_list(metadata.get("evidence_items"))[:6],
                    },
                )
            )
        serialized.sort(key=lambda item: item[0], reverse=True)
        return [item for _score, item in serialized]

    def _serialize_papers(
        self,
        papers: list[Paper],
        analysis_map: dict[str, str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for paper in papers[:limit]:
            pid = str(paper.id)
            metadata = dict(paper.metadata_json or {})
            analysis = _clean_text(analysis_map.get(pid, ""))
            items.append(
                {
                    "id": pid,
                    "title": paper.title,
                    "arxiv_id": paper.arxiv_id,
                    "year": paper.publication_date.year if paper.publication_date else None,
                    "abstract_preview": _clip(paper.abstract, 360),
                    "analysis_preview": _clip(analysis, 520),
                    "venue": metadata.get("venue") or metadata.get("citation_venue") or "",
                    "has_analysis": bool(analysis),
                }
            )
        return items

    def _serialize_citations(self, citations, paper_map: dict[str, Paper], *, limit: int) -> list[dict[str, Any]]:  # noqa: ANN001
        items: list[dict[str, Any]] = []
        for citation in citations:
            source = paper_map.get(str(citation.source_paper_id))
            target = paper_map.get(str(citation.target_paper_id))
            if source is None or target is None:
                continue
            items.append(
                {
                    "source_paper_id": str(citation.source_paper_id),
                    "source_title": source.title,
                    "target_paper_id": str(citation.target_paper_id),
                    "target_title": target.title,
                    "context": _clip(citation.context, 300),
                }
            )
            if len(items) >= limit:
                break
        return items

    def _build_evidence_items(
        self,
        edges: list[dict[str, Any]],
        papers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for edge in edges[:12]:
            paper_ids = _json_list(edge.get("paper_ids"))
            evidence.append(
                {
                    "source": "research_kg_edge",
                    "paper_id": paper_ids[0] if paper_ids else None,
                    "title": f"{edge.get('source_name')} -> {edge.get('target_name')}",
                    "snippet": edge.get("evidence") or "",
                    "relation": edge.get("type"),
                }
            )
        for paper in papers[:8]:
            evidence.append(
                {
                    "source": "paper_analysis",
                    "paper_id": paper.get("id"),
                    "title": paper.get("title"),
                    "snippet": paper.get("analysis_preview") or paper.get("abstract_preview") or "",
                }
            )
        return evidence

    def _build_evidence_pack(
        self,
        *,
        query: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        papers: list[dict[str, Any]],
        citations: list[dict[str, Any]],
    ) -> str:
        lines = [
            "# GraphRAG Evidence Pack",
            "",
            f"Query: {query}",
            "",
            "## Matched Entities",
        ]
        if nodes:
            for node in nodes[:12]:
                lines.append(f"- [{node.get('type')}] {node.get('name')}: {_clip(node.get('summary'), 180)}")
        else:
            lines.append("- No KG entity hit.")
        lines.extend(["", "## Relations"])
        if edges:
            for edge in edges[:16]:
                lines.append(
                    "- "
                    f"{edge.get('source_name')} --{edge.get('type')}--> {edge.get('target_name')}: "
                    f"{_clip(edge.get('evidence'), 220)}"
                )
        else:
            lines.append("- No KG relation hit.")
        lines.extend(["", "## Papers"])
        if papers:
            for paper in papers[:10]:
                preview = paper.get("analysis_preview") or paper.get("abstract_preview")
                lines.append(f"- {paper.get('title')} ({paper.get('year') or '?'}, {paper.get('arxiv_id') or 'no arXiv'}): {_clip(preview, 220)}")
        else:
            lines.append("- No paper context hit.")
        lines.extend(["", "## Citation Links"])
        if citations:
            for citation in citations[:12]:
                lines.append(f"- {citation.get('source_title')} -> {citation.get('target_title')}: {_clip(citation.get('context'), 160)}")
        else:
            lines.append("- No in-library citation edge hit.")
        return "\n".join(lines).strip()
