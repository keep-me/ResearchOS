from __future__ import annotations

from packages.integrations.openalex_client import OpenAlexClient


def test_work_to_search_result_prefers_conference_location_over_arxiv_primary_location() -> None:
    work = {
        "id": "https://openalex.org/W1",
        "title": "A Conference Paper with arXiv Mirror",
        "publication_year": 2024,
        "publication_date": "2024-07-01",
        "cited_by_count": 10,
        "authorships": [],
        "abstract_inverted_index": {},
        "ids": {},
        "primary_location": {
            "landing_page_url": "https://arxiv.org/abs/2401.00001",
            "source": {
                "display_name": "arXiv (Cornell University)",
                "type": "repository",
            },
        },
        "locations": [
            {
                "landing_page_url": "https://proceedings.mlr.press/v235/example.html",
                "source": {
                    "display_name": "Proceedings of the 41st International Conference on Machine Learning",
                    "type": "conference",
                },
            },
        ],
    }

    item = OpenAlexClient._work_to_search_result(work)

    assert item is not None
    assert item["venue"] == "Proceedings of the 41st International Conference on Machine Learning"
    assert item["venue_type"] == "conference"


def test_work_to_search_result_falls_back_to_primary_location_when_no_better_source_exists() -> None:
    work = {
        "id": "https://openalex.org/W2",
        "title": "A Preprint Only Work",
        "publication_year": 2024,
        "publication_date": "2024-03-01",
        "cited_by_count": 2,
        "authorships": [],
        "abstract_inverted_index": {},
        "ids": {},
        "primary_location": {
            "landing_page_url": "https://arxiv.org/abs/2403.00002",
            "source": {
                "display_name": "arXiv (Cornell University)",
                "type": "repository",
            },
        },
        "locations": [],
    }

    item = OpenAlexClient._work_to_search_result(work)

    assert item is not None
    assert item["venue"] == "arXiv (Cornell University)"
    assert item["venue_type"] == "repository"
