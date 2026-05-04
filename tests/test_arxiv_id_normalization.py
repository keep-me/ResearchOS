from __future__ import annotations

from packages.ai.paper.paper_ops_service import normalize_manual_paper_id
from packages.ai.paper.pipelines import PaperPipelines


def test_normalize_manual_paper_id_accepts_arxiv_prefix() -> None:
    assert normalize_manual_paper_id("arXiv:2504.02647") == "2504.02647"
    assert normalize_manual_paper_id("arXiv：2504.02647v2") == "2504.02647"


def test_normalize_manual_paper_id_accepts_arxiv_urls() -> None:
    assert normalize_manual_paper_id("https://arxiv.org/abs/2504.02647v1?foo=bar") == "2504.02647"
    assert normalize_manual_paper_id("https://arxiv.org/pdf/2504.02647.pdf#page=1") == "2504.02647"


def test_pipeline_normalize_arxiv_id_matches_manual_normalizer() -> None:
    assert PaperPipelines._normalize_arxiv_id("arXiv:cs/9901001v2") == "cs/9901001"
