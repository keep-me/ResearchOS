"""ResearchOS native agent service with opencode-like session state."""

from __future__ import annotations

import copy
import json
import logging
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.agent import (
    session_bus,
    session_pending,
    session_question,
    session_retry,
    session_snapshot,
)
from packages.agent.runtime.acp_service import get_acp_registry_service
from packages.agent.runtime.agent_backends import (
    CLAW_AGENT_BACKEND_ID,
    DEFAULT_AGENT_BACKEND_ID,
    LEGACY_CLI_AGENT_BACKEND_ID,
    is_native_agent_backend,
    normalize_agent_backend_id,
)
from packages.agent.runtime.agent_runtime_manager import get_agent_runtime_manager
from packages.agent.runtime.agent_runtime_policy import (
    STEP_LIMIT_SUMMARY_PROMPT,
)
from packages.agent.runtime.agent_runtime_policy import (
    build_reasoning_profile_prompt as _shared_build_reasoning_profile_prompt,
)
from packages.agent.runtime.agent_runtime_policy import (
    build_step_limit_reached_notice as _shared_build_step_limit_reached_notice,
)
from packages.agent.runtime.agent_runtime_policy import (
    get_max_tool_steps as _shared_get_max_tool_steps,
)
from packages.agent.runtime.agent_runtime_policy import (
    is_auto_compaction_enabled as _shared_is_auto_compaction_enabled,
)
from packages.agent.runtime.agent_runtime_policy import (
    is_tool_progress_placeholder_text as _shared_is_tool_progress_placeholder_text,
)
from packages.agent.runtime.agent_runtime_policy import (
    should_inject_max_steps_prompt as _shared_should_inject_max_steps_prompt,
)
from packages.agent.runtime.agent_runtime_state import (  # noqa: F401 - compatibility surface for tests and callers that monkeypatch agent_service.
    ensure_session,
    get_todos,
    normalize_mode,
)
from packages.agent.runtime.agent_transcript import (
    build_cli_chat_prompt_text as _build_shared_cli_chat_prompt_text,
)
from packages.agent.runtime.agent_transcript import (
    build_cli_transcript as _build_shared_cli_transcript,
)
from packages.agent.runtime.agent_transcript import (
    format_orphan_tool_message_context as _shared_format_orphan_tool_message_context,
)
from packages.agent.runtime.agent_transcript import (
    format_tool_result_turn_summary as _shared_format_tool_result_turn_summary,
)
from packages.agent.runtime.agent_transcript import (
    json_loads_maybe as _shared_json_loads_maybe,
)
from packages.agent.runtime.agent_transcript import (
    resolve_tool_result_followup_text as _shared_resolve_tool_result_followup_text,
)
from packages.agent.runtime.agent_transcript import (
    serialize_tool_context_data as _shared_serialize_tool_context_data,
)
from packages.agent.runtime.agent_transcript import (
    summarize_tool_text as _shared_summarize_tool_text,
)
from packages.agent.runtime.cli_agent_service import get_cli_agent_service
from packages.agent.runtime.permission_next import (
    authorize_tool_call,
    effective_ruleset,
)
from packages.agent.runtime.permission_next import (
    create_request as create_permission_request,
)
from packages.agent.runtime.permission_next import (
    disabled as disabled_tools_from_rules,
)
from packages.agent.runtime.permission_next import (
    manages_tool as permission_manages_tool,
)
from packages.agent.runtime.permission_next import (
    reply as reply_permission,
)
from packages.agent.session import sse_events
from packages.agent.session.session_bus import SessionBusEvent
from packages.agent.session.session_compaction import (
    detect_overflow_error,
    is_overflow_tokens,
    latest_auto_compaction_target,
    summarize_session,
)
from packages.agent.session.session_lifecycle import (
    acquire_prompt_instance,
    claim_prompt_callback,
    clear_session_abort,
    drain_prompt_callbacks,
    finish_prompt_instance,
    get_prompt_instance,
    is_session_aborted,
    mark_prompt_instance_running,
    pause_prompt_instance,
    queue_prompt_callback,
    reject_prompt_callbacks,
)
from packages.agent.session.session_plan import build_plan_mode_reminder
from packages.agent.session.session_processor import (
    ModelTurnResult,  # noqa: F401 - compatibility surface for tests and callers that monkeypatch agent_service.
    ModelTurnRuntimeCallbacks,
    ModelTurnRuntimeConfig,
    PermissionResponseCallbacks,
    PermissionResponseConfig,
    PromptEvent,
    PromptEventStreamDriver,  # noqa: F401 - compatibility surface for tests and callers that monkeypatch agent_service.
    PromptLifecycleConfig,
    PromptLifecycleSession,
    PromptLoopCallbacks,
    PromptLoopRuntimeConfig,
    PromptStreamControl,
    SessionProcessor,
    ToolCallProcessingCallbacks,
    ToolCallProcessingConfig,
    ToolExecutionCallbacks,
    ToolExecutionConfig,
    ToolPendingActionConfig,
)
from packages.agent.session.session_processor import (
    iter_callback_stream as _processor_iter_callback_stream,
)
from packages.agent.session.session_processor import (
    prompt_event as _processor_prompt_event,
)
from packages.agent.session.session_processor import (
    prompt_terminal_error as _processor_prompt_terminal_error,
)
from packages.agent.session.session_processor import (
    serialize_prompt_event_stream as _processor_serialize_prompt_event_stream,
)
from packages.agent.session.session_processor import (
    stream_model_turn_events as _processor_stream_model_turn_events,
)
from packages.agent.session.session_processor import (
    stream_permission_response_runtime as _processor_stream_permission_response_runtime,
)
from packages.agent.session.session_processor import (
    stream_prompt_lifecycle as _processor_stream_prompt_lifecycle,
)
from packages.agent.session.session_processor import (
    stream_prompt_loop as _processor_stream_prompt_loop,
)
from packages.agent.session.session_processor import (
    stream_tool_call_processing as _processor_stream_tool_call_processing,
)
from packages.agent.session.session_processor import (
    stream_tool_execution_events as _processor_stream_tool_execution_events,
)
from packages.agent.session.session_runtime import (
    get_latest_user_message_id,
    get_session_message_by_id,
    get_session_record,
    get_session_turn_state,
    list_session_messages,
    load_agent_messages,
    set_session_status,
)
from packages.agent.tools.mounted_paper_context import (
    build_mounted_papers_prompt,
    resolve_research_skill_ids,
)
from packages.agent.tools.skill_registry import (  # noqa: F401 - compatibility surface for tests and callers that monkeypatch agent_service.
    get_local_skill_detail,
    list_local_skills,
)
from packages.agent.tools.tool_registry import (
    build_turn_tools,
    get_tool_definition,
    tool_registry_names,
)
from packages.agent.tools.tool_runtime import (
    AgentToolContext,
    ToolProgress,
    ToolResult,
    execute_tool_stream,
)
from packages.agent.workspace.workspace_executor import (
    get_assistant_exec_policy,
    list_workspace_roots,  # noqa: F401 - compatibility surface for tests and callers that monkeypatch agent_service.
    local_shell_command_to_string,
    should_confirm_workspace_action,
)
from packages.config import get_settings
from packages.integrations.llm_client import LLMClient

logger = logging.getLogger(__name__)


def get_claw_runtime_manager():  # noqa: ANN201
    return get_agent_runtime_manager()


_DEFAULT_GET_ASSISTANT_EXEC_POLICY = get_assistant_exec_policy
_REFERENCE_PROMPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "reference"
    / "opencode-dev"
    / "packages"
    / "opencode"
    / "src"
    / "session"
    / "prompt"
)
_REFERENCE_PROMPT_CACHE: dict[str, str] = {}
_REFERENCE_PROMPT_FALLBACKS: dict[str, str] = {
    "codex_header.txt": (
        "You are OpenCode, a coding agent that works inside the user's workspace.\n"
        "Be direct, complete the task end-to-end, and keep outputs actionable."
    ),
    "beast.txt": (
        "You are OpenCode, a coding agent that should reason carefully and execute precisely."
    ),
    "gemini.txt": (
        "You are OpenCode, a coding agent optimized for large-context analysis and implementation."
    ),
    "anthropic.txt": ("You are OpenCode, a coding agent focused on reliable multi-step execution."),
    "trinity.txt": (
        "You are OpenCode, a coding agent that should stay grounded in the local workspace."
    ),
    "qwen.txt": (
        "You are OpenCode, a coding agent. Prefer concrete execution over abstract discussion."
    ),
    "build-switch.txt": (
        "The operational mode has changed from plan to build.\n"
        "Stop planning and start executing the requested implementation steps."
    ),
    "max-steps.txt": ("CRITICAL - MAXIMUM STEPS REACHED\n已达到最大步数，停止继续调用工具。"),
}

_DEFAULT_AGENT_EXTENSION_TOOLS = {
    "search_papers",
}


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


MAX_AUTO_COMPACTION_ATTEMPTS = 4


def _load_reference_prompt(name: str) -> str:
    cached = _REFERENCE_PROMPT_CACHE.get(name)
    if cached is not None:
        return cached
    path = _REFERENCE_PROMPT_DIR / name
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        logger.debug("Failed to load reference prompt %s", path, exc_info=True)
        text = str(_REFERENCE_PROMPT_FALLBACKS.get(name) or "").strip()
    _REFERENCE_PROMPT_CACHE[name] = text
    return text


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    metadata: dict[str, Any] | None = None
    provider_executed: bool = False


@dataclass
class AgentRuntimeOptions:
    session_id: str
    mode: str = "build"
    workspace_path: str | None = None
    workspace_server_id: str | None = None
    reasoning_level: str = "default"
    model_override: str | None = None
    active_skill_ids: list[str] = field(default_factory=list)
    mounted_paper_ids: list[str] = field(default_factory=list)
    mounted_primary_paper_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id or "").strip(),
            "mode": normalize_mode(self.mode),
            "workspace_path": str(self.workspace_path or "").strip() or None,
            "workspace_server_id": str(self.workspace_server_id or "").strip() or None,
            "reasoning_level": str(self.reasoning_level or "default").strip().lower() or "default",
            "model_override": str(self.model_override or "").strip() or None,
            "active_skill_ids": [
                str(item).strip() for item in (self.active_skill_ids or []) if str(item).strip()
            ],
            "mounted_paper_ids": [
                str(item).strip() for item in (self.mounted_paper_ids or []) if str(item).strip()
            ],
            "mounted_primary_paper_id": (str(self.mounted_primary_paper_id or "").strip() or None),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> AgentRuntimeOptions | None:
        if not isinstance(payload, dict):
            return None
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return None
        return cls(
            session_id=session_id,
            mode=normalize_mode(str(payload.get("mode") or "build")),
            workspace_path=str(payload.get("workspace_path") or "").strip() or None,
            workspace_server_id=str(payload.get("workspace_server_id") or "").strip() or None,
            reasoning_level=str(payload.get("reasoning_level") or "default").strip().lower()
            or "default",
            model_override=str(payload.get("model_override") or "").strip() or None,
            active_skill_ids=[
                str(item).strip()
                for item in (payload.get("active_skill_ids") or [])
                if str(item).strip()
            ],
            mounted_paper_ids=[
                str(item).strip()
                for item in (payload.get("mounted_paper_ids") or [])
                if str(item).strip()
            ],
            mounted_primary_paper_id=(
                str(payload.get("mounted_primary_paper_id") or "").strip() or None
            ),
        )


@dataclass(frozen=True)
class OutputConstraint:
    limit: int
    unit: str
    label: str


@dataclass(frozen=True)
class LatestUserPromptShaping:
    tools: dict[str, bool] | None
    request: str
    system: str
    output_constraint: str


@dataclass
class StreamPersistenceConfig:
    session_id: str
    parent_id: str | None = None
    assistant_meta: dict[str, Any] | None = None
    assistant_message_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "parent_id": self.parent_id,
            "assistant_meta": copy.deepcopy(self.assistant_meta)
            if isinstance(self.assistant_meta, dict)
            else None,
            "assistant_message_id": self.assistant_message_id,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> StreamPersistenceConfig | None:
        if not isinstance(payload, dict):
            return None
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return None
        assistant_meta = payload.get("assistant_meta")
        return cls(
            session_id=session_id,
            parent_id=str(payload.get("parent_id") or "").strip() or None,
            assistant_meta=copy.deepcopy(assistant_meta)
            if isinstance(assistant_meta, dict)
            else None,
            assistant_message_id=str(payload.get("assistant_message_id") or "").strip() or None,
        )


class PersistedSSEStream:
    def __init__(
        self, iterator: Iterator[str], *, prompt_control: PromptStreamControl | None = None
    ) -> None:
        self._iterator = iter(iterator)
        self._researchos_persisted = True
        if prompt_control is not None:
            self._researchos_prompt_control = prompt_control

    def __iter__(self) -> PersistedSSEStream:
        return self

    def __next__(self) -> str:
        return next(self._iterator)

    def close(self) -> None:
        close_method = getattr(self._iterator, "close", None)
        if callable(close_method):
            close_method()


_prompt_event = _processor_prompt_event
_serialize_prompt_event_stream = _processor_serialize_prompt_event_stream


PendingAction = session_pending.PendingAction


@dataclass
class QueuedCallbackRunResult:
    control: PromptStreamControl | None = None
    message_id: str | None = None
    error: str | None = None
    paused: bool = False
    result: dict[str, Any] | None = None
    next_payload: dict[str, Any] | None = None


def _pending_action_from_state(payload: dict[str, Any]) -> PendingAction | None:
    return session_pending.pending_action_from_state(
        payload,
        hydrate_options=_hydrate_pending_options,
        options_cls=AgentRuntimeOptions,
    )


def _mark_persisted_stream_if_needed(
    raw_stream: Iterator[str],
    persistence: StreamPersistenceConfig | None,
) -> Iterator[str]:
    if persistence is None:
        return raw_stream
    return PersistedSSEStream(
        raw_stream,
        prompt_control=getattr(raw_stream, "_researchos_prompt_control", None),
    )


def _persist_inline_stream_if_needed(
    raw_stream: Iterator[str],
    persistence: StreamPersistenceConfig | None,
    *,
    manage_lifecycle: bool = True,
) -> Iterator[str]:
    if persistence is None:
        return raw_stream
    upstream_control = getattr(raw_stream, "_researchos_prompt_control", None)
    prompt_control = (
        upstream_control
        if isinstance(upstream_control, PromptStreamControl)
        else PromptStreamControl()
    )
    if str(persistence.assistant_message_id or "").strip():
        prompt_control.assistant_message_id = str(persistence.assistant_message_id or "").strip()
    session_processor = SessionProcessor(
        session_id=persistence.session_id,
        parent_id=persistence.parent_id,
        assistant_meta=copy.deepcopy(persistence.assistant_meta)
        if isinstance(persistence.assistant_meta, dict)
        else None,
        assistant_message_id=persistence.assistant_message_id,
    )
    processor_manages_lifecycle = manage_lifecycle and not isinstance(
        upstream_control, PromptStreamControl
    )
    return PersistedSSEStream(
        session_processor.stream(
            raw_stream,
            manage_lifecycle=processor_manages_lifecycle,
            control=prompt_control,
            lifecycle_kind="inline",
            step_index=0,
        ),
        prompt_control=prompt_control,
    )


def _make_sse(event: str, data: dict) -> str:
    return sse_events.format_sse_event(event, data)


def _parse_sse_event(raw: str) -> tuple[str, dict[str, Any]] | None:
    return sse_events.parse_sse_event(raw)


_prompt_terminal_error = _processor_prompt_terminal_error


def _prompt_result_payload(session_id: str, message_id: str | None) -> dict[str, Any] | None:
    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        return None
    payload: dict[str, Any] = {"messageID": normalized_message_id}
    message = get_session_message_by_id(session_id, normalized_message_id)
    if isinstance(message, dict):
        payload["message"] = message
    return payload


def _session_loop_turn_state(session_id: str | None) -> dict[str, Any] | None:
    return get_session_turn_state(session_id)


def _get_max_tool_steps(reasoning_level: str | None = None) -> int:
    return _shared_get_max_tool_steps(reasoning_level)


def _resolve_max_tool_steps(reasoning_level: str | None = None) -> int:
    try:
        return _get_max_tool_steps(reasoning_level)
    except TypeError:
        return _get_max_tool_steps()


def _session_options(
    *,
    session_id: str | None = None,
    mode: str | None = None,
    workspace_path: str | None = None,
    workspace_server_id: str | None = None,
    reasoning_level: str | None = None,
    model_override: str | None = None,
    active_skill_ids: list[str] | None = None,
    mounted_paper_ids: list[str] | None = None,
    mounted_primary_paper_id: str | None = None,
) -> AgentRuntimeOptions:
    state = ensure_session(
        session_id,
        mode=mode,
        workspace_path=workspace_path,
        workspace_server_id=workspace_server_id,
    )
    return AgentRuntimeOptions(
        session_id=state.session_id,
        mode=normalize_mode(state.mode),
        workspace_path=state.workspace_path,
        workspace_server_id=state.workspace_server_id,
        reasoning_level=str(reasoning_level or "default").strip().lower() or "default",
        model_override=str(model_override or "").strip() or None,
        active_skill_ids=[
            str(item).strip() for item in (active_skill_ids or []) if str(item).strip()
        ],
        mounted_paper_ids=[
            str(item).strip() for item in (mounted_paper_ids or []) if str(item).strip()
        ],
        mounted_primary_paper_id=(str(mounted_primary_paper_id or "").strip() or None),
    )


