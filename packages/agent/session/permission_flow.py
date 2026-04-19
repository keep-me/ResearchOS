"""Permission pause/resume helpers for agent session lifecycle."""

from __future__ import annotations


def finalize_paused_session_abort(session_id: str) -> bool:
    from packages.agent.session.session_runtime import _finalize_paused_session_abort

    return _finalize_paused_session_abort(session_id)


__all__ = ["finalize_paused_session_abort"]

