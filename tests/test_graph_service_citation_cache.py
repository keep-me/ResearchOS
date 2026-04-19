from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from packages.ai.research import graph_service
from packages.integrations.semantic_scholar_client import RichCitationInfo


class _FakeCitationProvider:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs
        self.calls = 0

    def fetch_rich_citations(
        self,
        title: str,
        ref_limit: int = 30,
        cite_limit: int = 30,
        *,
        arxiv_id: str | None = None,
    ) -> list[RichCitationInfo]:
        del title, ref_limit, cite_limit, arxiv_id
        self.calls += 1
        return [
            RichCitationInfo(
                scholar_id="ref-1",
                title="Reference One",
                year=2024,
                venue="NeurIPS",
                citation_count=18,
                arxiv_id="2401.00001",
                abstract="reference abstract",
                direction="reference",
            ),
            RichCitationInfo(
                scholar_id="cit-1",
                title="Citation One",
                year=2025,
                venue="ICLR",
                citation_count=7,
                arxiv_id=None,
                abstract="citation abstract",
                direction="citation",
            ),
        ]


class _FakePaperRepository:
    def __init__(self, session) -> None:
        del session

    def get_by_id(self, paper_id: str):
        return SimpleNamespace(
            id=paper_id,
            title="Cached Paper",
            arxiv_id="2401.99999",
            metadata_json={},
        )

    def list_all(self, limit: int = 50000):
        del limit
        return [
            SimpleNamespace(
                id="paper-lib-ref",
                title="Reference One",
                arxiv_id="2401.00001",
                metadata_json={"title_zh": "参考文献一"},
            )
        ]


class _FakeCitationRepository:
    def __init__(self, session) -> None:
        del session
        self.edges: list[tuple[str, str, str | None]] = []

    def upsert_edge(
        self,
        source_paper_id: str,
        target_paper_id: str,
        context: str | None = None,
    ) -> None:
        self.edges.append((source_paper_id, target_paper_id, context))


@contextmanager
def _fake_session_scope():
    yield object()


def test_citation_detail_uses_persisted_cache_and_force_refresh(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        graph_service,
        "get_settings",
        lambda: SimpleNamespace(
            pdf_storage_root=tmp_path / "papers",
            openalex_email=None,
            semantic_scholar_api_key=None,
        ),
    )
    monkeypatch.setattr(graph_service, "CitationProvider", _FakeCitationProvider)
    monkeypatch.setattr(graph_service, "LLMClient", lambda: object())
    monkeypatch.setattr(graph_service, "WikiContextGatherer", lambda: object())
    monkeypatch.setattr(graph_service, "session_scope", _fake_session_scope)
    monkeypatch.setattr(graph_service, "PaperRepository", _FakePaperRepository)
    monkeypatch.setattr(graph_service, "CitationRepository", _FakeCitationRepository)
    monkeypatch.setattr(
        graph_service.GraphService,
        "_translate_citation_titles",
        lambda self, titles, max_titles=60: {},
    )

    service = graph_service.GraphService()
    provider = service.citations

    first = service.citation_detail("paper-1")
    second = service.citation_detail("paper-1")
    refreshed = service.citation_detail("paper-1", force_refresh=True)

    assert isinstance(provider, _FakeCitationProvider)
    assert provider.calls == 2
    assert first == second
    assert refreshed == first
    assert service._citation_detail_cache_path("paper-1").exists()
    assert first["stats"]["total_references"] == 1
    assert first["references"][0]["in_library"] is True
    assert first["references"][0]["title_zh"] == "参考文献一"