def _session_prompt_runtime(
    session_id: str,
    request_message_id: str | None = None,
) -> tuple[AgentRuntimeOptions, dict[str, Any], dict[str, Any], str]:
    turn_state = _session_loop_turn_state(session_id) or {}
    turn_request_message_id = str(turn_state.get("request_message_id") or "").strip()
    turn_assistant_message_id = str(turn_state.get("assistant_message_id") or "").strip()
    normalized_request_message_id = str(request_message_id or "").strip()
    request_message: dict[str, Any] = {}
    request_info: dict[str, Any] = {}
    if normalized_request_message_id:
        request_message = get_session_message_by_id(session_id, normalized_request_message_id) or {}
        request_info = (
            request_message.get("info") if isinstance(request_message.get("info"), dict) else {}
        )
        explicit_role = str(request_info.get("role") or "").strip()
        explicit_message_id = str(request_info.get("id") or "").strip()
        explicit_assistant_matches = False
        if turn_assistant_message_id:
            turn_assistant = (
                turn_state.get("assistant_message")
                if isinstance(turn_state.get("assistant_message"), dict)
                else get_session_message_by_id(session_id, turn_assistant_message_id) or {}
            )
            turn_assistant_info = (
                turn_assistant.get("info") if isinstance(turn_assistant.get("info"), dict) else {}
            )
            explicit_assistant_matches = str(
                turn_assistant_info.get("parentID") or turn_assistant_info.get("parentId") or ""
            ).strip() == explicit_message_id and str(
                turn_assistant_info.get("finish") or ""
            ).strip() in {"", "tool-calls", "unknown"}
        if (
            explicit_role not in {"user"}
            or not explicit_message_id
            or (
                turn_request_message_id
                and turn_request_message_id != explicit_message_id
                and not explicit_assistant_matches
            )
        ):
            request_message = {}
            request_info = {}
            normalized_request_message_id = ""

    if not normalized_request_message_id:
        normalized_request_message_id = turn_request_message_id
        request_message = (
            turn_state.get("request_message")
            if isinstance(turn_state.get("request_message"), dict)
            else get_session_message_by_id(session_id, normalized_request_message_id) or {}
        )
        request_info = (
            request_message.get("info") if isinstance(request_message.get("info"), dict) else {}
        )

    if not normalized_request_message_id:
        raise RuntimeError("queued prompt callback is missing request cursor")
    if str(request_info.get("role") or "") not in {"", "user"}:
        raise RuntimeError("queued prompt callback request cursor does not point to a user message")

    session_record = get_session_record(session_id) or {}
    reasoning_level = str(request_info.get("variant") or "").strip().lower() or "default"
    request_active_skill_ids = (
        request_info.get("active_skill_ids")
        if isinstance(request_info.get("active_skill_ids"), list)
        else request_info.get("activeSkillIDs")
        if isinstance(request_info.get("activeSkillIDs"), list)
        else None
    )
    request_mounted_paper_ids = (
        request_info.get("mounted_paper_ids")
        if isinstance(request_info.get("mounted_paper_ids"), list)
        else request_info.get("mountedPaperIDs")
        if isinstance(request_info.get("mountedPaperIDs"), list)
        else None
    )
    active_skill_ids = [
        str(item).strip()
        for item in (request_active_skill_ids if isinstance(request_active_skill_ids, list) else [])
        if str(item).strip()
    ]
    mounted_paper_ids = [
        str(item).strip()
        for item in (
            request_mounted_paper_ids if isinstance(request_mounted_paper_ids, list) else []
        )
        if str(item).strip()
    ]
    options = _session_options(
        session_id=session_id,
        mode=str(session_record.get("mode") or "build"),
        workspace_path=(str(session_record.get("workspace_path") or "").strip() or None),
        workspace_server_id=(str(session_record.get("workspace_server_id") or "").strip() or None),
        reasoning_level=reasoning_level,
        active_skill_ids=active_skill_ids,
        mounted_paper_ids=mounted_paper_ids,
        mounted_primary_paper_id=(
            str(
                request_info.get("mounted_primary_paper_id")
                or request_info.get("mountedPrimaryPaperID")
                or ""
            ).strip()
            or None
        ),
    )
    return options, session_record, request_message, normalized_request_message_id


def _hydrate_pending_options(session_id: str) -> AgentRuntimeOptions:
    try:
        options, _session_record, _request_message, _request_message_id = _session_prompt_runtime(
            session_id
        )
        return options
    except Exception:
        session_record = get_session_record(session_id) or {}
        return _session_options(
            session_id=session_id,
            mode=str(session_record.get("mode") or "build"),
            workspace_path=(
                str(session_record.get("workspace_path") or "").strip()
                or str(session_record.get("directory") or "").strip()
                or None
            ),
            workspace_server_id=(
                str(session_record.get("workspace_server_id") or "").strip() or None
            ),
            reasoning_level="default",
            active_skill_ids=[],
            mounted_paper_ids=[],
            mounted_primary_paper_id=None,
        )


def _opencode_provider_prompt(options: AgentRuntimeOptions) -> str:
    identity = _resolve_current_model_identity(options)
    model_id = str(identity.get("modelID") or "").strip().lower()
    if "gpt-5" in model_id:
        return _load_reference_prompt("codex_header.txt")
    if "gpt-" in model_id or "o1" in model_id or "o3" in model_id:
        return _load_reference_prompt("beast.txt")
    if "gemini-" in model_id:
        return _load_reference_prompt("gemini.txt")
    if "claude" in model_id:
        return _load_reference_prompt("anthropic.txt")
    if "trinity" in model_id:
        return _load_reference_prompt("trinity.txt")
    return _load_reference_prompt("qwen.txt")


def _opencode_role_prompt(options: AgentRuntimeOptions) -> str:
    lines = [
        "Role:",
        "- You are ResearchOS, a research-paper assistant for literature retrieval, paper analysis, figure-grounded explanation, and local research workflows.",
        "- Use concise Simplified Chinese by default unless the user explicitly asks for another language.",
        "- In user-visible replies, sound like a dedicated research assistant rather than a generic coding agent.",
        "- Do not introduce yourself as OpenCode in user-facing text.",
    ]
    if options.mounted_paper_ids:
        lines.append(
            "- This session already has mounted local papers. Treat them as first-class context and avoid asking the user to restate basic paper metadata."
        )
    if str(options.workspace_path or "").strip():
        lines.append(
            "- If the task becomes code- or workspace-oriented, you may also act as an implementation agent and use workspace tools directly."
        )
    return "\n".join(lines)


def _opencode_environment_prompt(options: AgentRuntimeOptions) -> str:
    identity = _resolve_current_model_identity(options)
    workspace_root = str(options.workspace_path or "").strip()
    workspace_dir = workspace_root or str(Path.cwd())
    git_repo = "yes" if (Path(workspace_dir) / ".git").exists() else "no"
    today = datetime.now().astimezone().strftime("%a %b %d %Y")
    lines = [
        f"You are powered by the model named {identity['modelID'] or 'unknown-model'}. The exact model ID is {identity['providerID'] or 'unknown-provider'}/{identity['modelID'] or 'unknown-model'}",
        "Here is some useful information about the environment you are running in:",
        "<env>",
        f"  Working directory: {workspace_dir}",
        f"  Workspace root folder: {workspace_root or workspace_dir}",
        f"  Is directory a git repo: {git_repo}",
        f"  Platform: {sys.platform}",
        f"  Today's date: {today}",
        "</env>",
        "<directories>",
        "</directories>",
    ]
    return "\n".join(lines)


def _active_skill_items(options: AgentRuntimeOptions) -> list[dict[str, Any]]:
    active_ids = resolve_research_skill_ids(
        options.active_skill_ids,
        options.mounted_paper_ids,
    )
    if not active_ids:
        return []
    skill_by_id = {
        str(item.get("id") or "").strip(): item
        for item in list_local_skills()
        if str(item.get("id") or "").strip()
    }
    return [skill_by_id[skill_id] for skill_id in active_ids if skill_id in skill_by_id]


def _opencode_skills_prompt(options: AgentRuntimeOptions) -> str:
    session_record = get_session_record(options.session_id) or {}
    policy = _runtime_exec_policy_override()
    disabled = disabled_tools_from_rules(
        ["skill"],
        effective_ruleset(session_record, policy)
        if policy is not None
        else effective_ruleset(session_record),
    )
    if "skill" in disabled:
        return ""
    skills = _active_skill_items(options)
    if not skills:
        return ""
    lines = [
        "Skills are optional workflow templates. Core ResearchOS paper features are available as MCP tools even when no skill is enabled.",
        "Enabled skills for this turn:",
        "Only use the skill tool with one of the skills listed below.",
    ]
    for item in skills[:20]:
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        if not name:
            continue
        lines.append(f"- {name}: {description or 'No description provided.'}")
    return "\n".join(lines)


def _session_record_for_tool_binding(options: AgentRuntimeOptions) -> dict[str, Any]:
    return get_session_record(options.session_id) or {
        "id": options.session_id,
        "projectID": "global",
        "directory": options.workspace_path or "",
        "workspace_path": options.workspace_path,
        "workspace_server_id": options.workspace_server_id,
        "permission": None,
    }


def _disabled_tools_for_turn(session_record: dict[str, Any]) -> set[str]:
    policy = _runtime_exec_policy_override()
    return set(
        disabled_tools_from_rules(
            sorted(tool_registry_names()),
            effective_ruleset(session_record, policy)
            if policy is not None
            else effective_ruleset(session_record),
        )
    )


def _runtime_exec_policy_override() -> dict[str, Any] | None:
    if get_assistant_exec_policy is _DEFAULT_GET_ASSISTANT_EXEC_POLICY:
        return None
    policy = get_assistant_exec_policy()
    override = dict(policy or {})
    if str(override.get("approval_mode") or "").strip() == "off":
        # Runtime-level test overrides historically patched approval_mode only.
        # Preserve that shorthand without weakening the persisted global defaults.
        override.setdefault("workspace_access", "read_write")
        override.setdefault("command_execution", "full")
    return override


