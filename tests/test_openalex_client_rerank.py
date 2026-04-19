from __future__ import annotations

from packages.integrations.openalex_client import OpenAlexClient


def test_search_works_prefers_exact_title_and_published_venue(monkeypatch) -> None:
    client = OpenAlexClient()

    broad_results = {
        "results": [
            {
                "id": "https://openalex.org/W-repo",
                "title": "Attention Is All You Need",
                "publication_year": 2017,
                "publication_date": "2017-06-01",
                "cited_by_count": 1000,
                "primary_location": {
                    "source": {"display_name": "arXiv (Cornell University)", "type": "repository"},
                },
                "locations": [],
                "authorships": [],
                "ids": {"arxiv": "1706.03762"},
            },
            {
                "id": "https://openalex.org/W-conf",
                "title": "Attention Is All You Need",
                "publication_year": 2017,
                "publication_date": "2017-12-01",
                "cited_by_count": 900,
                "primary_location": {
                    "source": {"display_name": "arXiv (Cornell University)", "type": "repository"},
                },
                "locations": [
                    {
                        "source": {"display_name": "Neural Information Processing Systems", "type": "conference"},
                    },
                ],
                "authorships": [],
                "ids": {"doi": "https://doi.org/10.5555/3295222.3295349"},
            },
            {
                "id": "https://openalex.org/W-fuzzy",
                "title": "Attention Is Nearly All You Need",
                "publication_year": 2018,
                "publication_date": "2018-01-01",
                "cited_by_count": 200,
                "primary_location": {
                    "source": {"display_name": "Some Journal", "type": "journal"},
                },
                "locations": [],
                "authorships": [],
                "ids": {},
            },
        ]
    }

    def fake_get(path: str, params=None):  # noqa: ANN001
        if path == "/works" and isinstance(params, dict) and params.get("search") == "Attention Is All You Need":
            return broad_results
        if path == "/works" and isinstance(params, dict) and str(params.get("filter") or "").startswith("title.search:"):
            return broad_results
        return None

    monkeypatch.setattr(client, "_get", fake_get)

    items = client.search_works("Attention Is All You Need", max_results=5)

    assert items[0]["openalex_id"] == "https://openalex.org/W-conf"
    assert items[0]["venue_type"] == "conference"
    assert items[1]["openalex_id"] == "https://openalex.org/W-repo"


def test_search_works_prefers_exact_doi_lookup(monkeypatch) -> None:
    client = OpenAlexClient()
    doi_query = "10.5555/3295222.3295349"

    exact_work = {
        "id": "https://openalex.org/W-doi",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
        "publication_date": "2017-12-01",
        "cited_by_count": 900,
        "primary_location": {
            "source": {"display_name": "Neural Information Processing Systems", "type": "conference"},
        },
        "locations": [],
        "authorships": [],
        "ids": {"doi": f"https://doi.org/{doi_query}"},
    }

    def fake_get(path: str, params=None):  # noqa: ANN001
        if path == "/works" and isinstance(params, dict) and params.get("filter") == f"doi:{doi_query}":
            return {"results": [exact_work]}
        if path == "/works" and isinstance(params, dict):
            return {"results": []}
        return None

    monkeypatch.setattr(client, "_get", fake_get)

    items = client.search_works(doi_query, max_results=5)

    assert items[0]["openalex_id"] == "https://openalex.org/W-doi"
    assert items[0]["venue"] == "Neural Information Processing Systems"


def test_search_works_does_not_fallback_to_broad_results_for_missing_exact_doi(monkeypatch) -> None:
    client = OpenAlexClient()

    def fake_get(path: str, params=None):  # noqa: ANN001
        if path == "/works" and isinstance(params, dict) and str(params.get("filter") or "").startswith("doi:10.1234/not-found"):
            return {"results": []}
        if path == "/works" and isinstance(params, dict) and params.get("search") == "10.1234/not-found":
            return {
                "results": [
                    {
                        "id": "https://openalex.org/W-noise",
                        "title": "An Irrelevant Paper",
                        "publication_year": 2024,
                        "cited_by_count": 9,
                        "primary_location": {
                            "source": {"display_name": "Some Conference", "type": "conference"},
                        },
                        "locations": [],
                        "authorships": [],
                        "ids": {},
                    }
                ]
            }
        return None

    monkeypatch.setattr(client, "_get", fake_get)

    items = client.search_works("10.1234/not-found", max_results=5)

    assert items == []


def test_search_works_prefers_exact_arxiv_lookup(monkeypatch) -> None:
    client = OpenAlexClient()
    arxiv_id = "1706.03762"

    exact_work = {
        "id": "https://openalex.org/W-arxiv",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
        "publication_date": "2017-06-12",
        "cited_by_count": 800,
        "primary_location": {
            "source": {"display_name": "arXiv (Cornell University)", "type": "repository"},
        },
        "locations": [
            {
                "source": {"display_name": "Neural Information Processing Systems", "type": "conference"},
            },
        ],
        "authorships": [],
        "ids": {"arxiv": arxiv_id},
    }

    def fake_get(path: str, params=None):  # noqa: ANN001
        if path == "/works" and isinstance(params, dict) and str(params.get("filter") or "").startswith("doi:10.48550/arxiv.1706.03762"):
            return {"results": [exact_work]}
        if path == "/works" and isinstance(params, dict):
            return {"results": []}
        return None

    monkeypatch.setattr(client, "_get", fake_get)

    items = client.search_works(arxiv_id, max_results=5)

    assert items[0]["openalex_id"] == "https://openalex.org/W-arxiv"
    assert items[0]["venue_type"] == "conference"


def test_search_works_recovers_published_variant_from_same_title_family(monkeypatch) -> None:
    client = OpenAlexClient()
    arxiv_id = "1706.03762"

    repo_work = {
        "id": "https://openalex.org/W-repo-only",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
        "publication_date": "2017-06-12",
        "cited_by_count": 1200,
        "primary_location": {
            "source": {"display_name": "arXiv (Cornell University)", "type": "repository"},
        },
        "locations": [],
        "authorships": [],
        "ids": {"arxiv": arxiv_id},
    }
    conference_variant = {
        "id": "https://openalex.org/W-conf-variant",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
        "publication_date": "2017-12-01",
        "cited_by_count": 1100,
        "primary_location": {
            "source": {"display_name": "arXiv (Cornell University)", "type": "repository"},
        },
        "locations": [
            {
                "source": {"display_name": "Neural Information Processing Systems", "type": "conference"},
            },
        ],
        "authorships": [],
        "ids": {"doi": "https://doi.org/10.5555/3295222.3295349"},
    }

    def fake_get(path: str, params=None):  # noqa: ANN001
        if path == "/works" and isinstance(params, dict) and str(params.get("filter") or "").startswith("doi:10.48550/arxiv.1706.03762"):
            return {"results": [repo_work]}
        if path == "/works" and isinstance(params, dict) and str(params.get("filter") or "").startswith('title.search:"Attention Is All You Need"'):
            return {"results": [repo_work, conference_variant]}
        if path == "/works" and isinstance(params, dict):
            return {"results": []}
        return None

    monkeypatch.setattr(client, "_get", fake_get)

    items = client.search_works(arxiv_id, max_results=5)

    assert items[0]["openalex_id"] == "https://openalex.org/W-conf-variant"
    assert items[0]["venue_type"] == "conference"
