from datetime import date

from packages.integrations.arxiv_client import _arxiv_rate_limit_delay, _build_arxiv_query


def test_build_arxiv_query_has_no_default_date_filter():
    query = _build_arxiv_query("vision transformer")
    assert query == "all:vision AND all:transformer"
    assert "submittedDate:[" not in query


def test_build_arxiv_query_appends_explicit_date_range():
    query = _build_arxiv_query(
        "all:vision AND cat:cs.CV",
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 11),
    )
    assert query.startswith("all:vision AND cat:cs.CV")
    assert "submittedDate:[" in query


def test_build_arxiv_query_preserves_existing_submitted_date_filter():
    query = _build_arxiv_query(
        "all:vision AND submittedDate:[20260301000000 TO 20260311000000]",
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 11),
    )
    assert query.count("submittedDate:[") == 1


def test_arxiv_rate_limit_delay_uses_retry_after_header():
    assert _arxiv_rate_limit_delay(0, "45") == 45


def test_arxiv_rate_limit_delay_backs_off_without_header():
    assert _arxiv_rate_limit_delay(0) == 30
    assert _arxiv_rate_limit_delay(1) == 60
    assert _arxiv_rate_limit_delay(10) == 180
