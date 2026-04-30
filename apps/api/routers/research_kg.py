"""GraphRAG / research KG API routes."""

from __future__ import annotations

from fastapi import APIRouter

from packages.ai.research.graph_rag_service import GraphRAGService
from packages.domain.schemas import GraphRAGBuildReq, GraphRAGQueryReq

router = APIRouter()


@router.get("/research-kg/status")
def research_kg_status() -> dict:
    return GraphRAGService().status()


@router.post("/research-kg/build")
def build_research_kg(body: GraphRAGBuildReq) -> dict:
    return GraphRAGService().build_papers(
        paper_ids=body.paper_ids or None,
        limit=body.limit,
        force=body.force,
    )


@router.post("/research-kg/query")
def query_research_kg(body: GraphRAGQueryReq) -> dict:
    return GraphRAGService().query(
        body.query,
        top_k=body.top_k,
        paper_ids=body.paper_ids or None,
    )
