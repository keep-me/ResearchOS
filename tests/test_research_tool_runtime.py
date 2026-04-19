from __future__ import annotations

from datetime import date

from packages.agent import research_tool_runtime
from packages.ai.research.research_venue_catalog import (
    classify_venue_type,
    matches_venue_filter,
    venue_tier_for_name,
)
from packages.domain.schemas import PaperCreate


class _FakeOpenAlexClient:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs

    def search_works(self, query: str, *, max_results: int = 20) -> list[dict]:
        del query, max_results
        return [
            {
                "title": "Diffusion Policies at Scale",
                "abstract": "",
                "publication_year": 2024,
                "publication_date": "2024-12-01",
                "citation_count": 123,
                "venue": "Conference on Neural Information Processing Systems",
                "venue_type": "conference",
                "authors": ["Alice", "Bob"],
                "arxiv_id": "2401.00001",
                "openalex_id": "https://openalex.org/W1",
                "source_url": "https://openalex.org/W1",
                "pdf_url": None,
                "source": "openalex",
            },
            {
                "title": "Diffusion Policies in Production",
                "abstract": "",
                "publication_year": 2023,
                "publication_date": "2023-07-01",
                "citation_count": 88,
                "venue": "Journal of Machine Learning Research",
                "venue_type": "journal",
                "authors": ["Carol"],
                "arxiv_id": None,
                "openalex_id": "https://openalex.org/W2",
                "source_url": "https://openalex.org/W2",
                "pdf_url": None,
                "source": "openalex",
            },
            {
                "title": "Diffusion Policies Workshop Paper",
                "abstract": "",
                "publication_year": 2024,
                "publication_date": "2024-06-01",
                "citation_count": 5,
                "venue": "Some Workshop",
                "venue_type": "conference",
                "authors": ["Dave"],
                "arxiv_id": None,
                "openalex_id": "https://openalex.org/W3",
                "source_url": "https://openalex.org/W3",
                "pdf_url": None,
                "source": "openalex",
            },
        ]

    def close(self) -> None:
        return None


class _FakeArxivClient:
    def search_candidates(self, query: str, *, max_results: int = 20, fetch_limit: int | None = None):  # noqa: ANN201
        del query, max_results, fetch_limit
        return [
            PaperCreate(
                arxiv_id="2401.00001",
                title="Diffusion Policies at Scale",
                abstract="",
                publication_date=date(2024, 1, 2),
                metadata={"authors": ["Alice"], "categories": ["cs.LG"]},
            ),
            PaperCreate(
                arxiv_id="2402.00002",
                title="Fresh arXiv Diffusion Policy Result",
                abstract="",
                publication_date=date(2024, 2, 3),
                metadata={"authors": ["Eve"], "categories": ["cs.RO"]},
            ),
        ]


def test_research_venue_catalog_matches_ccf_a_aliases() -> None:
    assert venue_tier_for_name("Conference on Neural Information Processing Systems") == "ccf_a"
    assert venue_tier_for_name("Journal of Machine Learning Research") == "ccf_a"
    assert classify_venue_type("conference", "Conference on Neural Information Processing Systems") == "conference"
    assert matches_venue_filter(
        "Conference on Neural Information Processing Systems",
        raw_venue_type="conference",
        venue_tier="ccf_a",
        venue_type="conference",
        venue_names=["NeurIPS"],
    )


def test_search_literature_filters_ccf_a_conferences(monkeypatch) -> None:
    monkeypatch.setattr(research_tool_runtime, "OpenAlexClient", _FakeOpenAlexClient)
    monkeypatch.setattr(research_tool_runtime, "ArxivClient", _FakeArxivClient)

    result = research_tool_runtime._search_literature(
        "diffusion policy",
        source_scope="hybrid",
        venue_tier="ccf_a",
        venue_type="conference",
        max_results=10,
    )

    assert result.success is True
    papers = result.data["papers"]
    assert len(papers) == 1
    assert papers[0]["venue"] == "Conference on Neural Information Processing Systems"
    assert papers[0]["venue_tier"] == "ccf_a"
    assert result.data["source_counts"]["openalex"] == 1
    assert result.data["source_counts"]["arxiv"] == 0
    assert "arxiv" in result.data["skipped_sources"]


def test_search_literature_merges_openalex_and_arxiv_without_duplicates(monkeypatch) -> None:
    monkeypatch.setattr(research_tool_runtime, "OpenAlexClient", _FakeOpenAlexClient)
    monkeypatch.setattr(research_tool_runtime, "ArxivClient", _FakeArxivClient)

    result = research_tool_runtime._search_literature(
        "diffusion policy",
        source_scope="hybrid",
        max_results=10,
    )

    assert result.success is True
    papers = result.data["papers"]
    assert len(papers) == 4
    assert sum(1 for item in papers if item["arxiv_id"] == "2401.00001") == 1
    assert any(item["source"] == "openalex" and item["venue"] == "Journal of Machine Learning Research" for item in papers)
    assert any(item["source"] == "arxiv" and item["arxiv_id"] == "2402.00002" for item in papers)
