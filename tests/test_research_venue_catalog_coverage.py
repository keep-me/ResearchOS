from __future__ import annotations

from packages.agent import research_tool_runtime
from packages.ai.research import research_venue_catalog as venue_catalog


class _CoverageFakeOpenAlexClient:
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        del args, kwargs

    def search_works(self, query: str, *, max_results: int = 20) -> list[dict]:
        del query, max_results
        return [
            {
                "title": "Scaling Laws for Multimodal Pretraining",
                "abstract": "",
                "publication_year": 2024,
                "publication_date": "2024-12-10",
                "citation_count": 88,
                "venue": "Conference on Neural Information Processing Systems",
                "venue_type": "conference",
                "authors": ["Alice"],
                "arxiv_id": None,
                "openalex_id": "https://openalex.org/W-neurips",
                "source_url": "https://openalex.org/W-neurips",
                "pdf_url": None,
                "source": "openalex",
            },
            {
                "title": "Efficient Foundation Models for Robotics",
                "abstract": "",
                "publication_year": 2024,
                "publication_date": "2024-07-18",
                "citation_count": 51,
                "venue": "Proceedings of the 41st International Conference on Machine Learning",
                "venue_type": "conference",
                "authors": ["Bob"],
                "arxiv_id": None,
                "openalex_id": "https://openalex.org/W-icml",
                "source_url": "https://openalex.org/W-icml",
                "pdf_url": None,
                "source": "openalex",
            },
            {
                "title": "Vision-Language Alignment at Scale",
                "abstract": "",
                "publication_year": 2025,
                "publication_date": "2025-06-21",
                "citation_count": 67,
                "venue": "Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition",
                "venue_type": "conference",
                "authors": ["Carol"],
                "arxiv_id": None,
                "openalex_id": "https://openalex.org/W-cvpr",
                "source_url": "https://openalex.org/W-cvpr",
                "pdf_url": None,
                "source": "openalex",
            },
            {
                "title": "Long-Context Retrieval for Language Agents",
                "abstract": "",
                "publication_year": 2024,
                "publication_date": "2024-08-02",
                "citation_count": 39,
                "venue": "Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics",
                "venue_type": "conference",
                "authors": ["Dave"],
                "arxiv_id": None,
                "openalex_id": "https://openalex.org/W-acl",
                "source_url": "https://openalex.org/W-acl",
                "pdf_url": None,
                "source": "openalex",
            },
            {
                "title": "Open-World Web Agents",
                "abstract": "",
                "publication_year": 2024,
                "publication_date": "2024-05-10",
                "citation_count": 28,
                "venue": "Proceedings of The Web Conference 2024",
                "venue_type": "conference",
                "authors": ["Eve"],
                "arxiv_id": None,
                "openalex_id": "https://openalex.org/W-www",
                "source_url": "https://openalex.org/W-www",
                "pdf_url": None,
                "source": "openalex",
            },
            {
                "title": "Benchmarking Workshop Baselines",
                "abstract": "",
                "publication_year": 2024,
                "publication_date": "2024-04-01",
                "citation_count": 3,
                "venue": "Proceedings of the Workshop on Foundation Models",
                "venue_type": "conference",
                "authors": ["Mallory"],
                "arxiv_id": None,
                "openalex_id": "https://openalex.org/W-workshop",
                "source_url": "https://openalex.org/W-workshop",
                "pdf_url": None,
                "source": "openalex",
            },
        ]

    def close(self) -> None:
        return None


def test_ccf_a_conference_catalog_has_expected_core_coverage() -> None:
    conferences = [entry for entry in venue_catalog._CCF_A_VENUES if entry.venue_type == "conference"]
    conference_names = {entry.display_name for entry in conferences}

    assert len(conferences) == 54
    assert "Conference on Neural Information Processing Systems" in conference_names
    assert "International Conference on Machine Learning" in conference_names
    assert "IEEE/CVF Conference on Computer Vision and Pattern Recognition" in conference_names
    assert "Annual Meeting of the Association for Computational Linguistics" in conference_names
    assert "The Web Conference" in conference_names


def test_ccf_a_catalog_matches_realistic_openalex_proceedings_names() -> None:
    samples = {
        "Proceedings of the AAAI Conference on Artificial Intelligence": "AAAI",
        "Proceedings of the 41st International Conference on Machine Learning": "ICML",
        "Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition": "CVPR",
        "Proceedings of the 62nd Annual Meeting of the Association for Computational Linguistics": "ACL",
        "Proceedings of The Web Conference 2024": "WWW",
        "Advances in Neural Information Processing Systems": "NeurIPS",
    }

    for venue_name, alias in samples.items():
        assert venue_catalog.venue_tier_for_name(venue_name) == "ccf_a"
        assert venue_catalog.matches_venue_filter(
            venue_name,
            raw_venue_type="conference",
            venue_tier="ccf_a",
            venue_type="conference",
            venue_names=[alias],
        )


def test_ccf_a_catalog_rejects_workshop_and_extended_abstract_variants() -> None:
    rejected_samples = [
        "2022 IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops (CVPRW)",
        "CHI Conference on Human Factors in Computing Systems Extended Abstracts",
        "Proceedings of the ACL Workshop on Trustworthy Language Models",
    ]

    for venue_name in rejected_samples:
        assert venue_catalog.venue_tier_for_name(venue_name) is None
        assert venue_catalog.matches_venue_filter(
            venue_name,
            raw_venue_type="conference",
            venue_tier="ccf_a",
            venue_type="conference",
        ) is False


def test_search_literature_returns_only_ccf_a_conferences_for_realistic_venues(monkeypatch) -> None:
    monkeypatch.setattr(research_tool_runtime, "OpenAlexClient", _CoverageFakeOpenAlexClient)

    result = research_tool_runtime._search_literature(
        "foundation model",
        source_scope="openalex",
        venue_tier="ccf_a",
        venue_type="conference",
        max_results=10,
    )

    assert result.success is True
    papers = result.data["papers"]
    assert len(papers) == 5
    assert {paper["venue_tier"] for paper in papers} == {"ccf_a"}
    assert {paper["venue_type"] for paper in papers} == {"conference"}
    assert "Proceedings of the Workshop on Foundation Models" not in {paper["venue"] for paper in papers}


def test_search_literature_can_filter_specific_ccf_a_conference_aliases(monkeypatch) -> None:
    monkeypatch.setattr(research_tool_runtime, "OpenAlexClient", _CoverageFakeOpenAlexClient)

    result = research_tool_runtime._search_literature(
        "foundation model",
        source_scope="openalex",
        venue_tier="ccf_a",
        venue_type="conference",
        venue_names=["ICML", "CVPR", "WWW"],
        max_results=10,
    )

    assert result.success is True
    papers = result.data["papers"]
    assert [paper["venue"] for paper in papers] == [
        "Proceedings of the 41st International Conference on Machine Learning",
        "Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition",
        "Proceedings of The Web Conference 2024",
    ]
