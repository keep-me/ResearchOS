from __future__ import annotations

from uuid import uuid4

from packages.ai.paper import figure_service, paper_ops_service


def test_extract_paper_figures_payload_reuses_saved_candidates(monkeypatch) -> None:
    paper_id = uuid4()
    saved_items = [
        {
            "id": "fig-1",
            "page_number": 1,
            "image_index": 0,
            "image_type": "figure",
            "caption": "Figure 1. Cached candidate.",
            "description": "",
            "ocr_markdown": "Figure 1. Cached candidate.",
            "analysis_markdown": "",
            "candidate_source": "arxiv_source",
            "analyzed": False,
            "has_image": True,
        },
        {
            "id": "fig-2",
            "page_number": 2,
            "image_index": 0,
            "image_type": "figure",
            "caption": "Figure 2. Cached candidate.",
            "description": "",
            "ocr_markdown": "Figure 2. Cached candidate.",
            "analysis_markdown": "",
            "candidate_source": "arxiv_source",
            "analyzed": False,
            "has_image": True,
        },
    ]

    monkeypatch.setattr(
        figure_service.FigureService,
        "get_paper_analyses",
        classmethod(lambda cls, pid: saved_items if pid == paper_id else []),
    )
    monkeypatch.setattr(
        paper_ops_service,
        "ensure_paper_pdf",
        lambda session, repo, paper, paper_id: (_ for _ in ()).throw(AssertionError("should not prepare pdf")),
    )

    payload = paper_ops_service.extract_paper_figures_payload(paper_id, max_figures=1)

    assert payload["paper_id"] == str(paper_id)
    assert payload["count"] == 1
    assert payload["items"] == saved_items[:1]
