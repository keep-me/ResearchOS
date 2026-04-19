from datetime import date

from packages.integrations.arxiv_client import _build_arxiv_query


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
