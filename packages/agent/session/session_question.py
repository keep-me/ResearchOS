"""OpenCode-like question tool helpers."""

from __future__ import annotations

from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_questions_payload(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question = _clean(item.get("question"))
        header = _clean(item.get("header"))
        options_input = item.get("options") if isinstance(item.get("options"), list) else []
        options: list[dict[str, str]] = []
        for option in options_input:
            if not isinstance(option, dict):
                continue
            label = _clean(option.get("label"))
            description = _clean(option.get("description"))
            if not label or not description:
                continue
            options.append(
                {
                    "label": label,
                    "description": description,
                }
            )
        if not question or not header or not options:
            continue
        payload: dict[str, Any] = {
            "question": question,
            "header": header,
            "options": options,
        }
        if item.get("multiple") is True:
            payload["multiple"] = True
        if item.get("custom") is False:
            payload["custom"] = False
        normalized.append(payload)
    return normalized


def pending_questions(permission_request: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(permission_request, dict):
        return []
    if _clean(permission_request.get("permission")) != "question":
        return []
    metadata = (
        permission_request.get("metadata")
        if isinstance(permission_request.get("metadata"), dict)
        else {}
    )
    return normalize_questions_payload(metadata.get("questions"))


def is_question_request(permission_request: dict[str, Any] | None) -> bool:
    return bool(pending_questions(permission_request))


def normalize_answers_payload(
    value: Any,
    questions: list[dict[str, Any]],
) -> list[list[str]]:
    raw_answers = value if isinstance(value, list) else []
    normalized: list[list[str]] = []
    for index, _question in enumerate(questions):
        entry = (
            raw_answers[index]
            if index < len(raw_answers) and isinstance(raw_answers[index], list)
            else []
        )
        answers: list[str] = []
        for item in entry:
            cleaned = _clean(item)
            if cleaned and cleaned not in answers:
                answers.append(cleaned)
        normalized.append(answers)
    return normalized


def format_answers(questions: list[dict[str, Any]], answers: list[list[str]]) -> str:
    formatted: list[str] = []
    for index, question in enumerate(questions):
        question_text = _clean(question.get("question"))
        question_answers = answers[index] if index < len(answers) else []
        answer_text = ", ".join(question_answers) if question_answers else "Unanswered"
        if question_text:
            formatted.append(f'"{question_text}"="{answer_text}"')
    return ", ".join(formatted)


def success_title(questions: list[dict[str, Any]]) -> str:
    count = len(questions)
    suffix = "s" if count != 1 else ""
    return f"Asked {count} question{suffix}"


def success_output(questions: list[dict[str, Any]], answers: list[list[str]]) -> str:
    return (
        f"User has answered your questions: {format_answers(questions, answers)}. "
        "You can now continue with the user's answers in mind."
    )


def rejected_output(note: str | None = None) -> str:
    message = _clean(note)
    if message:
        return f"User dismissed the question prompt: {message}."
    return "User dismissed the question prompt."
