from __future__ import annotations

from datetime import date

from packages.ai.research.arxiv_trend_service import ArxivTrendService


def _paper(arxiv_id: str = "2604.00001") -> dict:
    return {
        "arxiv_id": arxiv_id,
        "title": "Large Language Model Agents for Reasoning",
        "abstract": "We study reasoning and tool use in large language model agents.",
        "published_at": "2026-04-17T12:00:00Z",
        "categories": ["cs.AI", "cs.CL"],
        "primary_category": "cs.AI",
    }


def test_today_snapshot_uses_latest_day_with_parsed_papers() -> None:
    class FakeTrendService(ArxivTrendService):
        def __init__(self) -> None:
            self.calls: list[date] = []

        def _fetch_day(self, day: date, sample_limit: int) -> tuple[int, list[dict]]:
            self.calls.append(day)
            if len(self.calls) == 3:
                return 10, [_paper()]
            return 0, []

    service = FakeTrendService()

    result = service.today_snapshot(sample_limit=100, fallback_days=5)

    assert result["available"] is True
    assert result["query_date"] == service.calls[2].isoformat()
    assert "最近非空发布日" in result["window_label"]
    assert result["sample_size"] == 1
    assert len(service.calls) == 3


def test_today_snapshot_skips_total_results_without_parsed_entries() -> None:
    class FakeTrendService(ArxivTrendService):
        def __init__(self) -> None:
            self.calls: list[date] = []

        def _fetch_day(self, day: date, sample_limit: int) -> tuple[int, list[dict]]:
            self.calls.append(day)
            if len(self.calls) == 1:
                return 25, []
            return 8, [_paper("2604.00002")]

    service = FakeTrendService()

    result = service.today_snapshot(sample_limit=100, fallback_days=2)

    assert result["available"] is True
    assert result["query_date"] == service.calls[1].isoformat()
    assert result["recent_papers"][0]["arxiv_id"] == "2604.00002"
    assert len(service.calls) == 2