def _available_turn_function_tools(
    options: AgentRuntimeOptions,
    *,
    user_tools: dict[str, bool] | None = None,
) -> list[str]:
    tool_entries = _build_turn_tools(
        LLMClient(),
        options,
        disabled_tools=_disabled_tools_for_turn(_session_record_for_tool_binding(options)),
        user_tools=user_tools,
    )
    names: list[str] = []
    for entry in tool_entries:
        if str(entry.get("type") or "") != "function":
            continue
        function = entry.get("function") if isinstance(entry.get("function"), dict) else {}
        name = str(function.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


_REPO_LOOKUP_FILE_HINT_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+")
_ACADEMIC_LOOKUP_RE = re.compile(
    r"\b(arxiv|paper|papers|citation|citations|related work|survey|sota|benchmark|baseline|openalex|conference|journal|ccf)\b",
    re.IGNORECASE,
)


def _looks_like_repo_lookup_request(user_request: str) -> bool:
    normalized = " ".join(str(user_request or "").strip().split()).lower()
    if not normalized:
        return False
    shell_markers = (
        "git status",
        "git diff",
        "运行命令",
        "执行命令",
        "bash",
        "shell",
        "终端",
        "pytest",
        "npm ",
        "pnpm ",
        "yarn ",
        "uv run",
    )
    if any(marker in normalized for marker in shell_markers):
        return False

    repo_markers = (
        "不要凭记忆回答",
        "读取",
        "源码",
        "代码",
        "定义",
        "调用点",
        "接口",
        "route",
        "session/",
        "stream_chat",
        "symbol",
        "file",
        "read ",
        "grep ",
    )
    return bool(_REPO_LOOKUP_FILE_HINT_RE.search(user_request)) or any(
        marker in normalized for marker in repo_markers
    )


def _opencode_repo_lookup_prompt(user_request: str) -> str:
    if not _looks_like_repo_lookup_request(user_request):
        return ""
    return "\n".join(
        [
            "Repository lookup strategy for this turn:",
            "- If the request already names concrete file paths, read those files directly before using any other tool.",
            "- Do not narrate your internal thinking or say that you are about to search; either call the tool directly or answer.",
            "- If the task is to find a symbol, definition, or call site, use grep in source directories first and only widen the scope if source hits are insufficient.",
            "- Prefer production/source files over tests, docs, plans, caches, and temp artifacts. Only read tests if source files still leave the behavior uncertain.",
            "- If grep already returns exact file paths and line numbers for the definition/call site, read only those matched files and stop widening the search.",
            "- Do not use glob after grep already returned the exact files you need for a symbol lookup.",
            "- Do not use bash just to print file excerpts or line numbers; use grep results and read with offset/limit for local context.",
            "- Do not use bash for ordinary repository inspection when read, glob, or grep can answer the question.",
            "- Once you have enough direct file evidence to answer, stop exploring and answer immediately.",
        ]
    )


def _looks_like_academic_lookup_request(user_request: str) -> bool:
    normalized = " ".join(str(user_request or "").strip().split())
    if not normalized:
        return False
    lowered = normalized.lower()
    chinese_markers = (
        "论文",
        "文献",
        "arxiv",
        "引用",
        "综述",
        "相关工作",
        "知识库",
        "相似论文",
        "时间线",
        "研究任务",
        "方法思路",
        "训练信号",
        "数据集",
        "指标",
        "局限",
        "研究空白",
        "会议",
        "期刊",
        "ccf",
        "grounding",
        "multimodal",
        "visual",
    )
    return bool(_ACADEMIC_LOOKUP_RE.search(normalized)) or any(
        marker in lowered for marker in chinese_markers
    )


def _looks_like_figure_grounded_paper_request(user_request: str) -> bool:
    normalized = " ".join(str(user_request or "").strip().split())
    if not normalized:
        return False
    lowered = normalized.lower()
    markers = (
        "图",
        "图片",
        "图表",
        "表格",
        "配图",
        "框架图",
        "结构图",
        "流程图",
        "架构",
        "结构",
        "编码器",
        "解码器",
        "模块",
        "视觉编码器",
        "vision encoder",
        "visual encoder",
        "encoder",
        "decoder",
        "architecture",
        "framework",
        "diagram",
        "figure",
        "table",
        "pipeline",
    )
    return any(marker in lowered for marker in markers) or bool(
        re.search(r"表\s*\d+", normalized, re.IGNORECASE)
    )


def _looks_like_explicit_figure_or_table_request(user_request: str) -> bool:
    normalized = " ".join(str(user_request or "").strip().split())
    if not normalized:
        return False
    lowered = normalized.lower()
    explicit_markers = (
        "图",
        "图片",
        "图表",
        "表格",
        "配图",
        "框架图",
        "结构图",
        "流程图",
        "diagram",
        "figure",
        "table",
    )
    return any(marker in lowered for marker in explicit_markers) or bool(
        re.search(r"表\s*\d+", normalized, re.IGNORECASE)
    )


def _looks_like_mounted_paper_request(user_request: str) -> bool:
    normalized = " ".join(str(user_request or "").strip().split())
    if not normalized:
        return False
    lowered = normalized.lower()
    markers = (
        "这篇",
        "该论文",
        "导入的论文",
        "挂载",
        "已导入",
        "当前论文",
        "粗读",
        "精读",
        "推理链",
        "三轮分析",
        "方法",
        "模块",
        "训练",
        "实验",
        "消融",
        "基线",
        "数据集",
        "指标",
        "结果",
        "公式",
        "变量",
        "符号",
        "章节",
        "encoder",
        "decoder",
        "architecture",
        "framework",
        "diagram",
        "figure",
        "table",
    )
    return (
        _looks_like_academic_lookup_request(user_request)
        or _looks_like_explicit_figure_or_table_request(user_request)
        or any(marker in lowered for marker in markers)
    )


def _looks_like_precise_paper_evidence_request(user_request: str) -> bool:
    normalized = " ".join(str(user_request or "").strip().split())
    if not normalized:
        return False
    lowered = normalized.lower()
    markers = (
        "公式",
        "变量",
        "符号",
        "定义",
        "原文",
        "原句",
        "逐字",
        "表格",
        "table",
        "figure",
        "数值",
        "指标",
        "得分",
        "准确率",
        "精度",
        "召回",
        "f1",
        "auc",
        "超参",
        "batch size",
        "learning rate",
        "lr",
        "epoch",
        "参数量",
    )
    return any(marker in lowered for marker in markers) or bool(
        re.search(r"表\s*\d+", normalized, re.IGNORECASE)
    )


def _classify_mounted_paper_request(user_request: str) -> str:
    normalized = " ".join(str(user_request or "").strip().split())
    if not normalized:
        return "general"
    lowered = normalized.lower()

    if any(marker in lowered for marker in ("公式", "变量", "符号", "latex", "equation", "proof")):
        return "formula"
    if _looks_like_explicit_figure_or_table_request(user_request):
        return "figure"
    if any(
        marker in lowered
        for marker in ("对比", "比较", "区别", "共同点", "差异", "vs", "versus", "compare")
    ):
        return "comparison"
    if any(
        marker in lowered
        for marker in (
            "实验",
            "消融",
            "基线",
            "结果",
            "指标",
            "数据集",
            "超参",
            "benchmark",
            "ablation",
        )
    ):
        return "experiment"
    if any(
        marker in lowered
        for marker in ("方法", "模块", "结构", "训练", "流程", "实现", "架构", "原理", "algorithm")
    ):
        return "method"
    if any(
        marker in lowered
        for marker in (
            "讲什么",
            "概览",
            "速览",
            "值得读",
            "贡献",
            "创新点",
            "summary",
            "overview",
            "skim",
        )
    ):
        return "overview"
    return "general"


def _serialize_recovered_tool_context_data(
    value: Any,
    *,
    max_len: int = 1200,
) -> str:
    return _shared_serialize_tool_context_data(value, max_len=max_len)


def _format_orphan_tool_message_context(message: dict[str, Any]) -> str:
    return _shared_format_orphan_tool_message_context(
        message,
        stringify_message_content=_stringify_message_content,
    )


def _opencode_mounted_paper_turn_prompt(
    options: AgentRuntimeOptions,
    user_request: str,
    *,
    user_tools: dict[str, bool] | None = None,
) -> str:
    if not options.mounted_paper_ids or not _looks_like_mounted_paper_request(user_request):
        return ""
    tool_names = set(_available_turn_function_tools(options, user_tools=user_tools))
    intent = _classify_mounted_paper_request(user_request)
    wants_precise_evidence = _looks_like_precise_paper_evidence_request(user_request)
    lines = [
        "Mounted paper turn guidance:",
        "- This session already has imported local papers. Treat them as the first evidence source before library-wide or external search.",
        "- Do not fan out across every imported paper. Inspect only the 1-3 papers needed for this turn.",
    ]
    if "get_paper_detail" in tool_names:
        lines.append(
            "- Inspect get_paper_detail first so you can see which assets already exist for the imported paper before deciding on heavier tools."
        )
    if wants_precise_evidence:
        lines.append(
            "- This turn needs precise evidence. Prefer original Markdown/PDF/figure/table evidence over summary-only answers."
        )

    if intent == "overview":
        if "skim_paper" in tool_names:
            lines.append(
                "- For overview, contributions, novelty, and triage questions, prefer skim_paper first."
            )
        if "deep_read_paper" in tool_names:
            lines.append(
                "- Only escalate to deep_read_paper if skim_paper or saved analysis is insufficient for the user's requested depth."
            )
    elif intent == "method":
        if "deep_read_paper" in tool_names:
            lines.append(
                "- For method, module, training flow, or implementation questions, prefer deep_read_paper first."
            )
        if "get_paper_analysis" in tool_names or "analyze_paper_rounds" in tool_names:
            lines.append(
                "- Use three-round analysis as a supplement for strengths, weaknesses, and high-level judgment after method details are clear."
            )
    elif intent == "experiment":
        if "get_paper_analysis" in tool_names or "analyze_paper_rounds" in tool_names:
            lines.append(
                "- For experiment interpretation, ablation meaning, evidence sufficiency, strengths, and weaknesses, prefer get_paper_analysis / analyze_paper_rounds first."
            )
        if "deep_read_paper" in tool_names:
            lines.append(
                "- Use deep_read_paper for experiment setup, training protocol, and implementation details that the three-round analysis does not fully cover."
            )
        if "analyze_figures" in tool_names:
            lines.append(
                "- If the user asks for exact table values, ablation rows, plotted trends, or metric numbers, verify them with analyze_figures or the original paper content instead of relying only on summaries."
            )
    elif intent == "figure":
        lines.append("- This turn is figure-grounded on an already mounted local paper.")
        if "paper_figures" in tool_names:
            lines.append(
                "- If the user only wants to view already extracted pictures/figures/tables, call paper_figures; do not call analyze_paper_rounds."
            )
        if "analyze_figures" in tool_names:
            lines.append(
                "- If the user asks you to explain a diagram or original figure interaction, call analyze_figures before answering unless get_paper_detail already shows enough original figure evidence."
            )
            lines.append(
                "- Use analyze_figures only when existing figure cards are missing or the user asks to extract/analyze figure content."
            )
            lines.append(
                "- Use analyze_figures as the primary evidence source for diagram and table interpretation, and stop once the needed figure is found."
            )
        if "get_paper_analysis" in tool_names or "analyze_paper_rounds" in tool_names:
            lines.append(
                "- Do not call get_paper_analysis / analyze_paper_rounds just to recover figure refs or restate cached notes when the user only wants a figure-grounded explanation."
            )
    elif intent == "formula":
        lines.append(
            "- For formulas, variables, symbols, and equation explanations, verify against original Markdown/PDF snippets before answering."
        )
        if "deep_read_paper" in tool_names:
            lines.append(
                "- Use deep_read_paper only as supplemental explanation after the formula symbols and definitions are grounded in the source text."
            )
    elif intent == "comparison":
        lines.append(
            "- For multi-paper comparison, start with lightweight inspection of each imported paper and only deepen the one or two papers that matter most to the comparison."
        )
        if "get_paper_analysis" in tool_names:
            lines.append(
                "- Reuse existing three-round analysis before launching new heavy analysis jobs for every imported paper."
            )
    else:
        if "get_paper_analysis" in tool_names or "analyze_paper_rounds" in tool_names:
            lines.append(
                "- Use existing three-round analysis for interpretive questions before launching heavier re-analysis."
            )
        if "deep_read_paper" in tool_names:
            lines.append(
                "- Use deep_read_paper when the answer depends on concrete method or implementation details."
            )

    if "paper_figures" in tool_names or "analyze_figures" in tool_names:
        lines.append(
            "- When a dedicated figure tool output includes figure_refs with image_url, embed the most relevant original paper figure once and then explain it."
        )
        lines.append("- Do not output raw figure_ref IDs alone when image_url is available.")
    return "\n".join(lines)


def _opencode_research_lookup_prompt(
    options: AgentRuntimeOptions,
    user_request: str,
    *,
    user_tools: dict[str, bool] | None = None,
) -> str:
    if not _looks_like_academic_lookup_request(user_request):
        return ""
    tool_names = set(_available_turn_function_tools(options, user_tools=user_tools))
    lines = [
        "Academic lookup strategy for this turn:",
        "- Prefer built-in research tools over generic web search when the user is asking about papers, literature, citations, or research trends.",
    ]
    if options.mounted_paper_ids:
        lines.append(
            "- This session already has imported papers. If the user's question can be answered from them, inspect those papers before using search_papers or external literature search."
        )
    if "graph_rag_query" in tool_names:
        lines.append(
            "- Mandatory routing: for multi-paper comparison questions, method relationship questions, and questions mentioning two or more paper/model names, call graph_rag_query first. Do not start with search_papers/search_literature/websearch for these cases."
        )
        lines.append(
            "- For any request that says local library / 本地论文库 / 当前论文库 / 库内, call graph_rag_query before external search tools."
        )
        lines.append(
            "- For trend overviews, multi-paper comparisons, method lineage, dataset/metric/limitation summaries, citation/method context, and research-gap questions, call graph_rag_query first with the full user question as query."
        )
        lines.append(
            "- If graph_rag_query returns useful entities, relations, or papers, answer from that evidence pack and avoid search_literature/websearch unless the user explicitly asks for external sources."
        )
    if "search_papers" in tool_names:
        lines.append(
            "- Use search_papers first for papers already in the local library. It returns a compact candidate list; call get_paper_detail only for the few papers you will cite or compare."
        )
        lines.append(
            "- If search_papers returns 0 for a broad topic, Chinese query, or translated domain phrase, do not conclude the local library has no relevant papers. Continue with graph_rag_query when available, or use search_literature/search_arxiv for external discovery."
        )
    if "graph_rag_query" in tool_names:
        lines.append(
            "- Use graph_rag_query for research trends, method relationships, dataset/metric/limitation questions, citation/method lineage, research gaps, and any question that needs a structured evidence pack across local papers."
        )
        lines.append(
            "- If graph_rag_query reports an empty or stale Research KG and build_research_kg is available, build or refresh a small batch before answering instead of falling back directly to broad web search."
        )
    if "research_kg_status" in tool_names:
        lines.append(
            "- Use research_kg_status when you need to check whether the local GraphRAG knowledge graph has enough coverage."
        )
    if "get_paper_detail" in tool_names:
        lines.append(
            "- Use get_paper_detail after local search to inspect title, abstract, venue, saved analysis metadata, and any already extracted figures for a paper."
        )
    if "get_paper_analysis" in tool_names:
        lines.append(
            "- Use get_paper_analysis when the user asks for the existing three-round analysis or structured notes of a paper."
        )
    if "get_similar_papers" in tool_names:
        lines.append(
            "- Use get_similar_papers to expand from a known local paper into nearby related work already embedded in the library."
        )
    if "get_citation_tree" in tool_names:
        lines.append(
            "- Use get_citation_tree when the user asks for upstream/downstream references or a citation structure around one paper."
        )
    if "get_timeline" in tool_names:
        lines.append(
            "- Use get_timeline for milestone evolution, historical overview, and trend-by-year questions."
        )
    if "search_literature" in tool_names:
        lines.append(
            "- Use search_literature for external paper discovery across arXiv, conferences, and journals, especially for venue-filtered or CCF-A requests."
        )
        lines.append(
            "- For broad discovery or comparison, prefer one focused local search and one focused external search before answering; avoid repeated near-duplicate searches unless results are clearly off-target."
        )
        if "graph_rag_query" in tool_names:
            lines.append(
                "- Do not use search_literature before graph_rag_query when the user asks about the local library, already imported papers, or named papers likely present in the library."
            )
    if "preview_external_paper_head" in tool_names:
        lines.append(
            "- If an external arXiv paper is not imported yet and the user wants a quick triage pass, use preview_external_paper_head to inspect abstract metadata and section headings before ingesting it."
        )
    if "preview_external_paper_section" in tool_names:
        lines.append(
            "- Use preview_external_paper_section for lightweight external section reading such as Introduction, Method, or Experiments when the user does not need a full local ingest yet."
        )
    if "ingest_external_literature" in tool_names:
        lines.append(
            "- After search_literature finds useful papers, use ingest_external_literature to import selected results into the local paper library when the user wants them saved."
        )
    if "analyze_paper_rounds" in tool_names:
        lines.append(
            "- Use analyze_paper_rounds when the user wants the local paper library to generate the coarse-to-fine three-round analysis for a paper; it is not a figure viewing tool."
        )
    if "paper_figures" in tool_names:
        lines.append(
            "- Use paper_figures when the user asks to view already extracted pictures, figures, tables, or original image cards from a local paper."
        )
    if "skim_paper" in tool_names or "deep_read_paper" in tool_names:
        lines.append(
            "- Use skim_paper / deep_read_paper for lightweight or deeper single-paper reading passes when the user wants staged analysis instead of a full three-round run."
        )
    if "generate_wiki" in tool_names:
        lines.append(
            "- Use generate_wiki for topic surveys or single-paper structured overviews when the user asks for a longer synthesized write-up."
        )
    if "identify_research_gaps" in tool_names:
        lines.append(
            "- Use identify_research_gaps when the user explicitly wants open problems, research gaps, or future directions. If the user instead wants a full idea-discovery run or concrete research ideas, do not stop at a gap list."
        )
    if "research_wiki_init" in tool_names:
        lines.append(
            "- Use research_wiki_init when the user wants to initialize or refresh the project-level research wiki from the current project papers and ideas."
        )
    if "research_wiki_query" in tool_names:
        lines.append(
            "- Use research_wiki_query when you need a compact project memory pack before proposing ideas, planning experiments, or continuing an existing project thread."
        )
    if "research_wiki_stats" in tool_names:
        lines.append(
            "- Use research_wiki_stats to inspect the current project memory coverage before deciding whether more structure or curation is needed."
        )
    if "research_wiki_update_node" in tool_names:
        lines.append(
            "- Use research_wiki_update_node to persist important project facts as structured wiki nodes instead of leaving them only in transient chat text."
        )
    if "analyze_figures" in tool_names:
        lines.append(
            "- Use analyze_figures when the user wants figure/table extraction or chart-centric interpretation from a local paper; prefer paper_figures for view-only requests."
        )
    if "get_paper_detail" in tool_names and (
        "get_paper_analysis" in tool_names or "analyze_paper_rounds" in tool_names
    ):
        lines.append(
            "- When the user asks to analyze an already imported local paper, inspect get_paper_detail first so the UI can surface the mounted paper metadata and available figures alongside the answer."
        )
    if "paper_figures" in tool_names and (
        "get_paper_detail" in tool_names or "get_paper_analysis" in tool_names
    ):
        lines.append(
            "- If already extracted figure cards are enough for the request, call paper_figures instead of a three-round analysis tool."
        )
    if "analyze_figures" in tool_names and (
        "get_paper_detail" in tool_names or "get_paper_analysis" in tool_names
    ):
        lines.append(
            "- If a mounted paper has PDF support but no figure cards are available yet and figures matter for the request, run analyze_figures before giving the final paper analysis."
        )
    if "graph_rag_query" in tool_names and (
        "analyze_figures" in tool_names or "deep_read_paper" in tool_names
    ):
        lines.append(
            "- For ordinary method summaries and evidence overviews across local papers, graph_rag_query plus saved analysis is enough; do not start analyze_figures or deep_read_paper unless the user explicitly asks for figures, tables, exact visual evidence, or a new deep read."
        )
    if "list_topics" in tool_names or "manage_subscription" in tool_names:
        lines.append(
            "- Use list_topics / manage_subscription for the user's local folders, subscriptions, and recurring literature tracking workflows."
        )
    if _active_skill_items(options) and (
        "list_local_skills" in tool_names
        or "read_local_skill" in tool_names
        or "skill" in tool_names
    ):
        lines.append(
            "- If the user asks for a project-specific workflow and there is no dedicated research tool, inspect local skills before falling back to generic web search."
        )
    if "search_arxiv" in tool_names:
        lines.append("- Use search_arxiv for external paper discovery and arXiv candidate lookup.")
    lines.extend(
        [
            "- Use generic websearch/search_web only for non-paper web sources such as project pages, blogs, news, or documentation.",
            "- Do not start with broad web search if local library or arXiv tools can answer the request directly.",
        ]
    )
    return "\n".join(lines)


def _opencode_tool_binding_prompt(
    options: AgentRuntimeOptions,
    *,
    user_tools: dict[str, bool] | None = None,
) -> str:
    tool_names = _available_turn_function_tools(options, user_tools=user_tools)
    if not tool_names:
        return (
            "Tool binding: no function tools are exposed in this turn. "
            "If generic provider instructions mention any tools, treat them as unavailable."
        )

    lines = [
        "Tool binding: only call tools that are actually exposed in this turn.",
        "If generic provider instructions mention a tool that is not listed below, treat it as unavailable.",
        "Do not expose chain-of-thought or self-talk in user-visible text. When a tool is needed, call it directly.",
        f"Available function tools this turn: {', '.join(tool_names)}.",
    ]
    if "apply_patch" in tool_names and "edit" not in tool_names and "write" not in tool_names:
        lines.append("For file edits, use apply_patch. Do not call edit or write in this turn.")
    else:
        edit_tools = [
            name for name in ("apply_patch", "edit", "write", "multiedit") if name in tool_names
        ]
        if edit_tools:
            lines.append(
                f"For file changes, only use the exposed edit tools: {', '.join(edit_tools)}."
            )
    if "bash" not in tool_names:
        lines.append("Bash is not exposed in this turn.")
    if options.mode == "plan":
        lines.append("Plan mode stays read-only except for the plan file tools listed above.")
        control_tools = [name for name in ("question", "plan_exit") if name in tool_names]
        if control_tools:
            lines.append(f"Plan-mode control tools in this turn: {', '.join(control_tools)}.")
        if "bash" in tool_names:
            lines.append("In plan mode, bash may only be used for read-only inspection commands.")
        if "task" in tool_names:
            lines.append(
                "In plan mode, use task with subagent_type=explore for investigation and subagent_type=general for approach validation."
            )
    return "\n".join(lines)


def _opencode_reasoning_prompt(options: AgentRuntimeOptions) -> str:
    level = str(options.reasoning_level or "default").strip().lower() or "default"
    max_steps = _resolve_max_tool_steps(level)
    return _shared_build_reasoning_profile_prompt(
        level,
        max_steps=max_steps,
    )


def _build_turn_context_sections(
    options: AgentRuntimeOptions,
    *,
    user_tools: dict[str, bool] | None = None,
    latest_user_request: str = "",
) -> list[str]:
    sections: list[str] = []
    for section in (
        _opencode_role_prompt(options),
        _opencode_environment_prompt(options),
        _opencode_skills_prompt(options),
        _opencode_tool_binding_prompt(options, user_tools=user_tools),
        _opencode_reasoning_prompt(options),
        _opencode_repo_lookup_prompt(latest_user_request),
        build_mounted_papers_prompt(
            options.mounted_paper_ids,
            options.mounted_primary_paper_id,
        ),
        _opencode_mounted_paper_turn_prompt(
            options,
            latest_user_request,
            user_tools=user_tools,
        ),
        _opencode_research_lookup_prompt(
            options,
            latest_user_request,
            user_tools=user_tools,
        ),
    ):
        content = str(section or "").strip()
        if content:
            sections.append(content)
    return sections


def _build_system_prompt_messages(
    options: AgentRuntimeOptions,
    *,
    user_tools: dict[str, bool] | None = None,
    latest_user_request: str = "",
) -> list[str]:
    return [
        _opencode_provider_prompt(options),
        *_build_turn_context_sections(
            options,
            user_tools=user_tools,
            latest_user_request=latest_user_request,
        ),
    ]


def _build_system_prompt(
    options: AgentRuntimeOptions,
    *,
    user_tools: dict[str, bool] | None = None,
    latest_user_request: str = "",
) -> str:
    return "\n\n".join(
        _build_system_prompt_messages(
            options,
            user_tools=user_tools,
            latest_user_request=latest_user_request,
        )
    )


def _extract_latest_user_system(messages: list[dict]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "") != "user":
            continue
        system = str(message.get("system") or "").strip()
        if system:
            return system
    return ""


_OUTPUT_LIMIT_PATTERNS = (
    re.compile(
        r"(?:不超过|不多于|最多|至多|控制在|限制在|少于|小于|<=|≤)\s*(\d+)\s*(字|个字|字符|字数|词|单词|行|words?|chars?|characters?|lines?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:within|under|less than|no more than|max(?:imum)? of)\s*(\d+)\s*(words?|chars?|characters?|lines?)",
        re.IGNORECASE,
    ),
)


def _build_output_constraint_system_prompt(user_text: str) -> str:
    constraint = _extract_output_constraint(user_text)
    if constraint is None:
        return ""
    return (
        f"硬约束：最终回答必须不超过{constraint.label}。"
        "直接输出答案，不要列表、标题、markdown 强调或额外解释；输出前先检查长度。"
    )


def _extract_output_constraint(user_text: str | None) -> OutputConstraint | None:
    normalized = " ".join(str(user_text or "").strip().split())
    if not normalized:
        return None
    for pattern in _OUTPUT_LIMIT_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        value = max(int(match.group(1) or 0), 0)
        unit = str(match.group(2) or "").strip().lower()
        if value <= 0:
            return None
        if unit in {"行", "line", "lines"}:
            return OutputConstraint(limit=value, unit="lines", label=f"{value}行")
        elif unit in {"词", "单词", "word", "words"}:
            return OutputConstraint(limit=value, unit="words", label=f"{value}词")
        elif unit in {"字符", "char", "chars", "character", "characters"}:
            return OutputConstraint(limit=value, unit="chars", label=f"{value}字符")
        return OutputConstraint(limit=value, unit="chars", label=f"{value}字")
    return None


def _content_exceeds_output_constraint(content: str, constraint: OutputConstraint | None) -> bool:
    if constraint is None:
        return False
    text = str(content or "").strip()
    if constraint.unit == "lines":
        return len(text.splitlines() or [""]) > constraint.limit
    if constraint.unit == "words":
        return len(text.split()) > constraint.limit
    return len(text) > constraint.limit


def _trim_to_output_constraint(content: str, constraint: OutputConstraint | None) -> str:
    text = str(content or "").strip()
    if constraint is None or not text:
        return text
    if constraint.unit == "lines":
        return "\n".join(text.splitlines()[: constraint.limit]).strip()
    if constraint.unit == "words":
        return " ".join(text.split()[: constraint.limit]).strip()
    if len(text) <= constraint.limit:
        return text
    sentence_break = max(
        text.rfind(mark, 0, constraint.limit) for mark in ("。", "！", "？", "；", ";")
    )
    if sentence_break >= max(constraint.limit // 2, constraint.limit - 24):
        return text[: sentence_break + 1].strip()
    clause_break = max(
        text.rfind(mark, 0, constraint.limit) for mark in ("，", ",", "、", "：", ":", " ")
    )
    if clause_break >= max(constraint.limit // 2, constraint.limit - 24):
        return text[:clause_break].rstrip("，,、；;：: ")
    return text[: constraint.limit].rstrip("，,、；;：: ")


def _repair_output_constraint_text(
    llm: Any,
    content: str,
    constraint: OutputConstraint | None,
    options: AgentRuntimeOptions,
) -> str:
    text = str(content or "").strip()
    if not text or constraint is None or not _content_exceeds_output_constraint(text, constraint):
        return text
    repair_messages = [
        {
            "role": "system",
            "content": (
                f"你负责压缩回答。必须保留核心含义，并严格控制在{constraint.label}以内。"
                "只输出压缩后的最终答案，不要列表、标题、markdown、引号或解释。"
            ),
        },
        {
            "role": "user",
            "content": text,
        },
    ]
    repaired_parts: list[str] = []
    try:
        for event in llm.chat_stream(
            repair_messages,
            tools=None,
            variant_override="low",
            session_cache_key=f"{options.session_id}:constraint-repair"
            if options.session_id
            else None,
        ):
            if event.type == "text_delta" and event.content:
                repaired_parts.append(event.content)
    except Exception:
        logger.debug("Output-constraint repair failed", exc_info=True)
        repaired_parts = []
    repaired = "".join(repaired_parts).strip() or text
    if _content_exceeds_output_constraint(repaired, constraint):
        repaired = _trim_to_output_constraint(repaired, constraint)
    return repaired


def _extract_latest_user_output_constraint(messages: list[dict]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "") != "user":
            continue
        prompt = _build_output_constraint_system_prompt(
            _extract_user_text_content(message.get("content"))
        )
        if prompt:
            return prompt
    return ""


def _stringify_message_content(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    chunks.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip().lower()
            if item_type == "text":
                text = str(item.get("text") or item.get("content") or "")
                if text:
                    chunks.append(text)
                continue
            if item_type == "file":
                mime = str(item.get("mime") or "file").strip() or "file"
                filename = str(item.get("filename") or "file").strip() or "file"
                chunks.append(f"[Attached {mime}: {filename}]")
        return "\n\n".join(chunk for chunk in chunks if chunk.strip()).strip()
    if isinstance(content, dict):
        text = str(content.get("text") or content.get("content") or "")
        if text:
            return text
        try:
            return json.dumps(content, ensure_ascii=False)
        except TypeError:
            return str(content)
    return str(content)


def _extract_user_text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").strip().lower() != "text":
                continue
            text = str(item.get("text") or item.get("content") or "")
            if text:
                chunks.append(text)
        if chunks:
            return "\n\n".join(chunks).strip()
    return _stringify_message_content(content)


def _extract_latest_user_tools(messages: list[dict]) -> dict[str, bool] | None:
    for message in reversed(messages):
        if str(message.get("role") or "") != "user":
            continue
        tools = message.get("tools")
        if isinstance(tools, dict):
            normalized: dict[str, bool] = {}
            for key, value in tools.items():
                name = str(key or "").strip()
                if name:
                    normalized[name] = bool(value)
            return normalized
    return None


def _collect_latest_user_prompt_shaping(
    messages: list[dict],
    *,
    fallback_request: str = "",
) -> LatestUserPromptShaping:
    latest_user_request = (
        _extract_latest_user_request(messages).strip() or str(fallback_request or "").strip()
    )
    return LatestUserPromptShaping(
        tools=_extract_latest_user_tools(messages),
        request=latest_user_request,
        system=_extract_latest_user_system(messages),
        output_constraint=_extract_latest_user_output_constraint(messages),
    )


def _append_message_text_content(content: Any, extra_text: str) -> Any:
    reminder = str(extra_text or "").strip()
    if not reminder:
        return copy.deepcopy(content)
    if isinstance(content, list):
        updated = copy.deepcopy(content)
        updated.append({"type": "text", "text": reminder})
        return updated
    if isinstance(content, dict):
        return [copy.deepcopy(content), {"type": "text", "text": reminder}]
    base = str(content or "").strip()
    if not base:
        return reminder
    return f"{base}\n\n{reminder}"


def _session_has_plan_assistant(session_id: str | None) -> bool:
    for message in reversed(list_session_messages(session_id, limit=5000)):
        info = message.get("info") if isinstance(message.get("info"), dict) else {}
        if str(info.get("role") or "").strip() != "assistant":
            continue
        mode = str(info.get("mode") or info.get("agent") or "").strip().lower()
        if mode:
            return mode == "plan"
    return False


def _prepare_loop_messages(
    messages: list[dict],
    options: AgentRuntimeOptions,
    *,
    current_step: int,
    max_steps: int,
) -> list[dict]:
    prepared = copy.deepcopy(messages)

    latest_user_index: int | None = None
    for index in range(len(prepared) - 1, -1, -1):
        if str(prepared[index].get("role") or "") == "user":
            latest_user_index = index
            break

    if latest_user_index is not None:
        reminder = ""
        if options.mode == "plan":
            reminder = build_plan_mode_reminder(
                get_session_record(options.session_id)
                or {
                    "id": options.session_id,
                    "slug": options.session_id,
                    "directory": options.workspace_path,
                    "workspace_path": options.workspace_path,
                    "workspace_server_id": options.workspace_server_id,
                    "mode": options.mode,
                    "time": {},
                }
            )
        elif (
            current_step == 0
            and options.mode == "build"
            and _session_has_plan_assistant(options.session_id)
        ):
            reminder = _load_reference_prompt("build-switch.txt")
        if reminder:
            prepared[latest_user_index]["content"] = _append_message_text_content(
                prepared[latest_user_index].get("content"),
                reminder,
            )

    if _shared_should_inject_max_steps_prompt(current_step, max_steps):
        max_steps_prompt = _load_reference_prompt("max-steps.txt")
        if max_steps_prompt:
            prepared.append({"role": "assistant", "content": max_steps_prompt})

    return prepared


def _normalize_messages(messages: list[dict], options: AgentRuntimeOptions) -> list[dict]:
    prompt_shaping = _collect_latest_user_prompt_shaping(messages)
    normalized: list[dict] = [
        {"role": "system", "content": content}
        for content in _build_system_prompt_messages(
            options,
            user_tools=prompt_shaping.tools,
            latest_user_request=prompt_shaping.request,
        )
    ]
    if prompt_shaping.system:
        normalized.append({"role": "system", "content": prompt_shaping.system})
    if prompt_shaping.output_constraint:
        normalized.append({"role": "system", "content": prompt_shaping.output_constraint})
    seen_tool_call_ids: set[str] = set()
    for message in messages:
        role = str(message.get("role", "user") or "user")
        if role not in {"system", "user", "assistant", "tool"}:
            role = "user"

        if role == "assistant":
            raw_tool_calls = message.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                for raw_call in raw_tool_calls:
                    if not isinstance(raw_call, dict):
                        continue
                    call_id = str(raw_call.get("id") or "").strip()
                    if call_id:
                        seen_tool_call_ids.add(call_id)

        raw_content = message.get("content", "")
        content = (
            copy.deepcopy(raw_content)
            if isinstance(raw_content, (list, dict))
            else str(raw_content or "")
        )
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id", "") or "")
            if not tool_call_id or tool_call_id not in seen_tool_call_ids:
                normalized.append(
                    {
                        "role": "user",
                        "content": _format_orphan_tool_message_context(message),
                    }
                )
                continue
        payload = {"role": role, "content": content}
        if role == "user":
            tools = message.get("tools")
            if isinstance(tools, dict):
                payload["tools"] = copy.deepcopy(tools)
            system = str(message.get("system") or "").strip()
            if system:
                payload["system"] = system
            variant = str(message.get("variant") or "").strip()
            if variant:
                payload["variant"] = variant
        if role == "assistant":
            text_parts = message.get("text_parts")
            if isinstance(text_parts, list) and text_parts:
                payload["text_parts"] = copy.deepcopy(text_parts)
            reasoning_content = str(message.get("reasoning_content") or "").strip()
            if reasoning_content:
                payload["reasoning_content"] = reasoning_content
            reasoning_parts = message.get("reasoning_parts")
            if isinstance(reasoning_parts, list) and reasoning_parts:
                payload["reasoning_parts"] = copy.deepcopy(reasoning_parts)
            provider_metadata = message.get("provider_metadata")
            if isinstance(provider_metadata, dict) and provider_metadata:
                payload["provider_metadata"] = copy.deepcopy(provider_metadata)
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                payload["tool_calls"] = copy.deepcopy(tool_calls)
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id", "") or "")
            if tool_call_id:
                payload["tool_call_id"] = tool_call_id
            tool_name = str(message.get("tool_name", "") or message.get("name") or "")
            if tool_name:
                payload["name"] = tool_name
            if message.get("provider_executed") or message.get("providerExecuted"):
                payload["provider_executed"] = True
        normalized.append(payload)

    if len(normalized) == 1:
        normalized.append({"role": "user", "content": "你好"})
    return normalized


def _parse_tool_call(
    raw_id: str,
    raw_name: str,
    raw_arguments: str,
    *,
    metadata: dict[str, Any] | None = None,
    provider_executed: bool = False,
) -> ToolCall:
    call_id = raw_id.strip() or f"call_{uuid4().hex[:10]}"
    name = raw_name.strip()
    try:
        parsed = json.loads(raw_arguments or "{}")
        arguments = parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        logger.warning("Failed to parse tool arguments for %s: %s", name, raw_arguments[:200])
        arguments = {}
    return ToolCall(
        id=call_id,
        name=name,
        arguments=arguments,
        metadata=copy.deepcopy(metadata) if isinstance(metadata, dict) and metadata else None,
        provider_executed=provider_executed,
    )


def _extract_latest_user_request(messages: list[dict]) -> str:
    for message in reversed(messages):
        if str(message.get("role") or "") != "user":
            continue
        content = _extract_user_text_content(message.get("content")).strip()
        marker = "下面是用户真正的请求："
        if marker in content:
            content = content.split(marker, 1)[1].strip()
        if content:
            return content
    return ""


def _normalize_agent_backend_id(value: str | None) -> str:
    return normalize_agent_backend_id(value)


def _iter_text_chunks(text: str, chunk_size: int = 180) -> Iterator[str]:
    content = str(text or "")
    for index in range(0, len(content), chunk_size):
        yield content[index : index + chunk_size]


def _next_assistant_message_id() -> str:
    return f"message_{uuid4().hex}"


def _assistant_project_id(options: AgentRuntimeOptions) -> str:
    session_record = get_session_record(options.session_id) or {}
    return str(session_record.get("projectID") or "global")


def _stream_text_result(content: str) -> Iterator[str]:
    for chunk in _iter_text_chunks(content):
        yield _make_sse("text_delta", {"content": chunk})


def _json_loads_maybe(payload: Any) -> Any | None:
    return _shared_json_loads_maybe(payload)


def _normalize_cli_tool_args(raw_input: Any) -> dict[str, Any]:
    if isinstance(raw_input, dict):
        return copy.deepcopy(raw_input)
    parsed = _json_loads_maybe(raw_input)
    if isinstance(parsed, dict):
        return parsed
    return {}


def _extract_cli_tool_text(raw_output: Any) -> str:
    if isinstance(raw_output, str):
        return raw_output.strip()
    if not isinstance(raw_output, dict):
        return ""
    content = raw_output.get("content")
    if not isinstance(content, list):
        return ""
    lines: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def _summarize_cli_tool_text(text: str, *, max_len: int = 180) -> str:
    return _shared_summarize_tool_text(text, max_len=max_len)


def _coerce_cli_tool_result_event(
    item: dict[str, Any],
    *,
    default_call_id: str,
    default_name: str,
) -> dict[str, Any]:
    call_id = (
        str(item.get("tool_use_id") or item.get("id") or default_call_id).strip() or default_call_id
    )
    tool_name = (
        str(item.get("tool_name") or item.get("name") or default_name).strip() or default_name
    )

    raw_output = item.get("output")
    parsed_output = _json_loads_maybe(raw_output)
    is_error_flag = bool(
        (parsed_output or {}).get("isError")
        if isinstance(parsed_output, dict)
        else item.get("is_error")
    )
    success = not is_error_flag
    summary = ""
    data: dict[str, Any] = {}
    display_data: dict[str, Any] | None = None

    nested_payload: Any | None = None
    if isinstance(parsed_output, dict):
        structured = parsed_output.get("structuredContent")
        if isinstance(structured, dict):
            nested_payload = copy.deepcopy(structured)
        elif isinstance(structured, list):
            nested_payload = {"items": copy.deepcopy(structured)}
        if nested_payload is None:
            text_payload = _extract_cli_tool_text(parsed_output)
            if text_payload:
                nested_payload = _json_loads_maybe(text_payload)

    if isinstance(nested_payload, dict):
        if "success" in nested_payload:
            success = bool(nested_payload.get("success"))
        summary = str(nested_payload.get("summary") or "").strip()
        payload_data = nested_payload.get("data")
        if isinstance(payload_data, dict):
            data = copy.deepcopy(payload_data)
        elif payload_data is not None:
            data = {"value": copy.deepcopy(payload_data)}
        internal_data = nested_payload.get("internal_data")
        if isinstance(internal_data, dict):
            candidate_display = internal_data.get("display_data")
            if isinstance(candidate_display, dict) and candidate_display:
                display_data = copy.deepcopy(candidate_display)
    else:
        text_payload = _extract_cli_tool_text(
            parsed_output if isinstance(parsed_output, dict) else raw_output
        )
        if text_payload:
            summary = _summarize_cli_tool_text(text_payload)
            data = {"output": text_payload}
        elif isinstance(parsed_output, dict):
            data = copy.deepcopy(parsed_output)
            summary = _summarize_cli_tool_text(str(parsed_output.get("message") or ""))
        elif raw_output is not None:
            output_text = str(raw_output).strip()
            if output_text:
                summary = _summarize_cli_tool_text(output_text)
                data = {"output": output_text}

    if display_data:
        data.update({key: value for key, value in display_data.items() if key not in data})

    if not summary:
        summary = "工具调用完成" if success else "工具调用失败"

    payload: dict[str, Any] = {
        "id": call_id,
        "name": tool_name,
        "success": bool(success),
        "summary": summary,
        "data": data,
    }
    if display_data:
        payload["display_data"] = display_data
    return payload


def _format_tool_messages_turn_summary(tool_messages: list[dict[str, Any]]) -> str:
    normalized_results: list[dict[str, Any]] = []
    for item in tool_messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "") != "tool":
            continue
        parsed = _shared_json_loads_maybe(item.get("content"))
        if not isinstance(parsed, dict):
            continue
        normalized_results.append(
            {
                "id": str(item.get("tool_call_id") or "").strip() or None,
                "name": str(item.get("name") or "tool").strip() or "tool",
                "success": bool(parsed.get("success", True)),
                "summary": str(parsed.get("summary") or "").strip(),
                "data": copy.deepcopy(parsed.get("data")),
            }
        )
    return _shared_format_tool_result_turn_summary(normalized_results)


def _stream_cli_trace_events(result: dict[str, Any]) -> Iterator[str]:
    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        return

    reasoning_text = ""
    for key in ("reasoning", "reasoning_content", "thinking"):
        candidate = str(parsed.get(key) or "").strip()
        if candidate:
            reasoning_text = candidate
            break
    if reasoning_text:
        part_id = f"reasoning_cli_{uuid4().hex}"
        yield _make_sse("reasoning-start", {"id": part_id})
        for chunk in _iter_text_chunks(reasoning_text):
            yield _make_sse("reasoning_delta", {"id": part_id, "content": chunk})
        yield _make_sse("reasoning-end", {"id": part_id})

    raw_tool_uses = parsed.get("tool_uses")
    raw_tool_results = parsed.get("tool_results")
    tool_uses = [
        item
        for item in (raw_tool_uses if isinstance(raw_tool_uses, list) else [])
        if isinstance(item, dict)
    ]
    tool_results = [
        item
        for item in (raw_tool_results if isinstance(raw_tool_results, list) else [])
        if isinstance(item, dict)
    ]
    if not tool_uses and not tool_results:
        return

    results_by_call_id: dict[str, dict[str, Any]] = {}
    orphan_results: list[dict[str, Any]] = []
    for item in tool_results:
        call_id = str(item.get("tool_use_id") or item.get("id") or "").strip()
        if call_id and call_id not in results_by_call_id:
            results_by_call_id[call_id] = item
        else:
            orphan_results.append(item)

    started_call_ids: set[str] = set()
    for index, item in enumerate(tool_uses, start=1):
        call_id = str(item.get("id") or "").strip() or f"agent_call_{index}"
        tool_name = str(item.get("name") or item.get("tool_name") or "tool").strip() or "tool"
        tool_args = _normalize_cli_tool_args(item.get("input"))
        if call_id not in started_call_ids:
            started_call_ids.add(call_id)
            yield _make_sse(
                "tool_start",
                {
                    "id": call_id,
                    "name": tool_name,
                    "args": tool_args,
                },
            )
        result_item = results_by_call_id.pop(call_id, None)
        if result_item is not None:
            yield _make_sse(
                "tool_result",
                _coerce_cli_tool_result_event(
                    result_item,
                    default_call_id=call_id,
                    default_name=tool_name,
                ),
            )

    for index, item in enumerate([*results_by_call_id.values(), *orphan_results], start=1):
        call_id = (
            str(item.get("tool_use_id") or item.get("id") or "").strip()
            or f"agent_orphan_call_{index}"
        )
        tool_name = str(item.get("tool_name") or item.get("name") or "tool").strip() or "tool"
        if call_id not in started_call_ids:
            started_call_ids.add(call_id)
            yield _make_sse(
                "tool_start",
                {
                    "id": call_id,
                    "name": tool_name,
                    "args": {},
                },
            )
        yield _make_sse(
            "tool_result",
            _coerce_cli_tool_result_event(
                item,
                default_call_id=call_id,
                default_name=tool_name,
            ),
        )


def _stream_persisted_text_events(
    content: str,
    *,
    part_id: str | None = None,
) -> Iterator[PromptEvent]:
    text = str(content or "")
    if not text:
        return
    resolved_part_id = str(part_id or "").strip() or f"part_{uuid4().hex}"
    yield _prompt_event("text-start", {"id": resolved_part_id})
    for chunk in _iter_text_chunks(text):
        yield _prompt_event(
            "text_delta",
            {
                "id": resolved_part_id,
                "content": chunk,
            },
        )
    yield _prompt_event("text-end", {"id": resolved_part_id})


def _store_acp_pending_action(
    *,
    action_id: str,
    options: AgentRuntimeOptions,
    assistant_message_id: str,
    permission_payload: dict[str, Any],
) -> dict[str, Any]:
    tool_call_id = str(
        permission_payload.get("tool_call_id") or f"acp_permission_{action_id}"
    ).strip()
    tool_name = str(permission_payload.get("tool_name") or "custom_acp").strip() or "custom_acp"
    raw_input = (
        dict(permission_payload.get("raw_input") or {})
        if isinstance(permission_payload.get("raw_input"), dict)
        else {}
    )
    request = create_permission_request(
        request_id=action_id,
        session_id=options.session_id,
        project_id=_assistant_project_id(options),
        permission=f"acp_{tool_name}",
        patterns=[str(permission_payload.get("description") or tool_name or "ACP 权限请求")],
        metadata={
            "tool": tool_name,
            "arguments": raw_input,
            "acp": {
                "request_id": permission_payload.get("request_id"),
                "options": list(permission_payload.get("options") or []),
            },
        },
        always=[tool_name],
        tool={
            "callID": tool_call_id,
            "messageID": assistant_message_id,
        },
    )
    _store_pending_action(
        PendingAction(
            action_id=action_id,
            options=options,
            permission_request=asdict(request),
            continuation={"kind": "acp_prompt"},
        )
    )
    return {
        "id": action_id,
        "call_id": tool_call_id,
        "description": str(permission_payload.get("description") or "ACP 权限请求"),
        "tool": tool_name,
        "args": raw_input,
        "assistant_message_id": assistant_message_id,
        "permission": {
            "permission": request.permission,
            "patterns": request.patterns,
            "always": request.always,
        },
    }


def _build_cli_chat_prompt(
    user_messages: list[dict],
    options: AgentRuntimeOptions,
    *,
    backend_label: str,
) -> str:
    transcript_result = _build_shared_cli_transcript(
        user_messages,
        stringify_message_content=_stringify_message_content,
        extract_user_text_content=_extract_user_text_content,
    )
    transcript = transcript_result.entries
    prompt_shaping = _collect_latest_user_prompt_shaping(
        user_messages,
        fallback_request=transcript_result.latest_user_text,
    )

    mode_instruction = (
        "当前模式是 build，可以直接在工作区内落地实现、修改文件、执行命令并汇报结果。"
        if options.mode == "build"
        else "当前模式是 plan，请以分析、规划、审阅和方案建议为主，避免假装已经完成修改。"
        if options.mode == "plan"
        else "当前模式是 general，请综合利用上下文做较完整的分析与执行建议。"
    )
    context_sections = _build_turn_context_sections(
        options,
        user_tools=prompt_shaping.tools,
        latest_user_request=prompt_shaping.request,
    )
    return _build_shared_cli_chat_prompt_text(
        backend_label=backend_label,
        mode_instruction=mode_instruction,
        context_sections=context_sections,
        transcript_entries=transcript,
        latest_user_system=prompt_shaping.system,
        latest_user_output_constraint=prompt_shaping.output_constraint,
    )


def _stream_embedded_agent_chat(
    prompt: str,
    options: AgentRuntimeOptions,
    *,
    backend_label: str,
) -> Iterator[str]:
    service = get_cli_agent_service()
    try:
        config = service.get_runtime_config(LEGACY_CLI_AGENT_BACKEND_ID)
    except Exception as exc:
        yield _make_sse("error", {"message": str(exc)})
        return

    saw_text_delta = False
    streamed_text_fragments: list[str] = []
    done_payload: dict[str, Any] = {}
    tool_results: list[dict[str, Any]] = []
    try:
        event_stream = get_claw_runtime_manager().stream_prompt(
            config,
            prompt=prompt,
            workspace_path=options.workspace_path,
            workspace_server_id=options.workspace_server_id,
            timeout_sec=900,
            session_id=options.session_id,
        )
        for item in event_stream:
            if not isinstance(item, dict):
                continue
            event_name = str(item.get("event") or "").strip()
            payload = {
                key: copy.deepcopy(value)
                for key, value in item.items()
                if key not in {"event", "request_id", "requestId"}
            }
            if event_name == "done":
                done_payload = payload
                break
            if event_name == "error":
                yield _make_sse(
                    "error",
                    {"message": str(payload.get("message") or "内嵌运行时执行失败")},
                )
                continue
            if event_name == "text_delta" and str(payload.get("content") or ""):
                saw_text_delta = True
                streamed_text_fragments.append(str(payload.get("content") or ""))
            if event_name == "tool_result" and isinstance(payload, dict):
                tool_results.append(copy.deepcopy(payload))
            yield _make_sse(event_name, payload)
    except Exception as exc:  # pragma: no cover - exercised by live environment
        yield _make_sse("error", {"message": str(exc)})
        yield _make_sse(
            "done",
            {
                "agent_backend_id": DEFAULT_AGENT_BACKEND_ID,
                "agent_label": backend_label,
            },
        )
        return

    final_message = str(done_payload.get("message") or "").strip()
    streamed_text = "".join(streamed_text_fragments).strip()
    final_tool_results = [
        item
        for item in (
            done_payload.get("tool_results")
            if isinstance(done_payload.get("tool_results"), list)
            else []
        )
        if isinstance(item, dict)
    ]
    combined_tool_results = [copy.deepcopy(item) for item in tool_results if isinstance(item, dict)]
    seen_tool_result_keys = {
        (
            str(item.get("tool_use_id") or item.get("id") or "").strip(),
            str(item.get("tool_name") or item.get("name") or "").strip(),
        )
        for item in combined_tool_results
    }
    for item in final_tool_results:
        key = (
            str(item.get("tool_use_id") or item.get("id") or "").strip(),
            str(item.get("tool_name") or item.get("name") or "").strip(),
        )
        if key in seen_tool_result_keys:
            continue
        combined_tool_results.append(copy.deepcopy(item))
        seen_tool_result_keys.add(key)
    followup = _shared_resolve_tool_result_followup_text(
        streamed_text if saw_text_delta else final_message,
        combined_tool_results,
    )
    if not saw_text_delta and followup.final_text:
        yield from _stream_text_result(followup.final_text)
    elif saw_text_delta and followup.appended_summary and followup.summary_text:
        yield from _stream_text_result(f"\n\n{followup.summary_text}")

    yield _make_sse(
        "done",
        {
            "agent_backend_id": DEFAULT_AGENT_BACKEND_ID,
            "agent_label": backend_label,
            "execution_mode": "local_daemon",
            "duration_ms": done_payload.get("duration_ms"),
            "workspace_server_id": options.workspace_server_id,
            "session_path": done_payload.get("session_path"),
            "iterations": done_payload.get("iterations"),
            "status": done_payload.get("status"),
            "auto_compaction": done_payload.get("auto_compaction"),
        },
    )


def _stream_claw_daemon_chat(
    prompt: str,
    options: AgentRuntimeOptions,
    *,
    backend_label: str,
) -> Iterator[str]:
    yield from _stream_embedded_agent_chat(
        prompt,
        options,
        backend_label=backend_label,
    )


def _stream_cli_agent_chat(
    user_messages: list[dict],
    options: AgentRuntimeOptions,
    *,
    agent_backend_id: str,
) -> Iterator[str]:
    service = get_cli_agent_service()
    try:
        config = service.get_config(agent_backend_id)
    except ValueError as exc:
        yield _make_sse("error", {"message": str(exc)})
        yield _make_sse("done", {"agent_backend_id": agent_backend_id})
        return

    backend_label = str(config.get("label") or agent_backend_id)
    assistant_message_id = _next_assistant_message_id()
    yield _make_sse("assistant_message_id", {"message_id": assistant_message_id})
    prompt = _build_cli_chat_prompt(
        user_messages,
        options,
        backend_label=backend_label,
    )
    if agent_backend_id == CLAW_AGENT_BACKEND_ID:
        yield from _stream_embedded_agent_chat(
            prompt,
            options,
            backend_label=backend_label,
        )
        return
    try:
        result = service.execute_prompt(
            agent_backend_id,
            prompt=prompt,
            workspace_path=options.workspace_path,
            workspace_server_id=options.workspace_server_id,
            timeout_sec=900,
            session_id=options.session_id,
        )
    except Exception as exc:  # pragma: no cover - exercised by live environment
        yield _make_sse("error", {"message": str(exc)})
        yield _make_sse(
            "done",
            {
                "agent_backend_id": agent_backend_id,
                "agent_label": backend_label,
            },
        )
        return

    auto_accept_acp_permissions = (
        str((get_assistant_exec_policy() or {}).get("approval_mode") or "").strip() == "off"
    )
    while result.get("paused"):
        partial_content = str(result.get("content") or "").strip()
        if partial_content:
            yield from _stream_text_result(partial_content)
        pending_action_id = str(result.get("pending_action_id") or "").strip()
        permission_payload = (
            dict(result.get("permission_request") or {})
            if isinstance(result.get("permission_request"), dict)
            else {}
        )
        if not pending_action_id or not permission_payload:
            yield _make_sse("error", {"message": f"{backend_label} 权限请求缺少必要信息"})
            yield _make_sse(
                "done",
                {
                    "agent_backend_id": agent_backend_id,
                    "agent_label": backend_label,
                },
            )
            return
        if not auto_accept_acp_permissions:
            yield _make_sse(
                "action_confirm",
                _store_acp_pending_action(
                    action_id=pending_action_id,
                    options=options,
                    assistant_message_id=assistant_message_id,
                    permission_payload=permission_payload,
                ),
            )
            yield _make_sse(
                "done",
                {
                    "agent_backend_id": agent_backend_id,
                    "agent_label": backend_label,
                },
            )
            return
        try:
            result = get_acp_registry_service().respond_to_pending_permission(
                pending_action_id,
                response="always",
            )
        except Exception as exc:  # pragma: no cover - exercised by live environment
            yield _make_sse("error", {"message": str(exc)})
            yield _make_sse(
                "done",
                {
                    "agent_backend_id": agent_backend_id,
                    "agent_label": backend_label,
                },
            )
            return

    content = str(result.get("content") or "").strip()
    fallback_reason = str(result.get("fallback_reason") or "").strip() or None
    if not content:
        yield _make_sse("error", {"message": f"{backend_label} 没有返回有效结果"})
        yield _make_sse(
            "done",
            {
                "agent_backend_id": agent_backend_id,
                "agent_label": backend_label,
            },
        )
        return

    yield from _stream_cli_trace_events(result)
    yield from _stream_text_result(content)
    yield _make_sse(
        "done",
        {
            "agent_backend_id": agent_backend_id,
            "agent_label": backend_label,
            "execution_mode": result.get("execution_mode"),
            "duration_ms": result.get("duration_ms"),
            "workspace_server_id": result.get("workspace_server_id"),
            "fallback_reason": fallback_reason,
        },
    )


def _build_assistant_message(
    content: str,
    tool_calls: list[ToolCall],
    text_parts: list[dict[str, Any]] | None = None,
    reasoning_content: str = "",
    reasoning_parts: list[dict[str, Any]] | None = None,
    provider_metadata: dict[str, Any] | None = None,
) -> dict:
    payload: dict = {"role": "assistant", "content": content}
    if text_parts:
        payload["text_parts"] = copy.deepcopy(text_parts)
    if str(reasoning_content or "").strip():
        payload["reasoning_content"] = str(reasoning_content)
    if reasoning_parts:
        payload["reasoning_parts"] = copy.deepcopy(reasoning_parts)
    if isinstance(provider_metadata, dict) and provider_metadata:
        payload["provider_metadata"] = copy.deepcopy(provider_metadata)
    if tool_calls:
        payload["tool_calls"] = []
        for call in tool_calls:
            item: dict[str, Any] = {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            if isinstance(call.metadata, dict) and call.metadata:
                item["metadata"] = copy.deepcopy(call.metadata)
            if call.provider_executed:
                item["provider_executed"] = True
            payload["tool_calls"].append(item)
    return payload


def _build_tool_message(call: ToolCall, result: ToolResult) -> dict:
    content = json.dumps(
        {
            "success": result.success,
            "summary": result.summary,
            "data": result.data,
        },
        ensure_ascii=False,
    )
    payload = {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": content,
    }
    if call.provider_executed:
        payload["provider_executed"] = True
    return payload


def _build_question_tool_result(
    call: ToolCall,
    questions: list[dict[str, Any]],
    answers: list[list[str]],
    response_message: str | None,
    response: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if response == "reject":
        output = session_question.rejected_output(response_message)
        tool_result = ToolResult(
            success=False,
            summary="Question dismissed",
            data={
                "answers": [],
                "questions": copy.deepcopy(questions),
                "rejected": True,
                "message": str(response_message or "").strip() or None,
                "output": output,
            },
        )
    else:
        normalized_answers = session_question.normalize_answers_payload(answers, questions)
        tool_result = ToolResult(
            success=True,
            summary=session_question.success_title(questions),
            data={
                "answers": normalized_answers,
                "questions": copy.deepcopy(questions),
                "output": session_question.success_output(questions, normalized_answers),
            },
        )
    return (
        {
            "id": call.id,
            "name": call.name,
            "success": tool_result.success,
            "summary": tool_result.summary,
            "data": tool_result.data,
            "metadata": copy.deepcopy(call.metadata)
            if isinstance(call.metadata, dict) and call.metadata
            else None,
        },
        _build_tool_message(call, tool_result),
    )


def _fill_workspace_defaults(call: ToolCall, options: AgentRuntimeOptions) -> ToolCall:
    updated = dict(call.arguments)
    normalized_reasoning = str(options.reasoning_level or "default").strip().lower() or "default"
    search_profile = {
        "default": {"read_max_chars": 12000, "match_limit": 40, "max_entries": 120},
        "medium": {"read_max_chars": 12000, "match_limit": 40, "max_entries": 120},
        "low": {"read_max_chars": 6000, "match_limit": 20, "max_entries": 80},
        "high": {"read_max_chars": 20000, "match_limit": 80, "max_entries": 200},
    }.get(normalized_reasoning, {"read_max_chars": 12000, "match_limit": 40, "max_entries": 120})

    if call.name in {"read", "read_workspace_file"} and "max_chars" not in updated:
        updated["max_chars"] = search_profile["read_max_chars"]
    if call.name in {"glob", "grep"} and "limit" not in updated:
        updated["limit"] = search_profile["match_limit"]
    if call.name in {"list", "ls"} and "max_entries" not in updated:
        updated["max_entries"] = search_profile["max_entries"]

    if not options.workspace_path:
        return ToolCall(
            id=call.id,
            name=call.name,
            arguments=updated,
            metadata=copy.deepcopy(call.metadata)
            if isinstance(call.metadata, dict) and call.metadata
            else None,
            provider_executed=call.provider_executed,
        )
    workspace_arg_tools = {
        "inspect_workspace",
        "read_workspace_file",
        "write_workspace_file",
        "replace_workspace_text",
        "run_workspace_command",
    }
    if call.name in {"inspect_workspace"} and "max_entries" not in updated:
        updated["max_entries"] = search_profile["max_entries"]
    if call.name not in workspace_arg_tools:
        return ToolCall(
            id=call.id,
            name=call.name,
            arguments=updated,
            metadata=copy.deepcopy(call.metadata)
            if isinstance(call.metadata, dict) and call.metadata
            else None,
            provider_executed=call.provider_executed,
        )
    if not str(updated.get("workspace_path") or "").strip():
        updated["workspace_path"] = options.workspace_path
    return ToolCall(
        id=call.id,
        name=call.name,
        arguments=updated,
        metadata=copy.deepcopy(call.metadata)
        if isinstance(call.metadata, dict) and call.metadata
        else None,
        provider_executed=call.provider_executed,
    )


def _snapshot_workspace_root(options: AgentRuntimeOptions) -> str | None:
    server_id = str(options.workspace_server_id or "").strip().lower()
    if server_id and server_id != "local":
        return None
    candidate = str(options.workspace_path or "").strip()
    if candidate:
        return candidate
    session_record = get_session_record(options.session_id) or {}
    directory = str(session_record.get("directory") or "").strip()
    return directory or None


def _emit_step_finish(
    options: AgentRuntimeOptions,
    *,
    step: int,
    reason: str,
    usage: dict | None,
    start_snapshot: str | None,
) -> Iterator[PromptEvent]:
    finish_snapshot = None
    workspace_root = _snapshot_workspace_root(options)
    if workspace_root:
        try:
            finish_snapshot = session_snapshot.track(workspace_root)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.warning("Failed to capture finish snapshot: %s", exc)
    finish_payload = {
        "step": step,
        "reason": reason,
        "usage": usage or {},
        "cost": 0,
        "snapshot": finish_snapshot,
    }
    yield _prompt_event(
        "session_step_finish",
        finish_payload,
    )
    if workspace_root and start_snapshot:
        try:
            patch = session_snapshot.patch(workspace_root, start_snapshot)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.warning("Failed to compute snapshot patch: %s", exc)
        else:
            if patch.get("files"):
                patch_payload = {
                    "patches": [
                        {
                            **patch,
                            "workspace_path": workspace_root,
                        }
                    ]
                }
                yield _prompt_event(
                    "session_patch",
                    patch_payload,
                )


def _summarize_action(call: ToolCall) -> str:
    if call.name in {"inspect_workspace", "ls"}:
        workspace = str(
            call.arguments.get("workspace_path") or call.arguments.get("path") or ""
        ).strip()
        return f"将检查目录结构：{workspace or '[未提供路径]'}"
    if call.name in {"read_workspace_file", "read"}:
        target = str(
            call.arguments.get("relative_path") or call.arguments.get("file_path") or ""
        ).strip()
        return f"将读取文件：{target or '[未提供文件]'}"
    if call.name == "webfetch":
        url = str(call.arguments.get("url") or "").strip()
        return f"将抓取网页内容：{url or '[未提供 URL]'}"
    if call.name in {"search_web", "websearch"}:
        query = str(call.arguments.get("query") or "").strip()
        return f"将联网搜索：{query or '[未提供查询词]'}"
    if call.name == "codesearch":
        query = str(call.arguments.get("query") or "").strip()
        return f"将搜索代码/文档资料：{query or '[未提供查询词]'}"
    if call.name == "question":
        questions = session_question.normalize_questions_payload(call.arguments.get("questions"))
        if len(questions) == 1:
            return f"需要你先回答一个问题：{str(questions[0].get('question') or '').strip() or '[未提供问题]'}"
        return f"需要你先回答 {len(questions)} 个问题，然后智能体再继续执行"
    if call.name in {"write_workspace_file", "write"}:
        target = str(
            call.arguments.get("relative_path") or call.arguments.get("file_path") or ""
        ).strip()
        content = str(call.arguments.get("content") or "")
        return f"将写入文件 {target or '[未提供文件]'}（约 {len(content)} 个字符）"
    if call.name in {"replace_workspace_text", "edit"}:
        target = str(
            call.arguments.get("relative_path") or call.arguments.get("file_path") or ""
        ).strip()
        return f"将修改文件：{target or '[未提供文件]'}"
    if call.name == "multiedit":
        edits = call.arguments.get("edits") or []
        target = str(call.arguments.get("file_path") or "").strip()
        return f"将执行多处文件修改：{target or '[多个文件]'}（{len(edits)} 条 edit）"
    if call.name in {"run_workspace_command", "bash"}:
        command = str(call.arguments.get("command") or "").strip()
        background = bool(call.arguments.get("background"))
        suffix = "（后台任务）" if background else ""
        return f"将执行命令{suffix}：{command or '[空命令]'}"
    if call.name == "local_shell":
        action = (
            call.arguments.get("action") if isinstance(call.arguments.get("action"), dict) else {}
        )
        command = local_shell_command_to_string(action.get("command"))
        return f"将执行 local shell 命令：{command or '[空命令]'}"
    if call.name == "ingest_arxiv":
        arxiv_ids = call.arguments.get("arxiv_ids") or []
        return f"将导入 {len(arxiv_ids)} 篇 arXiv 论文到本地库"
    if call.name == "skim_paper":
        return "将对该论文执行粗读分析"
    if call.name == "deep_read_paper":
        return "将对该论文执行精读分析"
    if call.name == "embed_paper":
        return "将为该论文生成向量嵌入"
    if call.name == "generate_wiki":
        wiki_type = str(call.arguments.get("type") or "topic")
        target = str(call.arguments.get("keyword_or_id") or "")
        return f"将生成{wiki_type}综述：{target}"
    if call.name == "generate_daily_brief":
        return "将生成并保存研究简报"
    if call.name == "reasoning_analysis":
        return "将执行推理链分析"
    if call.name == "analyze_figures":
        return "将提取并分析论文图表"
    if call.name == "todowrite":
        return "将更新当前任务待办列表"
    return f"将执行工具：{call.name}"


def _store_pending_action(state: PendingAction) -> None:
    session_pending.store_pending_action(
        state,
        get_session_record=get_session_record,
    )


def _hydrate_pending_action(action_id: str) -> PendingAction | None:
    return session_pending.hydrate_pending_action(
        action_id,
        hydrate_options=_hydrate_pending_options,
        options_cls=AgentRuntimeOptions,
    )


def _delete_pending_action(action_id: str) -> None:
    session_pending.delete_pending_action(action_id)


def _pop_pending_action(action_id: str) -> PendingAction | None:
    return session_pending.pop_pending_action(
        action_id,
        hydrate_options=_hydrate_pending_options,
        options_cls=AgentRuntimeOptions,
    )


def get_pending_action(action_id: str) -> PendingAction | None:
    return session_pending.get_pending_action(
        action_id,
        hydrate_options=_hydrate_pending_options,
        options_cls=AgentRuntimeOptions,
    )


def _resolve_current_model_identity(options: AgentRuntimeOptions) -> dict[str, str]:
    llm = LLMClient()
    resolver = getattr(llm, "_resolve_model_target", None)
    if callable(resolver):
        try:
            target = resolver(
                "rag",
                str(getattr(options, "model_override", "") or "").strip() or None,
                variant_override=options.reasoning_level,
            )
            provider = str(getattr(target, "provider", "") or "").strip()
            model = str(getattr(target, "model", "") or "").strip()
            if provider or model:
                return {
                    "providerID": provider,
                    "modelID": model,
                }
        except Exception:
            logger.debug("Failed to resolve current model identity", exc_info=True)

    settings = get_settings()
    return {
        "providerID": str(getattr(llm, "provider", "") or settings.llm_provider or "").strip(),
        "modelID": str(getattr(settings, "llm_model_deep", "") or "").strip(),
    }


def _usage_to_tokens(usage: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(usage or {})
    input_tokens = int(payload.get("input_tokens") or 0)
    output_tokens = int(payload.get("output_tokens") or 0)
    reasoning_tokens = int(payload.get("reasoning_tokens") or 0)
    total = input_tokens + output_tokens + reasoning_tokens
    return {
        "total": total if total > 0 else None,
        "input": input_tokens,
        "output": output_tokens,
        "reasoning": reasoning_tokens,
        "cache": {"read": 0, "write": 0},
    }


def _build_turn_tools(
    llm: Any,
    options: AgentRuntimeOptions,
    *,
    disabled_tools: set[str],
    user_tools: dict[str, bool] | None = None,
) -> list[dict]:
    return build_turn_tools(
        llm,
        mode=options.mode,
        workspace_server_id=options.workspace_server_id,
        disabled_tools=set(disabled_tools),
        user_tools=user_tools,
        enabled_tools=_DEFAULT_AGENT_EXTENSION_TOOLS,
        reasoning_level=options.reasoning_level,
    )


def _assistant_checkpoint_meta(
    options: AgentRuntimeOptions,
    *,
    finish: str,
    usage: dict[str, Any] | None,
) -> dict[str, Any]:
    identity = _resolve_current_model_identity(options)
    return {
        "providerID": identity["providerID"],
        "modelID": identity["modelID"],
        "tokens": _usage_to_tokens(usage),
        "cost": 0.0,
        "finish": finish,
        "completed": _now_ms(),
    }


def _assistant_persistence_meta(
    options: AgentRuntimeOptions,
    *,
    session_record: dict[str, Any] | None = None,
    reasoning_level: str | None = None,
) -> dict[str, Any]:
    record = dict(session_record or {})
    identity = _resolve_current_model_identity(options)
    cwd = (
        options.workspace_path
        or str(record.get("workspace_path") or "").strip()
        or str(record.get("directory") or "").strip()
        or None
    )
    root = str(record.get("directory") or options.workspace_path or "").strip() or None
    return {
        "providerID": identity["providerID"],
        "modelID": identity["modelID"],
        "mode": options.mode,
        "agent": options.mode,
        "cwd": cwd,
        "root": root,
        "variant": reasoning_level if reasoning_level is not None else options.reasoning_level,
        "tokens": _usage_to_tokens(None),
        "cost": 0.0,
    }


def _native_pending_messages(pending: PendingAction) -> list[dict]:
    return session_pending.native_pending_messages(
        pending,
        load_agent_messages=load_agent_messages,
        normalize_messages=_normalize_messages,
    )


def _pending_permission_tool(pending: PendingAction) -> dict[str, Any]:
    return session_pending.pending_permission_tool(pending)


def _tokens_to_usage_payload(tokens: dict[str, Any] | None) -> dict[str, Any] | None:
    return session_pending.tokens_to_usage_payload(tokens)


def _native_pending_assistant_message(pending: PendingAction) -> tuple[str | None, dict[str, Any]]:
    return session_pending.native_pending_assistant_message(
        pending,
        get_session_message_by_id=get_session_message_by_id,
    )


def _native_pending_context(pending: PendingAction) -> dict[str, Any]:
    return session_pending.native_pending_context(
        pending,
        get_session_message_by_id=get_session_message_by_id,
        get_latest_user_message_id=get_latest_user_message_id,
    )


def _native_pending_resume_state(pending: PendingAction) -> session_pending.PendingResumeState:
    return session_pending.native_pending_resume_state(
        pending,
        get_session_message_by_id=get_session_message_by_id,
        get_latest_user_message_id=get_latest_user_message_id,
    )


def _native_pending_tool_calls(pending: PendingAction) -> list[ToolCall]:
    return session_pending.native_pending_tool_calls(
        pending,
        get_session_message_by_id=get_session_message_by_id,
        parse_tool_call=_parse_tool_call,
        fill_workspace_defaults=_fill_workspace_defaults,
        make_tool_call=ToolCall,
    )


def _native_pending_persistence(pending: PendingAction) -> StreamPersistenceConfig:
    return session_pending.native_pending_persistence(
        pending,
        get_session_record=get_session_record,
        get_latest_user_message_id=get_latest_user_message_id,
        get_session_message_by_id=get_session_message_by_id,
        assistant_meta_factory=lambda options, *, session_record: _assistant_persistence_meta(
            options,
            session_record=session_record,
        ),
        persistence_cls=StreamPersistenceConfig,
    )


def _merge_pending_persistence(
    pending: PendingAction,
    persistence: StreamPersistenceConfig | None = None,
) -> StreamPersistenceConfig:
    derived = _native_pending_persistence(pending)
    if persistence is None:
        return derived
    return StreamPersistenceConfig(
        session_id=derived.session_id,
        parent_id=derived.parent_id,
        assistant_meta=(
            copy.deepcopy(persistence.assistant_meta)
            if isinstance(persistence.assistant_meta, dict)
            else derived.assistant_meta
        ),
        assistant_message_id=derived.assistant_message_id,
    )


def _auto_compact_session(
    options: AgentRuntimeOptions,
    *,
    provider_id: str,
    model_id: str,
    overflow: bool,
) -> tuple[list[dict], str | None]:
    result = summarize_session(
        options.session_id,
        provider_id=provider_id,
        model_id=model_id,
        auto=True,
        overflow=overflow,
    )
    updated_messages = load_agent_messages(options.session_id)
    replay = result.get("replay") or {}
    replay_info = replay.get("info") or {}
    parent_id = str(replay_info.get("id") or "").strip() or None
    return updated_messages, parent_id


def _run_model_turn_events(
    messages: list[dict],
    options: AgentRuntimeOptions,
) -> Iterator[PromptEvent]:
    latest_user_request = _extract_latest_user_request(messages)
    output_constraint = _extract_output_constraint(latest_user_request)
    latest_user_tools = _extract_latest_user_tools(messages)
    session_record = get_session_record(options.session_id) or {
        "id": options.session_id,
        "projectID": "global",
        "directory": options.workspace_path or "",
        "workspace_path": options.workspace_path,
        "workspace_server_id": options.workspace_server_id,
        "permission": None,
    }
    policy = _runtime_exec_policy_override()
    disabled_tools = disabled_tools_from_rules(
        sorted(tool_registry_names()),
        effective_ruleset(session_record, policy)
        if policy is not None
        else effective_ruleset(session_record),
    )
    max_attempts = max(int(getattr(get_settings(), "agent_retry_max_attempts", 2) or 0), 0)
    result = yield from _processor_stream_model_turn_events(
        ModelTurnRuntimeConfig(
            messages=messages,
            llm=LLMClient(),
            options=options,
            latest_user_tools=latest_user_tools,
            disabled_tools=set(disabled_tools),
            output_constraint=output_constraint,
            max_attempts=max_attempts,
        ),
        ModelTurnRuntimeCallbacks(
            build_turn_tools=lambda llm, current_options, disabled, user_tools: _build_turn_tools(
                llm,
                current_options,
                disabled_tools=disabled,
                user_tools=user_tools,
            ),
            parse_tool_call=_parse_tool_call,
            fill_workspace_defaults=_fill_workspace_defaults,
            build_tool_message=_build_tool_message,
            make_tool_result=lambda success, summary, data: ToolResult(
                success=bool(success),
                summary=str(summary or ""),
                data=copy.deepcopy(data),
            ),
            tool_only_response=_format_tool_messages_turn_summary,
            repair_output_constraint_text=_repair_output_constraint_text,
            iter_text_chunks=_iter_text_chunks,
            detect_overflow_error=detect_overflow_error,
            retryable=session_retry.retryable,
            retry_delay_ms=session_retry.delay,
            sleep_retry=session_retry.sleep,
            set_session_status=set_session_status,
            is_session_aborted=is_session_aborted,
            on_exception=lambda exc: logger.exception("Agent model turn failed: %s", exc),
        ),
    )
    return result


def _run_model_turn(messages: list[dict], options: AgentRuntimeOptions) -> Iterator[str]:
    result = yield from _serialize_prompt_event_stream(_run_model_turn_events(messages, options))
    return result


def _execute_single_tool(
    call: ToolCall,
    options: AgentRuntimeOptions,
) -> Iterator[PromptEvent]:
    result = yield from _processor_stream_tool_execution_events(
        ToolExecutionConfig(call=call, options=options),
        ToolExecutionCallbacks(
            make_tool_context=lambda current_options: AgentToolContext(
                session_id=current_options.session_id,
                mode=current_options.mode,
                workspace_path=current_options.workspace_path,
                workspace_server_id=current_options.workspace_server_id,
                runtime_options=current_options,
            ),
            execute_tool_stream=lambda name, arguments, context: execute_tool_stream(
                name,
                arguments,
                context=context,
            ),
            is_progress_event=lambda event: isinstance(event, ToolProgress),
            make_tool_result=lambda success, summary, data: ToolResult(
                success=bool(success),
                summary=str(summary or ""),
                data=copy.deepcopy(data),
            ),
        ),
    )
    return result


def _fallback_step_limit_summary(messages: list[dict], max_steps: int) -> str:
    tool_summaries: list[str] = []
    last_assistant_text = ""

    for message in reversed(messages):
        role = str(message.get("role") or "")
        if role == "tool":
            try:
                payload = json.loads(str(message.get("content") or "{}"))
            except json.JSONDecodeError:
                payload = {}
            summary = str(payload.get("summary") or "").strip()
            if summary:
                tool_summaries.append(f"- {summary}")
            if len(tool_summaries) >= 5:
                break
        elif role == "assistant" and not last_assistant_text:
            content = str(message.get("content") or "").strip()
            if content and not _shared_is_tool_progress_placeholder_text(content):
                last_assistant_text = content

    summary_lines = [
        _shared_build_step_limit_reached_notice(max_steps),
        "",
        "1. 已完成内容",
    ]
    if tool_summaries:
        summary_lines.extend(tool_summaries[:5])
    else:
        summary_lines.append("- 本轮没有形成可复用的工具结果。")

    summary_lines.extend(
        [
            "",
            "2. 当前已获得的关键信息或中间结果",
            f"- {last_assistant_text[:240]}"
            if last_assistant_text
            else "- 当前主要保留了部分中间过程，但缺少最终结论。",
            "",
            "3. 尚未完成内容",
            "- 还没有在本轮内完成最终收敛，需要缩小问题范围或继续下一轮。",
            "",
            "4. 建议下一步",
            "- 可以继续追问一个更小、更明确的子任务，我会基于当前上下文继续推进。",
        ]
    )
    return "\n".join(summary_lines)


def _stream_step_limit_summary(
    messages: list[dict],
    options: AgentRuntimeOptions,
    *,
    max_steps: int,
) -> Iterator[PromptEvent]:
    llm = LLMClient()
    summary_messages = copy.deepcopy(messages)
    summary_messages.append({"role": "user", "content": STEP_LIMIT_SUMMARY_PROMPT})
    has_text = False
    open_text_part_id: str | None = None

    try:
        for event in llm.chat_stream(
            summary_messages,
            tools=None,
            variant_override=options.reasoning_level,
            model_override=str(getattr(options, "model_override", "") or "").strip() or None,
            session_cache_key=options.session_id,
        ):
            if event.type == "text_delta" and event.content:
                has_text = True
                if not open_text_part_id:
                    open_text_part_id = f"part_{uuid4().hex}"
                    yield _prompt_event("text-start", {"id": open_text_part_id})
                yield _prompt_event(
                    "text_delta",
                    {
                        "id": open_text_part_id,
                        "content": event.content,
                    },
                )
            elif event.type == "usage":
                usage_payload = {
                    "model": event.model,
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "reasoning_tokens": event.reasoning_tokens,
                }
                yield _prompt_event(
                    "usage",
                    usage_payload,
                )
            elif event.type == "error":
                logger.warning("Step limit summary stream failed: %s", event.content)
                has_text = False
                break
    except Exception as exc:  # pragma: no cover - defensive path
        logger.exception("Step limit summary failed: %s", exc)
        has_text = False

    if not has_text:
        fallback_content = _fallback_step_limit_summary(messages, max_steps)
        if not open_text_part_id:
            open_text_part_id = f"part_{uuid4().hex}"
            yield _prompt_event("text-start", {"id": open_text_part_id})
        yield _prompt_event(
            "text_delta",
            {"id": open_text_part_id, "content": fallback_content},
        )
    if open_text_part_id:
        yield _prompt_event("text-end", {"id": open_text_part_id})


def _process_tool_calls(
    messages: list[dict],
    tool_calls: list[ToolCall],
    step_index: int,
    options: AgentRuntimeOptions,
    *,
    skip_first_confirmation: bool = False,
    step_snapshot: str | None = None,
    step_usage: dict[str, Any] | None = None,
    assistant_message_id: str | None = None,
    rotate_message_on_pause_after_progress: bool = False,
) -> Iterator[PromptEvent]:
    session_record = get_session_record(options.session_id) or {
        "id": options.session_id,
        "projectID": "global",
        "directory": options.workspace_path or "",
        "workspace_path": options.workspace_path,
        "workspace_server_id": options.workspace_server_id,
        "permission": None,
    }
    policy = _runtime_exec_policy_override()

    def _create_pending_action(config: ToolPendingActionConfig) -> PendingAction:
        request = getattr(config.decision, "request", None)
        return PendingAction(
            action_id=config.action_id,
            options=config.options,
            permission_request=asdict(request) if request is not None else None,
        )

    result = yield from _processor_stream_tool_call_processing(
        ToolCallProcessingConfig(
            messages=messages,
            tool_calls=tool_calls,
            step_index=step_index,
            options=options,
            session_record=session_record,
            skip_first_confirmation=skip_first_confirmation,
            step_snapshot=step_snapshot,
            step_usage=step_usage,
            assistant_message_id=assistant_message_id,
            rotate_message_on_pause_after_progress=rotate_message_on_pause_after_progress,
        ),
        ToolCallProcessingCallbacks(
            get_tool_definition=get_tool_definition,
            authorize_tool_call=(
                (
                    lambda call, session, *, create_pending_request=True: authorize_tool_call(
                        call,
                        session,
                        policy,
                        create_pending_request=create_pending_request,
                    )
                )
                if policy is not None
                else authorize_tool_call
            ),
            permission_manages_tool=permission_manages_tool,
            requires_confirmation=_requires_confirmation,
            create_pending_action=_create_pending_action,
            store_pending_action=_store_pending_action,
            summarize_action=_summarize_action,
            build_tool_message=_build_tool_message,
            execute_tool=_execute_single_tool,
            make_tool_result=lambda success, summary, data: ToolResult(
                success=bool(success),
                summary=str(summary or ""),
                data=copy.deepcopy(data),
            ),
            emit_step_finish=lambda step, reason, usage, start_snapshot: _emit_step_finish(
                options,
                step=step,
                reason=reason,
                usage=usage,
                start_snapshot=start_snapshot,
            ),
            assistant_checkpoint_meta=lambda finish, usage: _assistant_checkpoint_meta(
                options,
                finish=finish,
                usage=usage,
            ),
            next_assistant_message_id=_next_assistant_message_id,
        ),
    )
    return result


def _requires_confirmation(call: ToolCall, tool_def, options: AgentRuntimeOptions) -> bool:
    if options.mode == "plan":
        return False
    policy = get_assistant_exec_policy()
    if policy.get("approval_mode") == "off":
        return False
    if should_confirm_workspace_action(call.name):
        return True
    return bool(tool_def and tool_def.requires_confirm)


class SessionPromptProcessor:
    @dataclass(frozen=True)
    class QueuedPermissionResponse:
        action_id: str
        response: str
        message: str | None
        answers: list[list[str]] | None
        pending: PendingAction

    def __init__(
        self,
        *,
        messages: list[dict],
        options: AgentRuntimeOptions,
        step_index: int = 0,
        assistant_message_id: str | None = None,
        lifecycle_kind: str = "prompt",
        resume_existing: bool = False,
        persistence: StreamPersistenceConfig | None = None,
        manage_session_lifecycle: bool = True,
        prefer_explicit_messages: bool = False,
        queued_permission_response: QueuedPermissionResponse | None = None,
    ) -> None:
        self.messages = messages
        self.options = options
        self.step_index = step_index
        self.assistant_message_id = assistant_message_id
        self.lifecycle_kind = lifecycle_kind
        self.resume_existing = resume_existing
        self.persistence = persistence
        self.manage_session_lifecycle = manage_session_lifecycle
        self.prefer_explicit_messages = prefer_explicit_messages
        self.queued_permission_response = queued_permission_response
        self._lifecycle_session: PromptLifecycleSession | None = None
        self._lifecycle_started_at = 0

    def _messages_for_run(self) -> list[dict]:
        if self.prefer_explicit_messages and self.messages:
            return copy.deepcopy(self.messages)
        session_id = str(self.options.session_id or "").strip()
        if session_id and self.lifecycle_kind in {"prompt", "callback", "resume"}:
            persisted = load_agent_messages(session_id)
            if persisted:
                return _normalize_messages(persisted, self.options)
        return self.messages

    def _callback_payload(self) -> dict[str, Any]:
        payload = {
            "session_id": str(self.options.session_id or "").strip(),
        }
        request_message_id = (
            str((self.persistence.parent_id if self.persistence is not None else "") or "").strip()
            or None
        )
        if request_message_id is not None:
            payload["request_message_id"] = request_message_id
        return payload

    def _lifecycle_config(self) -> PromptLifecycleConfig:
        session_id = str(self.options.session_id or "").strip()
        return PromptLifecycleConfig(
            session_id=session_id,
            processor_session_id=(
                self.persistence.session_id if self.persistence is not None else session_id
            ),
            parent_id=(
                self.persistence.parent_id
                if self.persistence is not None
                else get_latest_user_message_id(session_id)
            ),
            assistant_meta=(
                copy.deepcopy(self.persistence.assistant_meta)
                if self.persistence is not None
                and isinstance(self.persistence.assistant_meta, dict)
                else _assistant_persistence_meta(self.options)
            ),
            assistant_message_id=(
                self.persistence.assistant_message_id
                if self.persistence is not None
                else self.assistant_message_id
            ),
            lifecycle_kind=self.lifecycle_kind,
            step_index=self.step_index,
        )

    @staticmethod
    def _permission_lifecycle_config(
        pending: PendingAction,
        *,
        resume_state: session_pending.PendingResumeState,
        persistence: StreamPersistenceConfig | None,
    ) -> PromptLifecycleConfig:
        session_id = str(pending.options.session_id or "").strip()
        return PromptLifecycleConfig(
            session_id=session_id,
            processor_session_id=str(
                (persistence.session_id if persistence is not None else session_id) or ""
            ).strip(),
            parent_id=(
                persistence.parent_id
                if persistence is not None
                else (
                    str(resume_state.request_message_id or "").strip()
                    or get_latest_user_message_id(session_id)
                )
            ),
            assistant_meta=(
                copy.deepcopy(persistence.assistant_meta)
                if persistence is not None and isinstance(persistence.assistant_meta, dict)
                else None
            ),
            assistant_message_id=(
                persistence.assistant_message_id
                if persistence is not None
                else str(resume_state.assistant_message_id or "").strip() or None
            ),
            lifecycle_kind="resume",
            step_index=int(resume_state.step_index or 0),
        )

    @staticmethod
    def _callback_kind(payload: dict[str, Any] | None) -> str:
        kind = str((payload or {}).get("kind") or "prompt").strip().lower()
        return kind or "prompt"

    @staticmethod
    def _callback_runtime_from_payload(
        payload: dict[str, Any] | None,
    ) -> tuple[AgentRuntimeOptions, StreamPersistenceConfig]:
        raw = dict(payload or {})
        session_id = str(raw.get("session_id") or "").strip()
        if not session_id:
            raise RuntimeError("queued prompt callback is missing session context")
        options, session_record, request_message, request_message_id = _session_prompt_runtime(
            session_id,
            str(raw.get("request_message_id") or "").strip() or None,
        )
        turn_state = _session_loop_turn_state(session_id) or {}
        request_info = (
            request_message.get("info") if isinstance(request_message.get("info"), dict) else {}
        )
        reasoning_level = str(request_info.get("variant") or "").strip().lower() or "default"

        assistant_message_id = _next_assistant_message_id()
        existing_assistant_id = str(turn_state.get("assistant_message_id") or "").strip()
        if existing_assistant_id:
            existing_assistant = (
                turn_state.get("assistant_message")
                if isinstance(turn_state.get("assistant_message"), dict)
                else get_session_message_by_id(session_id, existing_assistant_id) or {}
            )
            existing_info = (
                existing_assistant.get("info")
                if isinstance(existing_assistant.get("info"), dict)
                else {}
            )
            existing_role = str(existing_info.get("role") or "").strip()
            existing_parent_id = str(
                existing_info.get("parentID") or existing_info.get("parentId") or ""
            ).strip()
            if existing_role == "assistant" and existing_parent_id == request_message_id:
                assistant_message_id = existing_assistant_id
        persistence = StreamPersistenceConfig(
            session_id=session_id,
            parent_id=request_message_id,
            assistant_meta=_assistant_persistence_meta(
                options,
                session_record=session_record,
                reasoning_level=reasoning_level,
            ),
            assistant_message_id=assistant_message_id,
        )
        return options, persistence

    @classmethod
    def _from_callback_payload(
        cls,
        payload: dict[str, Any],
        *,
        resume_existing: bool,
        manage_session_lifecycle: bool,
    ) -> SessionPromptProcessor:
        options, persistence = cls._callback_runtime_from_payload(payload)
        return cls(
            messages=[],
            options=options,
            step_index=0,
            assistant_message_id=str(persistence.assistant_message_id or "").strip() or None,
            lifecycle_kind="callback",
            resume_existing=resume_existing,
            persistence=persistence,
            manage_session_lifecycle=manage_session_lifecycle,
        )

    @classmethod
    def _from_permission_callback_payload(
        cls,
        payload: dict[str, Any],
        *,
        resume_existing: bool,
        manage_session_lifecycle: bool,
    ) -> SessionPromptProcessor:
        raw = dict(payload or {})
        action_id = str(raw.get("action_id") or "").strip()
        if not action_id:
            raise RuntimeError("queued permission callback is missing action context")
        pending = get_pending_action(action_id)
        if pending is None:
            raise RuntimeError("待确认动作不存在或已失效")
        resume_state = _native_pending_resume_state(pending)
        persistence = _merge_pending_persistence(pending)
        answers = [
            [str(item).strip() for item in answer if str(item).strip()]
            for answer in (raw.get("answers") or [])
            if isinstance(answer, list)
        ] or None
        return cls(
            messages=[],
            options=pending.options,
            step_index=int(resume_state.step_index or 0),
            assistant_message_id=str(resume_state.assistant_message_id or "").strip() or None,
            lifecycle_kind="resume",
            resume_existing=resume_existing,
            persistence=persistence,
            manage_session_lifecycle=manage_session_lifecycle,
            queued_permission_response=cls.QueuedPermissionResponse(
                action_id=action_id,
                response=str(raw.get("response") or "").strip(),
                message=str(raw.get("message") or "").strip() or None,
                answers=copy.deepcopy(answers) if isinstance(answers, list) else None,
                pending=pending,
            ),
        )

    @classmethod
    def _processor_from_callback(
        cls,
        callback,
        *,
        resume_existing: bool,
        manage_session_lifecycle: bool,
    ) -> SessionPromptProcessor:
        payload = dict(callback.payload or {})
        if not str(payload.get("session_id") or "").strip():
            payload["session_id"] = str(getattr(callback, "session_id", "") or "").strip() or None
        if cls._callback_kind(payload) == "permission":
            return cls._from_permission_callback_payload(
                payload,
                resume_existing=resume_existing,
                manage_session_lifecycle=manage_session_lifecycle,
            )
        return cls._from_callback_payload(
            payload,
            resume_existing=resume_existing,
            manage_session_lifecycle=manage_session_lifecycle,
        )

    @staticmethod
    def _iter_callback_stream(callback) -> Iterator[str]:  # noqa: ANN001
        yield from _processor_iter_callback_stream(callback)

    @classmethod
    def _resume_queued_callbacks(
        cls,
        session_id: str,
        *,
        resume_existing: bool = False,
    ) -> bool:
        loop_status, callback = claim_prompt_callback(session_id)
        if loop_status == "running":
            return True
        if callback is None:
            return False

        cls._run_callback_loop(
            session_id,
            callback,
            resume_existing=resume_existing,
        )
        return True

    @staticmethod
    def _reject_callback_list(callbacks: list[Any], message: str) -> None:
        normalized = str(message or "").strip() or "前序会话未正常结束，已取消排队请求"
        control = PromptStreamControl(
            saw_done=True,
            error_message=normalized,
            cancelled=normalized == "会话已中止",
        )
        reject_prompt_callbacks(callbacks, normalized, control=control)

    @classmethod
    def _settle_callback_result(
        cls,
        current_callback,
        *,
        queued_callbacks: list[Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        paused: bool = False,
        control: Any | None = None,
    ) -> None:
        if current_callback is None:
            return
        if error and not paused:
            current_callback.reject(error, control=control)
            cls._reject_callback_list(list(queued_callbacks or []), error)
            return
        current_callback.resolve(result=result, control=control)
        for callback in list(queued_callbacks or []):
            if callback is None:
                continue
            callback.resolve(result=result, control=control)

    @classmethod
    def _run_callback_loop(
        cls,
        session_id: str,
        callback,
        *,
        resume_existing: bool = False,
    ) -> None:
        def _run_single_callback(
            current_callback, callback_kind: str, current_resume_existing: bool
        ) -> QueuedCallbackRunResult:  # noqa: ANN001
            del callback_kind
            current_runner = cls._processor_from_callback(
                current_callback,
                resume_existing=current_resume_existing,
                manage_session_lifecycle=False,
            )
            stream = current_runner._stream_active()

            observed = PromptStreamControl()
            for item in stream:
                observed.observe(item)
            observed.absorb(getattr(stream, "_researchos_prompt_control", None))
            message_id = observed.assistant_message_id
            result = _prompt_result_payload(session_id, message_id)
            next_payload = None
            if (
                getattr(current_runner, "queued_permission_response", None) is not None
                and not observed.paused
                and not _prompt_terminal_error(observed)
            ):
                next_payload = {
                    "kind": "prompt",
                    "session_id": session_id,
                }
            return QueuedCallbackRunResult(
                control=observed,
                message_id=message_id,
                error=_prompt_terminal_error(observed),
                paused=bool(observed.paused),
                result=result,
                next_payload=next_payload,
            )

        current_callback = callback
        current_resume_existing = resume_existing
        final_message_id: str | None = None
        final_result: dict[str, Any] | None = None
        final_control: PromptStreamControl | None = None
        terminal_error: str | None = None
        terminal_paused = False
        owner_finished = False
        queued_to_resolve: list[Any] = []

        try:
            while current_callback is not None:
                if is_session_aborted(session_id):
                    cls._clear_aborted_permission_callback(current_callback)
                    final_message_id = cls._terminal_message_id_for_callback(
                        session_id, current_callback
                    )
                    final_result = _prompt_result_payload(session_id, final_message_id)
                    final_control = PromptStreamControl(
                        saw_done=True,
                        error_message="会话已中止",
                        cancelled=True,
                        assistant_message_id=final_message_id,
                    )
                    terminal_error = "会话已中止"
                    break
                turn_state = _session_loop_turn_state(session_id) or {}
                if not bool(turn_state.get("has_pending_prompt")):
                    final_message_id = (
                        str(turn_state.get("latest_finished_assistant_id") or "").strip()
                        or str(turn_state.get("assistant_message_id") or "").strip()
                        or final_message_id
                    )
                    final_result = _prompt_result_payload(session_id, final_message_id)
                    queued_to_resolve = drain_prompt_callbacks(session_id)
                    finish_prompt_instance(session_id, result=final_result)
                    owner_finished = True
                    break

                callback_kind = cls._callback_kind(
                    current_callback.payload
                    if isinstance(getattr(current_callback, "payload", None), dict)
                    else None
                )
                try:
                    run_result = _run_single_callback(
                        current_callback,
                        callback_kind,
                        current_resume_existing,
                    )
                except Exception as exc:  # pragma: no cover - defensive path
                    logger.exception(
                        "Queued prompt callback failed for %s: %s",
                        session_id,
                        exc,
                    )
                    terminal_error = str(exc)
                    break

                final_control = run_result.control
                final_message_id = str(run_result.message_id or "").strip() or final_message_id
                terminal_paused = bool(run_result.paused)
                terminal_error = str(run_result.error or "").strip() or None
                if terminal_paused or terminal_error:
                    final_result = (
                        copy.deepcopy(run_result.result)
                        if isinstance(run_result.result, dict)
                        else _prompt_result_payload(session_id, final_message_id)
                    )
                    break

                if isinstance(run_result.next_payload, dict) and run_result.next_payload:
                    current_callback.payload = copy.deepcopy(run_result.next_payload)
                current_resume_existing = True
                continue
        finally:
            settled_result = (
                _prompt_result_payload(session_id, final_message_id)
                if terminal_error and not terminal_paused
                else final_result
            )
            queued_rejections: list[Any] = []
            if terminal_paused:
                pause_prompt_instance(session_id)
            elif terminal_error and not owner_finished:
                queued_rejections = drain_prompt_callbacks(session_id)
                finish_prompt_instance(session_id, result=settled_result)
            cls._settle_callback_result(
                current_callback,
                queued_callbacks=queued_to_resolve or queued_rejections,
                result=settled_result,
                error=terminal_error,
                paused=terminal_paused,
                control=final_control,
            )
            if not terminal_paused and (owner_finished or terminal_error):
                set_session_status(session_id, {"type": "idle"})
                clear_session_abort(session_id)

    @staticmethod
    def _reject_queued_callbacks(session_id: str, message: str) -> None:
        SessionPromptProcessor._reject_callback_list(drain_prompt_callbacks(session_id), message)

    @staticmethod
    def _terminal_message_id_for_callback(session_id: str, callback) -> str | None:  # noqa: ANN001
        payload = callback.payload if isinstance(getattr(callback, "payload", None), dict) else {}
        if SessionPromptProcessor._callback_kind(payload) == "permission":
            action_id = str(payload.get("action_id") or "").strip()
            pending = get_pending_action(action_id) if action_id else None
            if pending is not None:
                return (
                    str(_native_pending_resume_state(pending).assistant_message_id or "").strip()
                    or None
                )
        turn_state = _session_loop_turn_state(session_id) or {}
        return (
            str(turn_state.get("assistant_message_id") or "").strip()
            or str(turn_state.get("latest_finished_assistant_id") or "").strip()
            or None
        )

    @staticmethod
    def _clear_aborted_permission_callback(callback) -> None:  # noqa: ANN001
        payload = callback.payload if isinstance(getattr(callback, "payload", None), dict) else {}
        if SessionPromptProcessor._callback_kind(payload) != "permission":
            return
        action_id = str(payload.get("action_id") or "").strip()
        if not action_id:
            return
        try:
            reply_permission(action_id, "reject", "会话已中止")
        except Exception:
            logger.exception(
                "Failed to reject pending permission callback during abort: %s", action_id
            )
        _delete_pending_action(action_id)

    def _require_lifecycle_session(self) -> PromptLifecycleSession:
        if self._lifecycle_session is None:
            raise RuntimeError("prompt lifecycle session is not initialized")
        return self._lifecycle_session

    def _emit_item(
        self,
        item: PromptEvent | str,
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        yield from self._require_lifecycle_session().emit_item(
            item,
            publish_bus=publish_bus,
        )

    def _emit_event(
        self,
        event_name: str,
        data: dict[str, Any],
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        yield from self._emit_item(
            _prompt_event(event_name, data),
            publish_bus=publish_bus,
        )

    def _emit_done(self, *, publish_bus: bool = False) -> Iterator[str]:
        yield from self._require_lifecycle_session().emit_done(publish_bus=publish_bus)

    def _emit_stream(
        self,
        raw_stream: Iterator[PromptEvent | str],
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        return (
            yield from self._require_lifecycle_session().emit_stream(
                raw_stream,
                publish_bus=publish_bus,
            )
        )

    def _lifecycle_completed(self, control: PromptStreamControl) -> bool:
        return bool(
            control.saw_done
            and not control.paused
            and not control.cancelled
            and not control.error_message
        )

    def _lifecycle_result(self, control: PromptStreamControl) -> str:
        if control.paused:
            return "paused"
        if control.cancelled:
            return "cancelled"
        if control.error_message:
            return "error"
        return "completed"

    def _on_lifecycle_start(self, current: PromptLifecycleSession) -> None:
        session_id = str(self.options.session_id or "").strip()
        self._lifecycle_started_at = _now_ms()
        if session_id:
            mark_prompt_instance_running(session_id, loop_kind="prompt")
        session_bus.publish(
            SessionBusEvent.PROMPT_STARTED,
            {
                "sessionID": session_id,
                "kind": self.lifecycle_kind,
                "step": self.step_index,
                "assistantMessageID": self.assistant_message_id,
            },
        )
        current.control.assistant_message_id = (
            current.control.assistant_message_id or self.assistant_message_id
        )

    def _on_lifecycle_exception(self, current: PromptLifecycleSession, exc: Exception) -> None:
        session_id = str(self.options.session_id or "").strip()
        current.control.error_message = str(exc)
        session_bus.publish(
            SessionBusEvent.ERROR,
            {
                "sessionID": session_id,
                "message": current.control.error_message,
            },
        )

    def _settle_lifecycle(self, session_id: str, control: PromptStreamControl) -> tuple[bool, bool]:
        owner_finished = False
        handed_off = False
        final_result = _prompt_result_payload(
            session_id,
            control.assistant_message_id,
        )
        if self.manage_session_lifecycle:
            if control.paused:
                pause_prompt_instance(session_id)
            elif self._lifecycle_completed(control):
                pause_prompt_instance(session_id)
                _, callback = claim_prompt_callback(session_id)
                if callback is not None:
                    handed_off = True
                    try:
                        type(self)._run_callback_loop(
                            session_id,
                            callback,
                            resume_existing=True,
                        )
                    except Exception:  # pragma: no cover - defensive path
                        logger.exception(
                            "Failed to resume queued prompt callback for %s", session_id
                        )
                        rejected = [callback, *drain_prompt_callbacks(session_id)]
                        finish_prompt_instance(session_id, result=final_result)
                        type(self)._reject_callback_list(
                            rejected,
                            "前序会话未正常结束，已取消排队请求",
                        )
                        owner_finished = True
                        handed_off = False
                else:
                    finish_prompt_instance(session_id, result=final_result)
                    owner_finished = True
            else:
                rejected = drain_prompt_callbacks(session_id)
                finish_prompt_instance(session_id, result=final_result)
                if rejected:
                    type(self)._reject_callback_list(
                        rejected,
                        (
                            control.error_message
                            or (
                                "会话已中止"
                                if control.cancelled
                                else "前序会话未正常结束，已取消排队请求"
                            )
                        ),
                    )
                owner_finished = True
        else:
            owner_finished = self._lifecycle_completed(control)
        control.handed_off = handed_off
        return owner_finished, handed_off

    def _on_lifecycle_finalize(self, current: PromptLifecycleSession) -> None:
        session_id = str(self.options.session_id or "").strip()
        control = current.control
        owner_finished = False
        if session_id:
            owner_finished, _ = self._settle_lifecycle(session_id, control)
            if self.manage_session_lifecycle:
                if owner_finished and not control.paused:
                    set_session_status(session_id, {"type": "idle"})
                if not control.paused:
                    clear_session_abort(session_id)
        session_bus.publish(
            SessionBusEvent.PROMPT_FINISHED,
            {
                "sessionID": session_id,
                "kind": self.lifecycle_kind,
                "step": self.step_index,
                "result": self._lifecycle_result(control),
                "duration_ms": max(_now_ms() - self._lifecycle_started_at, 0),
                "assistantMessageID": control.assistant_message_id,
            },
        )

    def _run_loop(self, messages: list[dict]) -> Iterator[str]:
        auto_compaction_enabled = _shared_is_auto_compaction_enabled()
        yield from _processor_stream_prompt_loop(
            PromptLoopRuntimeConfig(
                options=self.options,
                initial_messages=messages,
                step_index=self.step_index,
                assistant_message_id=self.assistant_message_id,
                max_steps=_resolve_max_tool_steps(self.options.reasoning_level),
                persistence_enabled=bool(self.options.session_id and self.persistence is not None),
                callbacks=PromptLoopCallbacks(
                    emit_event=self._emit_event,
                    emit_stream=self._emit_stream,
                    emit_done=self._emit_done,
                    publish_step_start=(
                        (
                            lambda step: session_bus.publish(
                                SessionBusEvent.STEP_STARTED,
                                {
                                    "sessionID": str(self.options.session_id or "").strip(),
                                    "step": int(step),
                                    "snapshot": None,
                                },
                            )
                        )
                        if str(self.options.session_id or "").strip()
                        else None
                    ),
                    prepare_loop_messages=lambda current_messages, current_step, max_steps: (
                        _prepare_loop_messages(
                            current_messages,
                            self.options,
                            current_step=current_step,
                            max_steps=max_steps,
                        )
                    ),
                    run_model_turn=lambda prepared_messages: _run_model_turn_events(
                        prepared_messages,
                        self.options,
                    ),
                    process_tool_calls=lambda current_messages, turn_calls, current_step, step_snapshot, step_usage, assistant_message_id: (
                        _process_tool_calls(
                            current_messages,
                            turn_calls,
                            current_step,
                            self.options,
                            step_snapshot=step_snapshot,
                            step_usage=step_usage,
                            assistant_message_id=assistant_message_id,
                        )
                    ),
                    step_limit_summary=lambda current_messages, max_steps: (
                        _stream_step_limit_summary(
                            current_messages,
                            self.options,
                            max_steps=max_steps,
                        )
                    ),
                    emit_step_finish=lambda step, reason, usage, start_snapshot: _emit_step_finish(
                        self.options,
                        step=step,
                        reason=reason,
                        usage=usage,
                        start_snapshot=start_snapshot,
                    ),
                    build_assistant_message=_build_assistant_message,
                    build_tool_message=_build_tool_message,
                    assistant_checkpoint_meta=lambda finish, usage: _assistant_checkpoint_meta(
                        self.options,
                        finish=finish,
                        usage=usage,
                    ),
                    resolve_model_identity=lambda: _resolve_current_model_identity(self.options),
                    usage_to_tokens=_usage_to_tokens,
                    is_overflow_tokens=is_overflow_tokens,
                    normalize_messages=lambda loaded_messages: _normalize_messages(
                        loaded_messages, self.options
                    ),
                    next_assistant_message_id=_next_assistant_message_id,
                    make_tool_result=lambda success, summary, data: ToolResult(
                        success=bool(success),
                        summary=str(summary or ""),
                        data=copy.deepcopy(data),
                    ),
                    auto_compact=(
                        lambda provider_id, model_id, overflow: _auto_compact_session(
                            self.options,
                            provider_id=provider_id,
                            model_id=model_id,
                            overflow=overflow,
                        )
                    )
                    if auto_compaction_enabled
                    else None,
                    latest_auto_compaction_target=(
                        (lambda: latest_auto_compaction_target(self.options.session_id))
                        if auto_compaction_enabled and self.options.session_id
                        else None
                    ),
                    load_persisted_messages=(
                        (lambda: load_agent_messages(self.options.session_id))
                        if self.options.session_id
                        else None
                    ),
                    snapshot_root=lambda: _snapshot_workspace_root(self.options),
                    capture_snapshot=lambda workspace_root: session_snapshot.track(workspace_root),
                    on_warning=lambda message, exc: logger.warning("%s: %s", message, exc),
                    max_auto_compaction_attempts=MAX_AUTO_COMPACTION_ATTEMPTS,
                ),
            )
        )

    def _stream_lifecycle(
        self,
        run: Callable[[PromptLifecycleSession], Iterator[str]],
        *,
        config: PromptLifecycleConfig | None = None,
    ) -> Iterator[str]:
        self._lifecycle_started_at = 0

        def _run_with_lifecycle(lifecycle: PromptLifecycleSession) -> Iterator[str]:
            self._lifecycle_session = lifecycle
            yield from run(lifecycle)

        return _mark_persisted_stream_if_needed(
            _processor_stream_prompt_lifecycle(
                config=config or self._lifecycle_config(),
                run=_run_with_lifecycle,
                on_start=self._on_lifecycle_start,
                on_exception=self._on_lifecycle_exception,
                on_finalize=self._on_lifecycle_finalize,
            ),
            self.persistence,
        )

    def _resolve_pending_resume_state(
        self, pending: PendingAction
    ) -> session_pending.PendingResumeState:
        return _native_pending_resume_state(pending)

    def _refresh_pending_permission_resume(
        self,
        pending: PendingAction,
    ) -> tuple[session_pending.PendingResumeState, StreamPersistenceConfig]:
        resume_state = self._resolve_pending_resume_state(pending)
        persistence = _merge_pending_persistence(pending, self.persistence)
        self.step_index = int(resume_state.step_index or 0)
        self.assistant_message_id = str(resume_state.assistant_message_id or "").strip() or None
        self.persistence = persistence
        return resume_state, persistence

    def _stream_cancelled(
        self,
        lifecycle: PromptLifecycleSession,
        *,
        action_id: str | None = None,
    ) -> Iterator[str]:
        if str(action_id or "").strip():
            try:
                reply_permission(str(action_id), "reject", "会话已中止")
            except Exception:
                logger.exception(
                    "Failed to reject pending permission during cancelled resume: %s", action_id
                )
            _delete_pending_action(str(action_id))
        yield from lifecycle.emit_event(
            "error",
            {"message": "会话已中止"},
            publish_bus=True,
        )
        yield from lifecycle.emit_done(publish_bus=True)

    def _stream_active(self) -> Iterator[str]:
        if self.queued_permission_response is not None:
            queued = self.queued_permission_response
            resume_state, persistence = self._refresh_pending_permission_resume(queued.pending)
            config = type(self)._permission_lifecycle_config(
                queued.pending,
                resume_state=resume_state,
                persistence=persistence,
            )
            if is_session_aborted(self.options.session_id):
                return self._stream_lifecycle(
                    lambda lifecycle: self._stream_cancelled(
                        lifecycle,
                        action_id=queued.action_id,
                    ),
                    config=config,
                )
            return self._stream_lifecycle(
                lambda lifecycle: self._stream_permission_response(
                    lifecycle,
                    action_id=queued.action_id,
                    response=queued.response,
                    message=queued.message,
                    answers=queued.answers,
                    pending=queued.pending,
                    resume_state=resume_state,
                ),
                config=config,
            )
        if self.resume_existing and is_session_aborted(self.options.session_id):
            return self._stream_lifecycle(
                lambda lifecycle: self._stream_cancelled(lifecycle),
            )
        return self._stream_lifecycle(
            lambda _lifecycle: self._run_loop(self._messages_for_run()),
        )

    def _resume_processor(
        self,
        *,
        messages: list[dict],
        step_index: int,
        assistant_message_id: str | None,
        prefer_explicit_messages: bool = False,
    ) -> SessionPromptProcessor:
        return type(self)(
            messages=messages,
            options=self.options,
            step_index=step_index,
            assistant_message_id=assistant_message_id,
            lifecycle_kind="resume",
            resume_existing=True,
            persistence=None,
            manage_session_lifecycle=self.manage_session_lifecycle,
            prefer_explicit_messages=prefer_explicit_messages,
        )

    def _stream_permission_response(
        self,
        lifecycle: PromptLifecycleSession,
        *,
        action_id: str,
        response: str,
        message: str | None,
        answers: list[list[str]] | None,
        pending: PendingAction,
        resume_state: session_pending.PendingResumeState,
    ) -> Iterator[str]:
        def _resume_stream(
            resume_messages: list[dict[str, Any]],
            next_step_index: int,
            next_assistant_message_id: str | None,
            prefer_explicit_messages: bool,
        ) -> Iterator[str]:
            stream = self._resume_processor(
                messages=resume_messages,
                step_index=next_step_index,
                assistant_message_id=next_assistant_message_id,
                prefer_explicit_messages=prefer_explicit_messages,
            ).stream()
            yield from stream
            lifecycle.control.absorb(getattr(stream, "_researchos_prompt_control", None))

        def _build_rejected_tool_result(
            first_call: ToolCall,
            response_message: str | None,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            summary = "用户拒绝执行该操作"
            if str(response_message or "").strip():
                summary = f"用户拒绝执行该操作：{str(response_message).strip()}"
            tool_result = ToolResult(
                success=False,
                summary=summary,
                data={
                    "rejected": True,
                    "message": str(response_message or "").strip() or None,
                },
            )
            return (
                {
                    "id": first_call.id,
                    "name": first_call.name,
                    "success": False,
                    "summary": tool_result.summary,
                    "data": tool_result.data,
                    "metadata": copy.deepcopy(first_call.metadata)
                    if isinstance(first_call.metadata, dict) and first_call.metadata
                    else None,
                },
                _build_tool_message(first_call, tool_result),
            )

        yield from _processor_stream_permission_response_runtime(
            PermissionResponseConfig(
                action_id=action_id,
                response=response,
                message=message,
                answers=copy.deepcopy(answers) if isinstance(answers, list) else None,
                pending=pending,
                resume_state=resume_state,
            ),
            PermissionResponseCallbacks(
                emit_event=lifecycle.emit_event,
                emit_done=lifecycle.emit_done,
                observe_prompt_events=lifecycle.observe_prompt_events,
                observe_stream=lifecycle.observe_stream,
                reply_permission=reply_permission,
                pop_pending_action=_pop_pending_action,
                pending_tool_calls=_native_pending_tool_calls,
                pending_messages=_native_pending_messages,
                build_rejected_tool_result=_build_rejected_tool_result,
                build_question_tool_result=_build_question_tool_result,
                emit_step_finish=lambda step, reason, usage, start_snapshot: _emit_step_finish(
                    pending.options,
                    step=step,
                    reason=reason,
                    usage=usage,
                    start_snapshot=start_snapshot,
                ),
                assistant_checkpoint_meta=lambda finish, usage: _assistant_checkpoint_meta(
                    pending.options,
                    finish=finish,
                    usage=usage,
                ),
                process_tool_calls_stream=lambda current_messages, pending_tool_calls, current_step, step_snapshot, step_usage, next_assistant_message_id: (
                    _process_tool_calls(
                        current_messages,
                        pending_tool_calls,
                        current_step,
                        pending.options,
                        skip_first_confirmation=True,
                        step_snapshot=step_snapshot,
                        step_usage=step_usage,
                        assistant_message_id=next_assistant_message_id,
                        rotate_message_on_pause_after_progress=True,
                    )
                ),
                resume_stream=_resume_stream,
                next_assistant_message_id=_next_assistant_message_id,
            ),
        )

    @classmethod
    def stream_permission_response(
        cls,
        action_id: str,
        response: str,
        message: str | None,
        answers: list[list[str]] | None = None,
        *,
        pending: PendingAction,
        persistence: StreamPersistenceConfig | None = None,
        manage_session_lifecycle: bool = True,
    ) -> Iterator[str]:
        resume_state = _native_pending_resume_state(pending)
        effective_persistence = _merge_pending_persistence(pending, persistence)
        runner = cls(
            messages=[],
            options=pending.options,
            step_index=int(resume_state.step_index or 0),
            assistant_message_id=str(resume_state.assistant_message_id or "").strip() or None,
            lifecycle_kind="resume",
            resume_existing=True,
            persistence=effective_persistence,
            manage_session_lifecycle=manage_session_lifecycle,
            queued_permission_response=cls.QueuedPermissionResponse(
                action_id=action_id,
                response=response,
                message=message,
                answers=copy.deepcopy(answers) if isinstance(answers, list) else None,
                pending=pending,
            ),
        )
        return runner._stream_active()

    def stream(self) -> Iterator[str]:
        session_id = str(self.options.session_id or "").strip()
        if session_id:
            if self.resume_existing:
                instance = get_prompt_instance(session_id)
                if instance is None:
                    raise RuntimeError("active prompt instance missing during resume_existing")
            else:
                instance = acquire_prompt_instance(session_id, wait=False)
                if instance is None:
                    session_bus.publish(
                        SessionBusEvent.PROMPT_QUEUED,
                        {
                            "sessionID": session_id,
                            "kind": self.lifecycle_kind,
                            "step": self.step_index,
                        },
                    )
                    callback = queue_prompt_callback(
                        session_id,
                        payload=self._callback_payload(),
                    )
                    return _mark_persisted_stream_if_needed(
                        self._iter_callback_stream(callback),
                        self.persistence,
                    )
        return self._stream_active()


def stream_chat(
    user_messages: list[dict],
    confirmed_action_id: str | None = None,
    *,
    session_id: str | None = None,
    assistant_message_id: str | None = None,
    agent_backend_id: str | None = None,
    mode: str = "build",
    workspace_path: str | None = None,
    workspace_server_id: str | None = None,
    reasoning_level: str = "default",
    model_override: str | None = None,
    active_skill_ids: list[str] | None = None,
    mounted_paper_ids: list[str] | None = None,
    mounted_primary_paper_id: str | None = None,
    persistence: StreamPersistenceConfig | None = None,
) -> Iterator[str]:
    if confirmed_action_id:
        return confirm_action(confirmed_action_id, persistence=persistence)

    session_backend_id = None
    if session_id:
        session_record = get_session_record(session_id) or {}
        session_backend_id = str(session_record.get("agent_backend_id") or "").strip() or None
    normalized_backend_id = _normalize_agent_backend_id(agent_backend_id or session_backend_id)
    prompt_messages = list(user_messages or [])
    if not prompt_messages and session_id:
        prompt_messages = load_agent_messages(session_id)

    options = _session_options(
        session_id=session_id,
        mode=mode,
        workspace_path=workspace_path,
        workspace_server_id=workspace_server_id,
        reasoning_level=reasoning_level,
        model_override=model_override,
        active_skill_ids=active_skill_ids,
        mounted_paper_ids=mounted_paper_ids,
        mounted_primary_paper_id=mounted_primary_paper_id,
    )

    if is_native_agent_backend(normalized_backend_id):
        return SessionPromptProcessor(
            messages=prompt_messages,
            options=options,
            assistant_message_id=assistant_message_id,
            persistence=persistence,
        ).stream()

    return _persist_inline_stream_if_needed(
        _stream_cli_agent_chat(
            prompt_messages,
            options,
            agent_backend_id=normalized_backend_id,
        ),
        persistence=persistence,
    )


def _respond_native_action_impl(
    action_id: str,
    response: str,
    message: str | None,
    answers: list[list[str]] | None = None,
    *,
    pending: PendingAction,
    persistence: StreamPersistenceConfig | None = None,
    manage_session_lifecycle: bool = True,
) -> Iterator[str]:
    return SessionPromptProcessor.stream_permission_response(
        action_id,
        response,
        message,
        answers,
        pending=pending,
        persistence=persistence,
        manage_session_lifecycle=manage_session_lifecycle,
    )


def _respond_action_impl(
    action_id: str,
    response: str,
    message: str | None = None,
    answers: list[list[str]] | None = None,
) -> Iterator[str]:
    pending = get_pending_action(action_id)
    if pending is None:
        yield _make_sse("error", {"message": "待确认动作不存在或已失效"})
        yield _make_sse("done", {})
        return

    if session_pending.is_acp_pending_action(pending):
        reply_permission(action_id, response, message)
        _pop_pending_action(action_id)
        yield _make_sse(
            "text_delta",
            {
                "content": (
                    "已拒绝该 ACP 权限请求，等待智能体继续返回结果。\n\n"
                    if response == "reject"
                    else "已确认，继续执行 ACP 权限请求。\n\n"
                )
            },
        )
        try:
            result = get_acp_registry_service().respond_to_pending_permission(
                action_id,
                response=response,
            )
        except Exception as exc:
            yield _make_sse("error", {"message": str(exc)})
            yield _make_sse("done", {})
            return

        content = str(result.get("content") or "").strip()
        if content:
            yield from _stream_text_result(content)
        if result.get("paused"):
            permission_payload = (
                dict(result.get("permission_request") or {})
                if isinstance(result.get("permission_request"), dict)
                else {}
            )
            if not permission_payload:
                yield _make_sse("error", {"message": "ACP 权限恢复后缺少新的权限请求信息"})
                yield _make_sse("done", {})
                return
            yield _make_sse(
                "action_confirm",
                _store_acp_pending_action(
                    action_id=action_id,
                    options=pending.options,
                    assistant_message_id=(
                        str(_pending_permission_tool(pending).get("messageID") or "").strip()
                        or _next_assistant_message_id()
                    ),
                    permission_payload=permission_payload,
                ),
            )
            yield _make_sse("done", {})
            return

        yield _make_sse(
            "done",
            {
                "agent_backend_id": "custom_acp",
                "agent_label": str(result.get("server_label") or "Custom ACP"),
            },
        )
        return

    yield from _respond_native_action_impl(
        action_id,
        response,
        message,
        answers,
        pending=pending,
    )


def _queue_native_action_callback(
    action_id: str,
    response: str,
    message: str | None,
    answers: list[list[str]] | None,
    *,
    pending: PendingAction,
    persistence: StreamPersistenceConfig | None = None,
) -> Iterator[str]:
    session_id = str(pending.options.session_id or "").strip()
    if not session_id:
        return _respond_native_action_impl(
            action_id,
            response,
            message,
            answers,
            pending=pending,
            persistence=persistence,
        )

    callback = queue_prompt_callback(
        session_id,
        payload={
            "kind": "permission",
            "session_id": session_id,
            "action_id": action_id,
            "response": response,
            "message": str(message or "").strip() or None,
            **({"answers": copy.deepcopy(answers)} if isinstance(answers, list) else {}),
        },
        front=True,
    )
    SessionPromptProcessor._resume_queued_callbacks(
        session_id,
        resume_existing=True,
    )
    return _mark_persisted_stream_if_needed(
        SessionPromptProcessor._iter_callback_stream(callback),
        persistence,
    )


def respond_action(
    action_id: str,
    response: str,
    message: str | None = None,
    answers: list[list[str]] | None = None,
    *,
    persistence: StreamPersistenceConfig | None = None,
) -> Iterator[str]:
    pending = get_pending_action(action_id)
    is_native_pending = session_pending.is_native_pending_action(pending)
    if is_native_pending:
        assert pending is not None
        return _queue_native_action_callback(
            action_id,
            response,
            message,
            answers,
            pending=pending,
            persistence=persistence,
        )
    if persistence is not None and session_pending.is_acp_pending_action(pending):
        return _persist_inline_stream_if_needed(
            _respond_action_impl(action_id, response, message, answers), persistence
        )
    return _persist_inline_stream_if_needed(
        _respond_action_impl(action_id, response, message, answers), persistence
    )


def confirm_action(
    action_id: str,
    *,
    persistence: StreamPersistenceConfig | None = None,
) -> Iterator[str]:
    return respond_action(action_id, "once", persistence=persistence)


def reject_action(
    action_id: str,
    *,
    persistence: StreamPersistenceConfig | None = None,
) -> Iterator[str]:
    return respond_action(action_id, "reject", persistence=persistence)
