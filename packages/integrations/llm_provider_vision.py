from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def vision_analyze(
    client,
    result_cls,
    *,
    sdk_client,
    image_base64: str,
    prompt: str,
    max_tokens: int,
    target,
):
    kwargs: dict = {
        "model": target.model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{image_base64}",
                    },
                ],
            }
        ],
        "store": False,
    }
    if max_tokens is not None:
        kwargs["max_output_tokens"] = max_tokens
    client._apply_variant_to_responses_kwargs(kwargs, target)
    response = sdk_client.responses.create(**kwargs)
    content, reasoning = client._extract_responses_text_and_reasoning(response)
    in_tokens, out_tokens, _ = client._extract_responses_usage(response)
    in_cost, out_cost = client._estimate_cost(
        model=target.model,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
    )
    return result_cls(
        content=(content or reasoning or "").strip(),
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        input_cost_usd=in_cost,
        output_cost_usd=out_cost,
        total_cost_usd=in_cost + out_cost,
        reasoning_content=reasoning or None,
    )


def vision_openai_compatible(
    client,
    result_cls,
    *,
    sdk_client,
    image_base64: str,
    prompt: str,
    max_tokens: int,
    target,
):
    try:
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}",
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        kwargs = {
            "model": target.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        client._apply_variant_to_chat_kwargs(kwargs, target)
        response = sdk_client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        content = message.content or ""
        reasoning_content = getattr(message, "reasoning_content", None) or ""
        if not content and reasoning_content:
            content = reasoning_content
        usage = response.usage
        in_tokens = usage.prompt_tokens if usage else None
        out_tokens = usage.completion_tokens if usage else None
        in_cost, out_cost = client._estimate_cost(
            model=target.model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
        )
        return result_cls(
            content=content,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            input_cost_usd=in_cost,
            output_cost_usd=out_cost,
            total_cost_usd=in_cost + out_cost,
            reasoning_content=reasoning_content or None,
        )
    except Exception as exc:
        logger.warning("Vision OpenAI-compatible call failed: %s", exc)
        return None
