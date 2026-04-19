from __future__ import annotations

from packages.agent.session import sse_events
from packages.agent.session.session_processor import PromptEvent, coerce_prompt_event
from packages.agent.session import session_runtime


def test_sse_events_parse_and_format_roundtrip():
    raw = sse_events.format_sse_event("text_delta", {"content": "hello", "index": 1})

    assert raw == 'event: text_delta\ndata: {"content": "hello", "index": 1}\n\n'
    assert sse_events.parse_sse_event(raw) == ("text_delta", {"content": "hello", "index": 1})
    assert session_runtime._parse_sse_event(raw) == ("text_delta", {"content": "hello", "index": 1})
    assert session_runtime._format_sse_event("done", {}) == "event: done\ndata: {}\n\n"


def test_sse_events_coerce_prompt_event_compatibility():
    event_name, data, raw = coerce_prompt_event(PromptEvent("done", {}))

    assert event_name == "done"
    assert data == {}
    assert raw == "event: done\ndata: {}\n\n"
    assert sse_events.parse_sse_event("event: custom\ndata: not-json\n\n") == ("custom", {"raw": "not-json"})

