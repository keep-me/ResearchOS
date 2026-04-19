from __future__ import annotations

from packages.ai.project.paper_artifacts import (
    build_paper_improvement_bundle,
    extract_review_verdict,
    extract_score,
    parse_review_text,
)


def test_parse_review_text_matches_aris_score_and_action_patterns():
    parsed = parse_review_text(
        "# Review\n\n"
        "Score: 7/10\n"
        "Verdict: ALMOST\n\n"
        "Weaknesses:\n"
        "1. Clarify the anchor sampling setup.\n"
        "2. Add a stronger baseline discussion.\n"
    )

    assert parsed["score"] == 7.0
    assert parsed["verdict"] == "almost"
    assert parsed["action_items"] == [
        "Clarify the anchor sampling setup.",
        "Add a stronger baseline discussion.",
    ]


def test_extract_score_supports_score_of_pattern_without_guessing():
    assert extract_score("The reviewer reports a score of 6.5 after revision.") == 6.5
    assert extract_score("Verdict: ready for submission") is None


def test_extract_review_verdict_follows_aris_keyword_order():
    assert extract_review_verdict("Minor revisions required before submission.") == "almost"
    assert extract_review_verdict("Overall assessment: weak accept.") == "ready"
    assert extract_review_verdict("Substantial issues remain.") == "not ready"


def test_build_paper_improvement_bundle_persists_verdicts_and_action_items():
    bundle = build_paper_improvement_bundle(
        project_name="AnchorCoT",
        review_round_one="# Review 1",
        revision_notes="# Revision Notes",
        review_round_two="# Review 2",
        score_round_one=None,
        score_round_two=7.8,
        verdict_round_one="ready",
        verdict_round_two="almost",
        action_items_round_one=["Clarify the problem statement."],
        action_items_round_two=["Fix citation formatting."],
    )

    assert "| 1 | 内容评审 | N/A | ready |" in bundle["reports/paper-score-progression.md"]
    assert "| 2 | 修订后复审 | 7.8 | almost |" in bundle["reports/paper-score-progression.md"]
    assert "Fix citation formatting." in bundle["reports/paper-format-check.md"]
    assert '"verdict_round_one": "ready"' in bundle["paper/improvement-metadata.json"]
    assert '"action_items_round_two": [' in bundle["paper/improvement-metadata.json"]
