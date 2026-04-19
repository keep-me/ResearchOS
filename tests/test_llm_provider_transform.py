from packages.integrations import llm_provider_transform


def test_coerce_openai_message_text_keeps_ascii_reasoning_words_separated() -> None:
    assert (
        llm_provider_transform.coerce_openai_message_text(
            [
                {"text": "I"},
                {"text": "think"},
                {"text": "I"},
                {"text": "should"},
                {"text": "inspect"},
                {"text": "the"},
                {"text": "repo."},
            ]
        )
        == "I think I should inspect the repo."
    )


def test_coerce_openai_message_text_keeps_cjk_compact() -> None:
    assert (
        llm_provider_transform.coerce_openai_message_text(
            [
                {"text": "先分析"},
                {"text": "上下文。"},
            ]
        )
        == "先分析上下文。"
    )
