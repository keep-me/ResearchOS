from fastapi import HTTPException

from apps.api.routers.papers import _build_pdf_reader_ai_prompt


def test_translate_prompt_requires_chinese_only_translation():
    prompt = _build_pdf_reader_ai_prompt("translate", "This is a paper fragment.")
    assert "简体中文" in prompt
    assert "只输出中文译文" in prompt
    assert "不要添加英文改写" in prompt


def test_analyze_prompt_requires_structured_chinese_analysis():
    prompt = _build_pdf_reader_ai_prompt("analyze", "This is a paper fragment.")
    assert "用简体中文分析" in prompt
    assert "关键方法、结论、假设、限制" in prompt


def test_legacy_summarize_action_is_still_mapped_to_analysis_prompt():
    prompt = _build_pdf_reader_ai_prompt("summarize", "This is a paper fragment.")
    assert "用简体中文分析" in prompt
    assert "关键方法、结论、假设、限制" in prompt


def test_ask_prompt_includes_question_and_excerpt():
    prompt = _build_pdf_reader_ai_prompt(
        "ask",
        "Selected excerpt.",
        question="这段话的核心方法是什么？",
    )
    assert "用户问题" in prompt
    assert "这段话的核心方法是什么？" in prompt
    assert "Selected excerpt." in prompt


def test_ask_prompt_requires_question():
    try:
        _build_pdf_reader_ai_prompt("ask", "Selected excerpt.", question=" ")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "question is required for ask action"
    else:
        raise AssertionError("Expected HTTPException for empty ask question")
