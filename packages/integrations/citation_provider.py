"""
双源引用数据提供者
OpenAlex（10 req/s）为主力，Semantic Scholar 为兜底
"""
from __future__ import annotations

import logging

from packages.integrations.openalex_client import OpenAlexClient
from packages.integrations.semantic_scholar_client import (
    CitationEdge,
    RichCitationInfo,
    SemanticScholarClient,
)

logger = logging.getLogger(__name__)


def _normalize_citation_title(title: str | None) -> str:
    return " ".join((title or "").strip().lower().split())


def _citation_key(item: RichCitationInfo) -> tuple[str, str]:
    if item.arxiv_id:
        return item.direction, f"arxiv:{item.arxiv_id.strip().lower()}"
    if item.scholar_id:
        return item.direction, f"scholar:{item.scholar_id.strip().lower()}"
    return item.direction, f"title:{_normalize_citation_title(item.title)}"


def _merge_directional_results(
    primary: list[RichCitationInfo],
    secondary: list[RichCitationInfo],
    *,
    direction: str,
    limit: int,
) -> list[RichCitationInfo]:
    if limit <= 0:
        return []

    merged: list[RichCitationInfo] = []
    seen: set[tuple[str, str]] = set()

    for source in (primary, secondary):
        for item in source:
            if item.direction != direction:
                continue
            key = _citation_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= limit:
                return merged

    return merged


class CitationProvider:
    """统一的引用数据入口，自动 fallback"""

    def __init__(
        self,
        openalex_email: str | None = None,
        scholar_api_key: str | None = None,
    ) -> None:
        self.openalex = OpenAlexClient(email=openalex_email)
        self.scholar = SemanticScholarClient(api_key=scholar_api_key)

    def fetch_edges_by_title(
        self, title: str, limit: int = 8, *, arxiv_id: str | None = None,
    ) -> list[CitationEdge]:
        try:
            edges = self.openalex.fetch_edges_by_title(title, limit=limit, arxiv_id=arxiv_id)
            if edges:
                logger.debug("OpenAlex returned %d edges for '%s'", len(edges), title[:50])
                return edges
        except Exception as exc:
            logger.warning("OpenAlex failed for '%s': %s, falling back to Scholar", title[:50], exc)

        try:
            edges = self.scholar.fetch_edges_by_title(title, limit=limit, arxiv_id=arxiv_id)
            if edges:
                logger.debug("Scholar returned %d edges for '%s'", len(edges), title[:50])
            return edges
        except Exception as exc:
            logger.warning("Scholar also failed for '%s': %s", title[:50], exc)
            return []

    def fetch_rich_citations(
        self,
        title: str,
        ref_limit: int = 30,
        cite_limit: int = 30,
        *,
        arxiv_id: str | None = None,
    ) -> list[RichCitationInfo]:
        openalex_results: list[RichCitationInfo] = []
        try:
            openalex_results = self.openalex.fetch_rich_citations(
                title, ref_limit=ref_limit, cite_limit=cite_limit, arxiv_id=arxiv_id,
            )
            if openalex_results:
                logger.debug(
                    "OpenAlex rich citations: %d for '%s'",
                    len(openalex_results),
                    title[:50],
                )
        except Exception as exc:
            logger.warning("OpenAlex rich failed for '%s': %s, falling back", title[:50], exc)
            openalex_results = []

        oa_refs = sum(1 for item in openalex_results if item.direction == "reference")
        oa_cites = sum(1 for item in openalex_results if item.direction == "citation")
        if openalex_results and oa_refs > 0 and oa_cites > 0:
            return openalex_results

        try:
            scholar_results = self.scholar.fetch_rich_citations(
                title, ref_limit=ref_limit, cite_limit=cite_limit, arxiv_id=arxiv_id,
            )
            if not openalex_results:
                return scholar_results

            merged_refs = _merge_directional_results(
                openalex_results,
                scholar_results,
                direction="reference",
                limit=ref_limit,
            )
            merged_cites = _merge_directional_results(
                openalex_results,
                scholar_results,
                direction="citation",
                limit=cite_limit,
            )
            logger.debug(
                "Merged rich citations for '%s': refs=%d, cites=%d",
                title[:50],
                len(merged_refs),
                len(merged_cites),
            )
            return merged_refs + merged_cites
        except Exception as exc:
            logger.warning("Scholar rich also failed for '%s': %s", title[:50], exc)
            return openalex_results

    def fetch_batch_metadata(self, titles: list[str], max_papers: int = 10) -> list[dict]:
        try:
            results = self.openalex.fetch_batch_metadata(titles, max_papers=max_papers)
            if results:
                return results
        except Exception as exc:
            logger.warning("OpenAlex batch metadata failed: %s, falling back", exc)

        try:
            return self.scholar.fetch_batch_metadata(titles, max_papers=max_papers)
        except Exception as exc:
            logger.warning("Scholar batch metadata also failed: %s", exc)
            return []

    def fetch_paper_metadata(
        self,
        title: str,
        *,
        arxiv_id: str | None = None,
        allow_fallback: bool = True,
    ) -> dict | None:
        """单篇元数据，优先 OpenAlex；在允许时再回退 Semantic Scholar"""
        try:
            result = self.openalex.fetch_paper_metadata(title, arxiv_id=arxiv_id)
            if result:
                return result
        except Exception as exc:
            logger.warning("OpenAlex single metadata failed for '%s': %s", title[:50], exc)
        if not allow_fallback:
            return None
        return self.scholar.fetch_paper_metadata(title, arxiv_id=arxiv_id)

    def close(self) -> None:
        self.openalex.close()
        self.scholar.close()
