from packages.ai.paper.analysis_options import (
    get_deep_detail_profile,
    get_reasoning_detail_profile,
    normalize_analysis_detail_level,
    normalize_reasoning_level,
    resolve_paper_analysis_levels,
)
from packages.ai.paper.prompts import build_deep_prompt, build_reasoning_prompt


def test_normalize_analysis_levels_fallback_to_medium():
    assert normalize_analysis_detail_level("HIGH") == "high"
    assert normalize_analysis_detail_level("unknown") == "medium"
    assert normalize_analysis_detail_level(None) == "medium"


def test_normalize_reasoning_levels_fallback_to_default():
    assert normalize_reasoning_level("XHIGH") == "xhigh"
    assert normalize_reasoning_level("weird") == "default"
    assert normalize_reasoning_level(None) == "default"


def test_resolve_paper_analysis_levels_syncs_reasoning_to_detail():
    assert resolve_paper_analysis_levels("medium", "xhigh") == ("medium", "medium")
    assert resolve_paper_analysis_levels("high", "default") == ("high", "high")


def test_resolve_paper_analysis_levels_supports_legacy_reasoning_only_calls():
    assert resolve_paper_analysis_levels(None, "xhigh") == ("high", "high")
    assert resolve_paper_analysis_levels(None, "low") == ("low", "low")
    assert resolve_paper_analysis_levels(None, None) == ("medium", "medium")


def test_detail_profiles_expand_with_higher_levels():
    low = get_deep_detail_profile("low")
    high = get_deep_detail_profile("high")
    assert int(low["vision_pages"]) < int(high["vision_pages"])
    assert int(low["text_chars"]) < int(high["text_chars"])
    assert int(low["max_tokens"]) < int(high["max_tokens"])


def test_reasoning_profile_uses_base_settings_and_level():
    low = get_reasoning_detail_profile("low", base_pages=8, base_tokens=3072, base_timeout=90)
    high = get_reasoning_detail_profile("high", base_pages=8, base_tokens=3072, base_timeout=90)
    assert int(low["pages"]) < int(high["pages"])
    assert int(low["excerpt_chars"]) < int(high["excerpt_chars"])
    assert int(low["timeout_seconds"]) < int(high["timeout_seconds"])


def test_build_deep_prompt_mentions_selected_detail_level():
    prompt = build_deep_prompt("Test Paper", "Mock content", detail_level="high")
    assert "详略级别：high" in prompt
    assert "高详略精读" in prompt


def test_build_reasoning_prompt_keeps_full_evidence_text():
    text = "A" * 4000 + "B" * 4000 + "C" * 4000
    low_prompt = build_reasoning_prompt("Title", "Abstract", text, detail_level="low")
    high_prompt = build_reasoning_prompt("Title", "Abstract", text, detail_level="high")

    assert "详略级别\nlow" in low_prompt
    assert "详略级别\nhigh" in high_prompt
    assert "CCCC" in low_prompt
    assert "CCCC" in high_prompt
