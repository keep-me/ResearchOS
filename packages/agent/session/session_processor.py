"""Direct session processor for OpenCode-style MessageV2 mutation."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from packages.agent import (
    session_bus,
    session_message_v2,
    session_snapshot,
)
from packages.agent import (
    session_runtime as runtime,
)
from packages.agent.runtime.agent_runtime_policy import (
    build_repeated_tool_call_notice as _shared_build_repeated_tool_call_notice,
)
from packages.agent.runtime.agent_runtime_policy import (
    build_step_limit_reached_notice as _shared_build_step_limit_reached_notice,
)
from packages.agent.runtime.agent_runtime_policy import (
    is_tool_progress_placeholder_text as _shared_is_tool_progress_placeholder_text,
)
from packages.agent.runtime.agent_runtime_policy import (
    should_hard_stop_after_repeated_tool_calls as _shared_should_hard_stop_after_repeated_tool_calls,
)
from packages.agent.runtime.agent_runtime_policy import (
    should_hard_stop_after_tool_request as _shared_should_hard_stop_after_tool_request,
)
from packages.agent.runtime.agent_runtime_policy import (
    tool_call_signature as _shared_tool_call_signature,
)
from packages.agent.runtime.agent_transcript import (
    collect_tool_result_items_from_messages as _shared_collect_tool_result_items_from_messages,
)
from packages.agent.runtime.agent_transcript import (
    resolve_tool_result_followup_text as _shared_resolve_tool_result_followup_text,
)
from packages.agent.session.session_bus import SessionBusEvent
from packages.agent.session.session_errors import normalize_error
from packages.storage.db import session_scope
from packages.storage.repositories import AgentSessionMessageRepository


def _llm_chat_stream_with_compat(llm: Any, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
    try:
        return llm.chat_stream(messages, **kwargs)
    except TypeError as exc:
        if "model_override" not in str(exc):
            raise
        fallback_kwargs = dict(kwargs)
        fallback_kwargs.pop("model_override", None)
        return llm.chat_stream(messages, **fallback_kwargs)


@dataclass
class PromptStreamControl:
    handed_off: bool = False
    saw_done: bool = False
    paused: bool = False
    cancelled: bool = False
    error_message: str | None = None
    assistant_message_id: str | None = None
    action_confirm: dict[str, Any] | None = None
    text_parts: list[dict[str, Any]] = field(default_factory=list)

    def observe(
        self,
        item: str,
        *,
        session_id: str = "",
        lifecycle_kind: str = "prompt",
        step_index: int = 0,
        publish_bus: bool = False,
    ) -> None:
        parsed = runtime._parse_sse_event(item)
        if not parsed:
            return
        event_name, data = parsed
        self.observe_event(
            event_name,
            data,
            session_id=session_id,
            lifecycle_kind=lifecycle_kind,
            step_index=step_index,
            publish_bus=publish_bus,
        )

    def observe_event(
        self,
        event_name: str,
        data: dict[str, Any],
        *,
        session_id: str = "",
        lifecycle_kind: str = "prompt",
        step_index: int = 0,
        publish_bus: bool = False,
    ) -> None:
        if event_name == "assistant_message_id":
            candidate = str(data.get("message_id") or data.get("messageID") or "").strip()
            if candidate:
                self.assistant_message_id = candidate
            return
        if event_name == "session_step_start":
            if publish_bus:
                session_bus.publish(
                    SessionBusEvent.STEP_STARTED,
                    {
                        "sessionID": session_id,
                        "step": int(data.get("step") or 0),
                        "snapshot": data.get("snapshot"),
                    },
                )
            return
        if event_name == "session_step_finish":
            if publish_bus:
                session_bus.publish(
                    SessionBusEvent.STEP_FINISHED,
                    {
                        "sessionID": session_id,
                        "step": int(data.get("step") or 0),
                        "reason": str(data.get("reason") or "stop"),
                        "usage": dict(data.get("usage") or {})
                        if isinstance(data.get("usage"), dict)
                        else {},
                    },
                )
            return
        if event_name == "action_confirm":
            self.paused = True
            self.action_confirm = copy.deepcopy(data) if isinstance(data, dict) else {}
            if publish_bus:
                session_bus.publish(
                    SessionBusEvent.PROMPT_PAUSED,
                    {
                        "sessionID": session_id,
                        "kind": lifecycle_kind,
                        "step": int(data.get("step") or step_index),
                        "actionID": str(data.get("id") or ""),
                    },
                )
            return
        if event_name == "text_delta":
            content = str(data.get("content") or "")
            if not content:
                return
            part_id = str(data.get("id") or "").strip()
            metadata = (
                copy.deepcopy(data.get("metadata"))
                if isinstance(data.get("metadata"), dict)
                else None
            )
            part: dict[str, Any] | None = None
            if part_id:
                for candidate in reversed(self.text_parts):
                    if str(candidate.get("id") or "").strip() == part_id:
                        part = candidate
                        break
            elif self.text_parts:
                last_part = self.text_parts[-1]
                if not str(last_part.get("id") or "").strip() and metadata is None:
                    last_metadata = last_part.get("metadata")
                    if not isinstance(last_metadata, dict) or not last_metadata:
                        part = last_part
            if part is None:
                part = {"type": "text", "text": ""}
                if part_id:
                    part["id"] = part_id
                if metadata is not None:
                    part["metadata"] = metadata
                self.text_parts.append(part)
            part["text"] = str(part.get("text") or "") + content
            if metadata is not None:
                part["metadata"] = metadata
            return
        if event_name == "error":
            error_message = str(data.get("message") or "").strip()
            if error_message:
                self.error_message = error_message
                if error_message == "会话已中止":
                    self.cancelled = True
                if publish_bus:
                    session_bus.publish(
                        SessionBusEvent.ERROR,
                        {
                            "sessionID": session_id,
                            "message": error_message,
                        },
                    )
            return
        if event_name == "done":
            self.saw_done = True

    def absorb(self, other: PromptStreamControl | None) -> PromptStreamControl:
        if not isinstance(other, PromptStreamControl):
            return self
        self.handed_off = bool(self.handed_off or other.handed_off)
        self.saw_done = bool(self.saw_done or other.saw_done)
        self.paused = bool(self.paused or other.paused)
        self.cancelled = bool(self.cancelled or other.cancelled)
        if other.error_message:
            self.error_message = other.error_message
        if other.assistant_message_id:
            self.assistant_message_id = other.assistant_message_id
        if isinstance(other.action_confirm, dict) and other.action_confirm:
            self.action_confirm = copy.deepcopy(other.action_confirm)
        if other.text_parts:
            self.text_parts = copy.deepcopy(other.text_parts)
        return self


class PromptLifecycleSSEStream:
    def __init__(self, iterator: Iterator[str], *, control: PromptStreamControl) -> None:
        self._iterator = iter(iterator)
        self._researchos_prompt_control = control

    def __iter__(self) -> PromptLifecycleSSEStream:
        return self

    def __next__(self) -> str:
        return next(self._iterator)

    def close(self) -> None:
        close_method = getattr(self._iterator, "close", None)
        if callable(close_method):
            close_method()


@dataclass(frozen=True)
class PromptEvent:
    event: str
    data: dict[str, Any] = field(default_factory=dict)


def prompt_event(event: str, data: dict[str, Any] | None = None) -> PromptEvent:
    return PromptEvent(
        event=str(event or "").strip(),
        data=copy.deepcopy(data) if isinstance(data, dict) else {},
    )


_DELTA_PERSIST_EVENT_NAMES = {"reasoning_delta", "text_delta"}
_DELTA_PERSIST_CHAR_THRESHOLD = 512
_DELTA_PERSIST_EVENT_THRESHOLD = 24


@dataclass
class _DeltaPersistenceBuffer:
    processor: Any
    active_event_name: str | None = None
    active_part_id: str | None = None
    active_metadata: dict[str, Any] | None = None
    active_first_persisted: bool = False
    event_name: str | None = None
    part_id: str | None = None
    metadata: dict[str, Any] | None = None
    chunks: list[str] = field(default_factory=list)
    chars: int = 0
    events: int = 0

    @staticmethod
    def can_buffer(event_name: str | None, data: dict[str, Any]) -> bool:
        if event_name not in _DELTA_PERSIST_EVENT_NAMES:
            return False
        return bool(str(data.get("content") or ""))

    @staticmethod
    def _part_id(data: dict[str, Any]) -> str | None:
        return runtime._clean_text(data.get("id")) or None

    @staticmethod
    def _metadata(data: dict[str, Any]) -> dict[str, Any] | None:
        metadata = data.get("metadata")
        return copy.deepcopy(metadata) if isinstance(metadata, dict) and metadata else None

    def _matches(
        self, event_name: str, part_id: str | None, metadata: dict[str, Any] | None
    ) -> bool:
        return (
            self.active_event_name == event_name
            and self.active_part_id == part_id
            and self.active_metadata == metadata
        )

    def _clear_pending(self) -> None:
        self.event_name = None
        self.part_id = None
        self.metadata = None
        self.chunks = []
        self.chars = 0
        self.events = 0

    def _reset_active(
        self, event_name: str, part_id: str | None, metadata: dict[str, Any] | None
    ) -> None:
        self.active_event_name = event_name
        self.active_part_id = part_id
        self.active_metadata = copy.deepcopy(metadata) if isinstance(metadata, dict) else None
        self.active_first_persisted = False

    def push(self, event_name: str, data: dict[str, Any]) -> list[PromptEvent]:
        content = str(data.get("content") or "")
        if not content:
            return []
        part_id = self._part_id(data)
        metadata = self._metadata(data)
        emitted: list[PromptEvent] = []
        if self.active_event_name is None or not self._matches(event_name, part_id, metadata):
            emitted.extend(self.flush())
            self._reset_active(event_name, part_id, metadata)
        if not self.chunks:
            self.event_name = event_name
            self.part_id = part_id
            self.metadata = copy.deepcopy(metadata) if isinstance(metadata, dict) else None
        self.chunks.append(content)
        self.chars += len(content)
        self.events += 1
        if not self.active_first_persisted:
            self.active_first_persisted = True
            emitted.extend(self.flush())
        elif (
            self.chars >= _DELTA_PERSIST_CHAR_THRESHOLD
            or self.events >= _DELTA_PERSIST_EVENT_THRESHOLD
        ):
            emitted.extend(self.flush())
        return emitted

    def flush(self) -> list[PromptEvent]:
        event_name = self.event_name
        if not event_name or not self.chunks:
            self._clear_pending()
            return []
        if event_name == "reasoning_delta":
            content = ""
            for chunk in self.chunks:
                content = session_message_v2.append_reasoning_fragment(content, chunk)
        else:
            content = "".join(self.chunks)
        payload: dict[str, Any] = {"content": content}
        if self.part_id:
            payload["id"] = self.part_id
        if isinstance(self.metadata, dict) and self.metadata:
            payload["metadata"] = copy.deepcopy(self.metadata)
        self._clear_pending()
        return [
            prompt_event(name, payload)
            for name, payload in self.processor.apply_event(event_name, payload)
        ]


def coerce_prompt_event(item: PromptEvent | str) -> tuple[str | None, dict[str, Any], str]:
    if isinstance(item, PromptEvent):
        data = copy.deepcopy(item.data) if isinstance(item.data, dict) else {}
        return item.event, data, runtime._format_sse_event(item.event, data)
    parsed = runtime._parse_sse_event(str(item or ""))
    if parsed is None:
        return None, {}, str(item)
    event_name, data = parsed
    return event_name, data, str(item)


def serialize_prompt_event_stream(raw_stream: Iterator[PromptEvent | str]) -> Iterator[str]:
    iterator = iter(raw_stream)
    while True:
        try:
            item = next(iterator)
        except StopIteration as stop:
            return stop.value
        _event_name, _data, serialized = coerce_prompt_event(item)
        yield serialized


class PromptEventStreamDriver:
    def __init__(
        self,
        *,
        control: PromptStreamControl,
        session_id: str,
        lifecycle_kind: str,
        step_index: int,
    ) -> None:
        self.control = control
        self.session_id = session_id
        self.lifecycle_kind = lifecycle_kind
        self.step_index = step_index

    def emit_raw(
        self,
        item: PromptEvent | str,
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        event_name, data, serialized = coerce_prompt_event(item)
        if not event_name:
            yield serialized
            return
        yield from self.emit_event(event_name, data, publish_bus=publish_bus)
        yield serialized

    def emit_event(
        self,
        event_name: str,
        data: dict[str, Any],
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        self.control.observe_event(
            event_name,
            data,
            session_id=self.session_id,
            lifecycle_kind=self.lifecycle_kind,
            step_index=self.step_index,
            publish_bus=publish_bus,
        )
        if False:
            yield ""


@dataclass(frozen=True)
class PromptLifecycleConfig:
    session_id: str
    processor_session_id: str | None
    parent_id: str | None
    assistant_meta: dict[str, Any] | None
    assistant_message_id: str | None
    lifecycle_kind: str
    step_index: int


class PromptLifecycleSession:
    def __init__(
        self,
        *,
        control: PromptStreamControl,
        session_processor: SessionProcessor | None,
        event_driver: PromptEventStreamDriver,
    ) -> None:
        self.control = control
        self.session_processor = session_processor
        self.event_driver = event_driver
        self._delta_persistence_buffer = (
            _DeltaPersistenceBuffer(session_processor) if session_processor is not None else None
        )

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        processor_session_id: str | None,
        parent_id: str | None,
        assistant_meta: dict[str, Any] | None,
        assistant_message_id: str | None,
        lifecycle_kind: str,
        step_index: int,
    ) -> PromptLifecycleSession:
        control = PromptStreamControl(
            assistant_message_id=runtime._clean_text(assistant_message_id) or None,
        )
        normalized_processor_session_id = runtime._clean_text(processor_session_id)
        session_processor = (
            SessionProcessor(
                session_id=normalized_processor_session_id,
                parent_id=parent_id,
                assistant_meta=assistant_meta,
                assistant_message_id=assistant_message_id,
            )
            if normalized_processor_session_id
            else None
        )
        event_driver = PromptEventStreamDriver(
            control=control,
            session_id=runtime._clean_text(session_id) or "",
            lifecycle_kind=lifecycle_kind,
            step_index=int(step_index),
        )
        return cls(
            control=control,
            session_processor=session_processor,
            event_driver=event_driver,
        )

    def persist_prompt_item(
        self,
        item: PromptEvent | str,
    ) -> list[PromptEvent | str]:
        if self.session_processor is None:
            return [item]
        event_name, data, serialized = coerce_prompt_event(item)
        if not event_name:
            return [serialized]
        return [
            *[
                prompt_event(name, payload)
                for name, payload in self.session_processor.apply_event(event_name, data)
            ],
            prompt_event(event_name, data),
        ]

    def _flush_delta_persistence(self) -> list[PromptEvent]:
        if self._delta_persistence_buffer is None:
            return []
        return self._delta_persistence_buffer.flush()

    def _emit_delta_persistence_flush(self, *, publish_bus: bool = False) -> Iterator[str]:
        for emitted in self._flush_delta_persistence():
            yield from self.event_driver.emit_raw(emitted, publish_bus=publish_bus)

    def _flush_delta_persistence_silent(self) -> None:
        self._flush_delta_persistence()

    def emit_item(
        self,
        item: PromptEvent | str,
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        event_name, data, serialized = coerce_prompt_event(item)
        if (
            self._delta_persistence_buffer is not None
            and event_name is not None
            and _DeltaPersistenceBuffer.can_buffer(event_name, data)
        ):
            for emitted in self._delta_persistence_buffer.push(event_name, data):
                yield from self.event_driver.emit_raw(emitted, publish_bus=publish_bus)
            yield from self.event_driver.emit_raw(
                prompt_event(event_name, data),
                publish_bus=publish_bus,
            )
            return
        yield from self._emit_delta_persistence_flush(publish_bus=publish_bus)
        if event_name is None:
            yield from self.event_driver.emit_raw(serialized, publish_bus=publish_bus)
            return
        for emitted in self.persist_prompt_item(item):
            yield from self.event_driver.emit_raw(emitted, publish_bus=publish_bus)

    def emit_event(
        self,
        event_name: str,
        data: dict[str, Any],
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        yield from self.emit_item(
            prompt_event(event_name, data),
            publish_bus=publish_bus,
        )

    def emit_done(self, *, publish_bus: bool = False) -> Iterator[str]:
        yield from self.emit_event("done", {}, publish_bus=publish_bus)

    def emit_stream(
        self,
        raw_stream: Iterator[PromptEvent | str],
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        iterator = iter(raw_stream)
        while True:
            try:
                item = next(iterator)
            except StopIteration as stop:
                return stop.value
            yield from self.emit_item(item, publish_bus=publish_bus)

    def observe_stream(
        self,
        stream: Iterator[str],
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        iterator = iter(stream)
        while True:
            try:
                item = next(iterator)
            except StopIteration as stop:
                return stop.value
            yield from self.emit_item(item, publish_bus=publish_bus)

    def observe_prompt_events(
        self,
        stream: Iterator[PromptEvent],
        *,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        iterator = iter(stream)
        while True:
            try:
                item = next(iterator)
            except StopIteration as stop:
                return stop.value
            yield from self.emit_item(item, publish_bus=publish_bus)

    def stream(
        self,
        run: Callable[[], Iterator[str]],
        *,
        on_start: Callable[[PromptLifecycleSession], None] | None = None,
        on_exception: Callable[[PromptLifecycleSession, Exception], None] | None = None,
        on_finalize: Callable[[PromptLifecycleSession], None] | None = None,
    ) -> PromptLifecycleSSEStream:
        def _iterate() -> Iterator[str]:
            if self.session_processor is not None:
                self.session_processor.start()
            if callable(on_start):
                on_start(self)
            try:
                yield from run()
                yield from self._emit_delta_persistence_flush()
            except GeneratorExit:
                self._flush_delta_persistence_silent()
                raise
            except Exception as exc:
                self._flush_delta_persistence_silent()
                if callable(on_exception):
                    on_exception(self, exc)
                raise
            finally:
                self._flush_delta_persistence_silent()
                if callable(on_finalize):
                    on_finalize(self)
                if self.session_processor is not None:
                    self.session_processor.finalize(
                        manage_lifecycle=False,
                        handed_off=bool(self.control.handed_off),
                    )

        return PromptLifecycleSSEStream(_iterate(), control=self.control)


def stream_prompt_lifecycle(
    *,
    config: PromptLifecycleConfig,
    run: Callable[[PromptLifecycleSession], Iterator[str]],
    on_start: Callable[[PromptLifecycleSession], None] | None = None,
    on_exception: Callable[[PromptLifecycleSession, Exception], None] | None = None,
    on_finalize: Callable[[PromptLifecycleSession], None] | None = None,
) -> PromptLifecycleSSEStream:
    lifecycle = PromptLifecycleSession.create(
        session_id=config.session_id,
        processor_session_id=config.processor_session_id,
        parent_id=config.parent_id,
        assistant_meta=copy.deepcopy(config.assistant_meta)
        if isinstance(config.assistant_meta, dict)
        else None,
        assistant_message_id=config.assistant_message_id,
        lifecycle_kind=config.lifecycle_kind,
        step_index=config.step_index,
    )

    def _iterate() -> Iterator[str]:
        yield from run(lifecycle)

    return lifecycle.stream(
        _iterate,
        on_start=on_start,
        on_exception=on_exception,
        on_finalize=on_finalize,
    )


@dataclass
class PromptLoopCallbacks:
    emit_event: Callable[..., Iterator[str]]
    emit_stream: Callable[..., Iterator[str]]
    emit_done: Callable[..., Iterator[str]]
    publish_step_start: Callable[[int], None] | None
    prepare_loop_messages: Callable[[list[dict[str, Any]], int, int], list[dict[str, Any]]]
    run_model_turn: Callable[[list[dict[str, Any]]], Iterator[Any]]
    process_tool_calls: Callable[
        [list[dict[str, Any]], list[Any], int, str | None, dict[str, Any] | None, str],
        Iterator[Any],
    ]
    step_limit_summary: Callable[[list[dict[str, Any]], int], Iterator[Any]]
    emit_step_finish: Callable[[int, str, dict[str, Any] | None, str | None], Iterator[Any]]
    build_assistant_message: Callable[..., dict[str, Any]]
    build_tool_message: Callable[[Any, Any], dict[str, Any]]
    assistant_checkpoint_meta: Callable[[str, dict[str, Any] | None], dict[str, Any]]
    resolve_model_identity: Callable[[], dict[str, str]]
    usage_to_tokens: Callable[[dict[str, Any] | None], int]
    is_overflow_tokens: Callable[[int, str, str], bool]
    normalize_messages: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    next_assistant_message_id: Callable[[], str]
    make_tool_result: Callable[[bool, str, Any], Any]
    auto_compact: Callable[[str, str, bool], tuple[list[dict[str, Any]], str | None]] | None = None
    latest_auto_compaction_target: Callable[[], dict[str, Any] | None] | None = None
    load_persisted_messages: Callable[[], list[dict[str, Any]] | None] | None = None
    snapshot_root: Callable[[], str | None] | None = None
    capture_snapshot: Callable[[str], str | None] | None = None
    on_warning: Callable[[str, Exception], None] | None = None
    max_auto_compaction_attempts: int = 4


@dataclass
class ModelTurnResult:
    status: str
    content: str = ""
    text_parts: list[dict[str, Any]] = field(default_factory=list)
    reasoning_content: str = ""
    reasoning_parts: list[dict[str, Any]] = field(default_factory=list)
    provider_metadata: dict[str, Any] | None = None
    tool_calls: list[Any] = field(default_factory=list)
    assistant_tool_calls: list[Any] = field(default_factory=list)
    tool_messages: list[dict[str, Any]] = field(default_factory=list)
    error_message: str | None = None
    usage: dict[str, Any] | None = None
    error_emitted: bool = False


@dataclass(frozen=True)
class ModelTurnRuntimeConfig:
    messages: list[dict[str, Any]]
    llm: Any
    options: Any
    latest_user_tools: dict[str, bool] | None
    disabled_tools: set[str]
    output_constraint: Any | None
    max_attempts: int


@dataclass
class ModelTurnRuntimeCallbacks:
    build_turn_tools: Callable[[Any, Any, set[str], dict[str, bool] | None], list[dict[str, Any]]]
    parse_tool_call: Callable[..., Any]
    fill_workspace_defaults: Callable[[Any, Any], Any]
    build_tool_message: Callable[[Any, Any], dict[str, Any]]
    make_tool_result: Callable[[bool, str, Any], Any]
    repair_output_constraint_text: Callable[[Any, str, Any, Any], str]
    iter_text_chunks: Callable[[str], Iterator[str]]
    detect_overflow_error: Callable[[str], bool]
    retryable: Callable[[Any], str | None]
    retry_delay_ms: Callable[[int, dict[str, Any]], int]
    sleep_retry: Callable[[str, int], bool]
    set_session_status: Callable[[str, dict[str, Any]], None]
    tool_only_response: Callable[[list[dict[str, Any]]], str] | None = None
    is_session_aborted: Callable[[str], bool] | None = None
    on_exception: Callable[[Exception], None] | None = None


@dataclass(frozen=True)
class ToolExecutionConfig:
    call: Any
    options: Any


@dataclass
class ToolExecutionCallbacks:
    make_tool_context: Callable[[Any], Any]
    execute_tool_stream: Callable[[str, dict[str, Any], Any], Iterator[Any]]
    is_progress_event: Callable[[Any], bool]
    make_tool_result: Callable[[bool, str, Any], Any]


@dataclass(frozen=True)
class ToolPendingActionConfig:
    action_id: str
    options: Any
    decision: Any
    messages: list[dict[str, Any]]
    remaining_calls: list[Any]
    step_index: int
    step_snapshot: str | None
    step_usage: dict[str, Any] | None
    assistant_message_id: str | None


@dataclass(frozen=True)
class ToolCallProcessingConfig:
    messages: list[dict[str, Any]]
    tool_calls: list[Any]
    step_index: int
    options: Any
    session_record: Any
    skip_first_confirmation: bool = False
    step_snapshot: str | None = None
    step_usage: dict[str, Any] | None = None
    assistant_message_id: str | None = None
    rotate_message_on_pause_after_progress: bool = False


@dataclass
class ToolCallProcessingCallbacks:
    get_tool_definition: Callable[[str], Any]
    authorize_tool_call: Callable[..., Any]
    permission_manages_tool: Callable[[str], bool]
    requires_confirmation: Callable[[Any, Any, Any], bool]
    create_pending_action: Callable[[ToolPendingActionConfig], Any]
    store_pending_action: Callable[[Any], None]
    summarize_action: Callable[[Any], str]
    build_tool_message: Callable[[Any, Any], dict[str, Any]]
    execute_tool: Callable[[Any, Any], Iterator[PromptEvent]]
    make_tool_result: Callable[[bool, str, Any], Any]
    emit_step_finish: (
        Callable[[int, str, dict[str, Any] | None, str | None], Iterator[PromptEvent]] | None
    ) = None
    assistant_checkpoint_meta: Callable[[str, dict[str, Any] | None], dict[str, Any]] | None = None
    next_assistant_message_id: Callable[[], str] | None = None


def _runtime_part_id() -> str:
    return f"part_{uuid4().hex}"


def _runtime_item_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _runtime_set_item_value(value: Any, name: str, item: Any) -> None:
    if isinstance(value, dict):
        value[name] = item
        return
    setattr(value, name, item)


def stream_model_turn_events(
    config: ModelTurnRuntimeConfig,
    callbacks: ModelTurnRuntimeCallbacks,
) -> Iterator[PromptEvent]:
    attempt = 0

    while True:
        assistant_parts: list[str] = []
        text_part_records: list[dict[str, Any]] = []
        text_part_by_id: dict[str, dict[str, Any]] = {}
        reasoning_parts: list[str] = []
        reasoning_part_records: list[dict[str, Any]] = []
        reasoning_part_by_id: dict[str, dict[str, Any]] = {}
        assistant_tool_calls: list[Any] = []
        tool_messages: list[dict[str, Any]] = []
        latest_usage: dict[str, Any] | None = None
        delay_content_stream = config.output_constraint is not None
        open_content_part: str | None = None
        open_content_part_id: str | None = None
        open_content_part_metadata: dict[str, Any] | None = None
        buffered_mirrored_text: list[tuple[str, str | None, dict[str, Any] | None]] = []
        buffered_mirrored_text_content = ""
        buffered_tool_preamble: list[tuple[str, str | None, dict[str, Any] | None]] = []
        buffered_tool_preamble_content = ""
        session_id = runtime._clean_text(getattr(config.options, "session_id", None))

        def _abort_requested() -> bool:
            if not session_id or not callable(callbacks.is_session_aborted):
                return False
            try:
                return bool(callbacks.is_session_aborted(session_id))
            except Exception:
                return False

        def _close_stream(stream_obj: Any) -> None:
            close = getattr(stream_obj, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

        def _abort_turn(stream_obj: Any) -> Iterator[PromptEvent]:
            if buffered_mirrored_text:
                _drop_buffered_mirrored_text()
            for payload in _transition_content_part(None):
                yield payload
            _close_stream(stream_obj)
            yield prompt_event("error", {"message": "会话已中止"})

        if attempt > 0 and session_id:
            callbacks.set_session_status(session_id, {"type": "busy"})

        def _transition_content_part(
            next_part: str | None,
            *,
            preferred_id: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> list[PromptEvent]:
            nonlocal open_content_part, open_content_part_id, open_content_part_metadata
            payloads: list[PromptEvent] = []
            normalized_next = next_part if next_part in {"text", "reasoning"} else None
            normalized_id = str(preferred_id or "").strip() or None
            should_close = False
            if open_content_part and open_content_part != normalized_next:
                should_close = True
            if (
                open_content_part
                and normalized_next
                and open_content_part == normalized_next
                and normalized_id
                and open_content_part_id
                and open_content_part_id != normalized_id
            ):
                should_close = True
            if open_content_part and should_close:
                payloads.append(
                    prompt_event(
                        f"{open_content_part}-end",
                        {"id": open_content_part_id} if open_content_part_id else {},
                    )
                )
                open_content_part = None
                open_content_part_id = None
                open_content_part_metadata = None
            if normalized_next and open_content_part != normalized_next:
                open_content_part = normalized_next
                open_content_part_id = normalized_id or _runtime_part_id()
                open_content_part_metadata = (
                    copy.deepcopy(metadata) if isinstance(metadata, dict) and metadata else None
                )
                start_payload: dict[str, Any] = {"id": open_content_part_id}
                if open_content_part_metadata:
                    start_payload["metadata"] = copy.deepcopy(open_content_part_metadata)
                payloads.append(prompt_event(f"{normalized_next}-start", start_payload))
            elif normalized_next and normalized_id and not open_content_part_id:
                open_content_part_id = normalized_id
            if (
                normalized_next
                and isinstance(metadata, dict)
                and metadata
                and not isinstance(open_content_part_metadata, dict)
            ):
                open_content_part_metadata = copy.deepcopy(metadata)
            return payloads

        def _remember_reasoning_part(
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            if not open_content_part_id:
                return None
            record = reasoning_part_by_id.get(open_content_part_id)
            if record is None:
                record = {
                    "id": open_content_part_id,
                    "text": "",
                }
                if isinstance(metadata, dict) and metadata:
                    record["metadata"] = copy.deepcopy(metadata)
                reasoning_part_by_id[open_content_part_id] = record
                reasoning_part_records.append(record)
            elif (
                isinstance(metadata, dict)
                and metadata
                and not isinstance(record.get("metadata"), dict)
            ):
                record["metadata"] = copy.deepcopy(metadata)
            return record

        def _remember_text_part(metadata: dict[str, Any] | None = None) -> dict[str, Any] | None:
            if not open_content_part_id:
                return None
            record = text_part_by_id.get(open_content_part_id)
            if record is None:
                record = {
                    "id": open_content_part_id,
                    "text": "",
                }
                if isinstance(metadata, dict) and metadata:
                    record["metadata"] = copy.deepcopy(metadata)
                text_part_by_id[open_content_part_id] = record
                text_part_records.append(record)
            elif (
                isinstance(metadata, dict)
                and metadata
                and not isinstance(record.get("metadata"), dict)
            ):
                record["metadata"] = copy.deepcopy(metadata)
            return record

        def _find_tool_call(call_id: str) -> Any | None:
            normalized = str(call_id or "").strip()
            if not normalized:
                return None
            for call in assistant_tool_calls:
                if str(_runtime_item_value(call, "id", "") or "") == normalized:
                    return call
            return None

        def _emit_text_delta(
            content: str,
            *,
            preferred_id: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> Iterator[PromptEvent]:
            for payload in _transition_content_part(
                "text",
                preferred_id=preferred_id,
                metadata=metadata,
            ):
                yield payload
            assistant_parts.append(content)
            record = _remember_text_part(
                metadata if isinstance(metadata, dict) else open_content_part_metadata,
            )
            if record is not None:
                record["text"] = str(record.get("text") or "") + content
            delta_payload: dict[str, Any] = {
                "id": open_content_part_id,
                "content": content,
            }
            if isinstance(metadata, dict) and metadata:
                delta_payload["metadata"] = copy.deepcopy(metadata)
            yield prompt_event("text_delta", delta_payload)

        def _should_buffer_mirrored_text(content: str) -> bool:
            if (
                not content
                or delay_content_stream
                or assistant_parts
                or text_part_records
                or assistant_tool_calls
            ):
                return False
            reasoning_content = session_message_v2.merge_reasoning_fragments(reasoning_parts)
            if not reasoning_content:
                return False
            return reasoning_content.startswith(buffered_mirrored_text_content + content)

        def _flush_buffered_mirrored_text() -> Iterator[PromptEvent]:
            nonlocal buffered_mirrored_text, buffered_mirrored_text_content
            pending = buffered_mirrored_text
            buffered_mirrored_text = []
            buffered_mirrored_text_content = ""
            for content, preferred_id, metadata in pending:
                yield from _emit_text_delta(
                    content,
                    preferred_id=preferred_id,
                    metadata=metadata,
                )

        def _drop_buffered_mirrored_text() -> None:
            nonlocal buffered_mirrored_text, buffered_mirrored_text_content
            buffered_mirrored_text = []
            buffered_mirrored_text_content = ""

        def _can_buffer_tool_preamble(candidate: str) -> bool:
            return bool(
                candidate
                and not delay_content_stream
                and not assistant_parts
                and not text_part_records
                and not assistant_tool_calls
                and not tool_messages
                and not buffered_mirrored_text
                and _shared_is_tool_progress_placeholder_text(candidate)
            )

        def _flush_buffered_tool_preamble() -> Iterator[PromptEvent]:
            nonlocal buffered_tool_preamble, buffered_tool_preamble_content
            pending = buffered_tool_preamble
            buffered_tool_preamble = []
            buffered_tool_preamble_content = ""
            for content, preferred_id, metadata in pending:
                yield from _emit_text_delta(
                    content,
                    preferred_id=preferred_id,
                    metadata=metadata,
                )

        def _drop_buffered_tool_preamble() -> None:
            nonlocal buffered_tool_preamble, buffered_tool_preamble_content
            buffered_tool_preamble = []
            buffered_tool_preamble_content = ""

        try:
            stream = _llm_chat_stream_with_compat(
                config.llm,
                config.messages,
                tools=callbacks.build_turn_tools(
                    config.llm,
                    config.options,
                    config.disabled_tools,
                    config.latest_user_tools,
                ),
                variant_override=getattr(config.options, "reasoning_level", None),
                model_override=str(getattr(config.options, "model_override", "") or "").strip()
                or None,
                session_cache_key=session_id or None,
            )
            for event in stream:
                if _abort_requested():
                    yield from _abort_turn(stream)
                    return ModelTurnResult(
                        status="error", error_message="会话已中止", error_emitted=True
                    )
                event_type = str(getattr(event, "type", "") or "")
                event_metadata = getattr(event, "metadata", None)
                event_content = str(getattr(event, "content", "") or "")
                if event_type == "text_delta" and event_content:
                    if delay_content_stream:
                        assistant_parts.append(event_content)
                        continue
                    normalized_metadata = (
                        event_metadata if isinstance(event_metadata, dict) else None
                    )
                    preferred_id = getattr(event, "part_id", None)
                    buffered_tool_candidate = buffered_tool_preamble_content + event_content
                    if _can_buffer_tool_preamble(buffered_tool_candidate):
                        buffered_tool_preamble.append(
                            (
                                event_content,
                                preferred_id,
                                copy.deepcopy(normalized_metadata),
                            )
                        )
                        buffered_tool_preamble_content = buffered_tool_candidate
                        continue
                    if buffered_tool_preamble:
                        yield from _flush_buffered_tool_preamble()
                    if _should_buffer_mirrored_text(event_content):
                        buffered_mirrored_text.append(
                            (
                                event_content,
                                preferred_id,
                                copy.deepcopy(normalized_metadata),
                            )
                        )
                        buffered_mirrored_text_content += event_content
                        continue
                    if buffered_mirrored_text:
                        yield from _flush_buffered_mirrored_text()
                    yield from _emit_text_delta(
                        event_content,
                        preferred_id=preferred_id,
                        metadata=normalized_metadata,
                    )
                elif event_type == "reasoning_delta" and (
                    event_content or getattr(event, "part_id", None) or event_metadata
                ):
                    if delay_content_stream:
                        if event_content:
                            reasoning_parts.append(event_content)
                        continue
                    for payload in _transition_content_part(
                        "reasoning",
                        preferred_id=getattr(event, "part_id", None),
                        metadata=event_metadata if isinstance(event_metadata, dict) else None,
                    ):
                        yield payload
                    if event_content:
                        reasoning_parts.append(event_content)
                    record = _remember_reasoning_part(
                        event_metadata
                        if isinstance(event_metadata, dict)
                        else open_content_part_metadata,
                    )
                    if record is not None and event_content:
                        record["text"] = session_message_v2.append_reasoning_fragment(
                            str(record.get("text") or ""),
                            event_content,
                        )
                    delta_payload = {
                        "id": open_content_part_id,
                        "content": event_content,
                    }
                    if isinstance(event_metadata, dict) and event_metadata:
                        delta_payload["metadata"] = copy.deepcopy(event_metadata)
                    elif (
                        isinstance(open_content_part_metadata, dict) and open_content_part_metadata
                    ):
                        delta_payload["metadata"] = copy.deepcopy(open_content_part_metadata)
                    yield prompt_event("reasoning_delta", delta_payload)
                elif event_type == "tool_call":
                    if buffered_tool_preamble:
                        _drop_buffered_tool_preamble()
                    if buffered_mirrored_text:
                        _drop_buffered_mirrored_text()
                    for payload in _transition_content_part(None):
                        yield payload
                    call = callbacks.parse_tool_call(
                        getattr(event, "tool_call_id", None),
                        getattr(event, "tool_name", None),
                        getattr(event, "tool_arguments", None),
                        metadata=event_metadata,
                        provider_executed=bool(getattr(event, "provider_executed", False)),
                    )
                    assistant_tool_calls.append(call)
                    tool_input_start_payload = {
                        "id": _runtime_item_value(call, "id", ""),
                        "toolName": _runtime_item_value(call, "name", ""),
                    }
                    if bool(_runtime_item_value(call, "provider_executed", False)):
                        tool_input_start_payload["providerExecuted"] = True
                    yield prompt_event("tool-input-start", tool_input_start_payload)
                    tool_arguments = str(getattr(event, "tool_arguments", "") or "")
                    if tool_arguments:
                        tool_input_delta_payload = {
                            "id": _runtime_item_value(call, "id", ""),
                            "delta": tool_arguments,
                        }
                        if bool(_runtime_item_value(call, "provider_executed", False)):
                            tool_input_delta_payload["providerExecuted"] = True
                        yield prompt_event("tool-input-delta", tool_input_delta_payload)
                    tool_input_end_payload = {
                        "id": _runtime_item_value(call, "id", ""),
                    }
                    if bool(_runtime_item_value(call, "provider_executed", False)):
                        tool_input_end_payload["providerExecuted"] = True
                    yield prompt_event("tool-input-end", tool_input_end_payload)
                elif event_type == "tool_result" and bool(
                    getattr(event, "provider_executed", False)
                ):
                    if buffered_tool_preamble:
                        _drop_buffered_tool_preamble()
                    if buffered_mirrored_text:
                        yield from _flush_buffered_mirrored_text()
                    for payload in _transition_content_part(None):
                        yield payload
                    call = _find_tool_call(str(getattr(event, "tool_call_id", "") or ""))
                    if call is None:
                        call = callbacks.parse_tool_call(
                            getattr(event, "tool_call_id", None),
                            getattr(event, "tool_name", None),
                            "",
                            metadata=event_metadata,
                            provider_executed=True,
                        )
                        assistant_tool_calls.append(call)
                    result = callbacks.make_tool_result(
                        bool(
                            True
                            if getattr(event, "tool_success", None) is None
                            else getattr(event, "tool_success", None)
                        ),
                        str(getattr(event, "tool_summary", "") or ""),
                        copy.deepcopy(getattr(event, "tool_result", None)),
                    )
                    payload = {
                        "id": _runtime_item_value(call, "id", ""),
                        "name": _runtime_item_value(call, "name", ""),
                        "success": bool(_runtime_item_value(result, "success", True)),
                        "summary": _runtime_item_value(result, "summary", ""),
                        "data": copy.deepcopy(_runtime_item_value(result, "data", None)),
                        "providerExecuted": True,
                    }
                    call_metadata = _runtime_item_value(call, "metadata", None)
                    if isinstance(call_metadata, dict) and call_metadata:
                        payload["metadata"] = copy.deepcopy(call_metadata)
                    yield prompt_event("tool_result", payload)
                    tool_messages.append(callbacks.build_tool_message(call, result))
                elif event_type == "usage":
                    if buffered_mirrored_text:
                        yield from _flush_buffered_mirrored_text()
                    for payload in _transition_content_part(None):
                        yield payload
                    latest_usage = {
                        "model": getattr(event, "model", None),
                        "input_tokens": int(getattr(event, "input_tokens", 0) or 0),
                        "output_tokens": int(getattr(event, "output_tokens", 0) or 0),
                        "reasoning_tokens": int(getattr(event, "reasoning_tokens", 0) or 0),
                    }
                    if isinstance(event_metadata, dict) and event_metadata:
                        latest_usage["metadata"] = copy.deepcopy(event_metadata)
                    yield prompt_event(
                        "usage",
                        {
                            "model": getattr(event, "model", None),
                            "input_tokens": int(getattr(event, "input_tokens", 0) or 0),
                            "output_tokens": int(getattr(event, "output_tokens", 0) or 0),
                            "reasoning_tokens": int(getattr(event, "reasoning_tokens", 0) or 0),
                            "metadata": copy.deepcopy(event_metadata)
                            if isinstance(event_metadata, dict) and event_metadata
                            else None,
                        },
                    )
                elif event_type == "error":
                    if buffered_mirrored_text:
                        _drop_buffered_mirrored_text()
                    message = event_content or "对话请求失败"
                    error_value: Any = message
                    if isinstance(event_metadata, dict) and event_metadata:
                        error_value = {"message": message, **copy.deepcopy(event_metadata)}
                    error_payload = normalize_error(error_value)
                    if (
                        not assistant_parts
                        and not assistant_tool_calls
                        and (
                            str(error_payload.get("name") or "") == "ContextOverflowError"
                            or callbacks.detect_overflow_error(message)
                        )
                    ):
                        return ModelTurnResult(status="compact", error_message=message)
                    retry_message = (
                        callbacks.retryable(error_value)
                        if not assistant_parts
                        and not assistant_tool_calls
                        and attempt < config.max_attempts
                        else None
                    )
                    if retry_message is not None:
                        attempt += 1
                        delay_ms = callbacks.retry_delay_ms(attempt, error_payload)
                        yield prompt_event(
                            "session_retry",
                            {
                                "attempt": attempt,
                                "message": retry_message,
                                "delay_ms": delay_ms,
                                "error": error_payload,
                            },
                        )
                        if session_id:
                            callbacks.set_session_status(
                                session_id,
                                {
                                    "type": "retry",
                                    "attempt": attempt,
                                    "message": retry_message,
                                    "next": runtime._now_ms() + delay_ms,
                                },
                            )
                        if not callbacks.sleep_retry(session_id, delay_ms):
                            return ModelTurnResult(status="error", error_message="会话已中止")
                        break
                    for payload in _transition_content_part(None):
                        yield payload
                    yield prompt_event("error", {"message": message})
                    return ModelTurnResult(
                        status="error", error_message=message, error_emitted=True
                    )
                if _abort_requested():
                    yield from _abort_turn(stream)
                    return ModelTurnResult(
                        status="error", error_message="会话已中止", error_emitted=True
                    )
            else:
                if buffered_tool_preamble and not assistant_tool_calls:
                    yield from _flush_buffered_tool_preamble()
                elif buffered_tool_preamble:
                    _drop_buffered_tool_preamble()
                if buffered_mirrored_text and not assistant_tool_calls:
                    yield from _flush_buffered_mirrored_text()
                elif buffered_mirrored_text:
                    _drop_buffered_mirrored_text()
                for payload in _transition_content_part(None):
                    yield payload
                content = "".join(assistant_parts)
                reasoning_content = session_message_v2.merge_reasoning_fragments(reasoning_parts)
                executable_tool_calls = [
                    callbacks.fill_workspace_defaults(call, config.options)
                    for call in assistant_tool_calls
                    if not bool(_runtime_item_value(call, "provider_executed", False))
                ]
                tool_followup = _shared_resolve_tool_result_followup_text(
                    content,
                    _shared_collect_tool_result_items_from_messages(
                        copy.deepcopy(tool_messages),
                        limit=5,
                        include_skipped=True,
                    ),
                )
                synthesized_tool_only_content = False
                appended_tool_followup_summary = ""
                content = tool_followup.final_text
                synthesized_tool_only_content = bool(tool_followup.synthesized and content)
                if tool_followup.appended_summary and tool_followup.summary_text:
                    appended_tool_followup_summary = tool_followup.summary_text
                if delay_content_stream and content:
                    if not executable_tool_calls:
                        content = callbacks.repair_output_constraint_text(
                            config.llm,
                            content,
                            config.output_constraint,
                            config.options,
                        )
                    content_part_id = _runtime_part_id()
                    yield prompt_event("text-start", {"id": content_part_id})
                    for chunk in callbacks.iter_text_chunks(content):
                        yield prompt_event(
                            "text_delta",
                            {
                                "id": content_part_id,
                                "content": chunk,
                            },
                        )
                    yield prompt_event("text-end", {"id": content_part_id})
                elif synthesized_tool_only_content:
                    content_part_id = _runtime_part_id()
                    yield prompt_event("text-start", {"id": content_part_id})
                    for chunk in callbacks.iter_text_chunks(content):
                        yield prompt_event(
                            "text_delta",
                            {
                                "id": content_part_id,
                                "content": chunk,
                            },
                        )
                    yield prompt_event("text-end", {"id": content_part_id})
                elif appended_tool_followup_summary:
                    content_part_id = _runtime_part_id()
                    yield prompt_event("text-start", {"id": content_part_id})
                    for chunk in callbacks.iter_text_chunks(
                        f"\n\n{appended_tool_followup_summary}"
                    ):
                        yield prompt_event(
                            "text_delta",
                            {
                                "id": content_part_id,
                                "content": chunk,
                            },
                        )
                    yield prompt_event("text-end", {"id": content_part_id})
                serialized_text_parts = [
                    copy.deepcopy(item)
                    for item in text_part_records
                    if isinstance(item.get("metadata"), dict) and item["metadata"]
                ]
                provider_metadata = None
                if isinstance((latest_usage or {}).get("metadata"), dict):
                    provider_metadata = copy.deepcopy(latest_usage["metadata"])
                return ModelTurnResult(
                    status="continue",
                    content=content,
                    text_parts=serialized_text_parts,
                    reasoning_content=reasoning_content,
                    reasoning_parts=copy.deepcopy(reasoning_part_records),
                    provider_metadata=provider_metadata,
                    tool_calls=executable_tool_calls,
                    assistant_tool_calls=copy.deepcopy(assistant_tool_calls),
                    tool_messages=copy.deepcopy(tool_messages),
                    usage=copy.deepcopy(latest_usage),
                )
            continue
        except Exception as exc:  # pragma: no cover - defensive path
            if callable(callbacks.on_exception):
                callbacks.on_exception(exc)
            error_payload = normalize_error(exc)
            message = str(error_payload.get("message") or str(exc))
            if (
                not assistant_parts
                and not assistant_tool_calls
                and (
                    str(error_payload.get("name") or "") == "ContextOverflowError"
                    or callbacks.detect_overflow_error(message)
                )
            ):
                return ModelTurnResult(status="compact", error_message=message)
            retry_message = (
                callbacks.retryable(exc)
                if not assistant_parts
                and not assistant_tool_calls
                and attempt < config.max_attempts
                else None
            )
            if retry_message is not None:
                attempt += 1
                delay_ms = callbacks.retry_delay_ms(attempt, error_payload)
                yield prompt_event(
                    "session_retry",
                    {
                        "attempt": attempt,
                        "message": retry_message,
                        "delay_ms": delay_ms,
                        "error": error_payload,
                    },
                )
                if session_id:
                    callbacks.set_session_status(
                        session_id,
                        {
                            "type": "retry",
                            "attempt": attempt,
                            "message": retry_message,
                            "next": runtime._now_ms() + delay_ms,
                        },
                    )
                if not callbacks.sleep_retry(session_id, delay_ms):
                    return ModelTurnResult(status="error", error_message="会话已中止")
                continue
            for payload in _transition_content_part(None):
                yield payload
            yield prompt_event("error", {"message": message})
            return ModelTurnResult(status="error", error_message=message, error_emitted=True)


def stream_tool_execution_events(
    config: ToolExecutionConfig,
    callbacks: ToolExecutionCallbacks,
) -> Iterator[PromptEvent]:
    call = config.call
    call_id = str(_runtime_item_value(call, "id", "") or "")
    call_name = str(_runtime_item_value(call, "name", "") or "")
    call_args = copy.deepcopy(_runtime_item_value(call, "arguments", {}) or {})
    call_metadata = _runtime_item_value(call, "metadata", None)
    provider_executed = bool(_runtime_item_value(call, "provider_executed", False))

    tool_start_payload = {"id": call_id, "name": call_name, "args": call_args}
    if isinstance(call_metadata, dict) and call_metadata:
        tool_start_payload["metadata"] = copy.deepcopy(call_metadata)
    if provider_executed:
        tool_start_payload["providerExecuted"] = True
    yield prompt_event("tool_start", tool_start_payload)

    final_result: Any | None = None
    context = callbacks.make_tool_context(config.options)
    for event in callbacks.execute_tool_stream(call_name, call_args, context):
        if callbacks.is_progress_event(event):
            yield prompt_event(
                "tool_progress",
                {
                    "id": call_id,
                    "name": call_name,
                    "message": str(_runtime_item_value(event, "message", "") or ""),
                    "current": _runtime_item_value(event, "current", None),
                    "total": _runtime_item_value(event, "total", None),
                },
            )
            continue
        final_result = event

    if final_result is None:
        final_result = callbacks.make_tool_result(False, f"{call_name} 未返回结果", None)

    tool_result_payload = {
        "id": call_id,
        "name": call_name,
        "success": bool(_runtime_item_value(final_result, "success", False)),
        "summary": _runtime_item_value(final_result, "summary", ""),
        "data": copy.deepcopy(_runtime_item_value(final_result, "data", None)),
    }
    if isinstance(call_metadata, dict) and call_metadata:
        tool_result_payload["metadata"] = copy.deepcopy(call_metadata)
    if provider_executed:
        tool_result_payload["providerExecuted"] = True
    internal_data = _runtime_item_value(final_result, "internal_data", None)
    if isinstance(internal_data, dict):
        display_data = internal_data.get("display_data")
        if isinstance(display_data, dict) and display_data:
            tool_result_payload["display_data"] = copy.deepcopy(display_data)
    yield prompt_event("tool_result", tool_result_payload)

    if isinstance(internal_data, dict):
        patch_data = {
            key: copy.deepcopy(value)
            for key, value in internal_data.items()
            if key != "display_data"
        }
    else:
        patch_data = {}
    if patch_data:
        yield prompt_event(
            "session_patch",
            {
                "id": call_id,
                "name": call_name,
                **patch_data,
            },
        )
    return final_result


def stream_tool_call_processing(
    config: ToolCallProcessingConfig,
    callbacks: ToolCallProcessingCallbacks,
) -> Iterator[PromptEvent]:
    remaining = list(config.tool_calls)
    first_call = True

    while remaining:
        call = remaining[0]
        call_id = str(_runtime_item_value(call, "id", "") or "")
        call_name = str(_runtime_item_value(call, "name", "") or "")
        call_args = copy.deepcopy(_runtime_item_value(call, "arguments", {}) or {})
        call_metadata = _runtime_item_value(call, "metadata", None)
        tool_def = callbacks.get_tool_definition(call_name)
        decision = callbacks.authorize_tool_call(
            call,
            config.session_record,
            create_pending_request=not (config.skip_first_confirmation and first_call),
        )
        decision_status = str(_runtime_item_value(decision, "status", "") or "")
        decision_reason = str(_runtime_item_value(decision, "reason", "") or "")
        decision_permission = _runtime_item_value(decision, "permission", None)
        decision_patterns = copy.deepcopy(_runtime_item_value(decision, "patterns", None))
        decision_always = copy.deepcopy(_runtime_item_value(decision, "always", None))
        decision_request = _runtime_item_value(decision, "request", None)

        if decision_status == "deny":
            tool_result = callbacks.make_tool_result(
                False,
                decision_reason or "权限规则阻止了这次工具调用",
                {
                    "denied": True,
                    "permission": decision_permission,
                    "patterns": decision_patterns,
                },
            )
            yield prompt_event(
                "tool_result",
                {
                    "id": call_id,
                    "name": call_name,
                    "success": False,
                    "summary": _runtime_item_value(tool_result, "summary", ""),
                    "data": copy.deepcopy(_runtime_item_value(tool_result, "data", None)),
                },
            )
            config.messages.append(callbacks.build_tool_message(call, tool_result))
            remaining.pop(0)
            first_call = False
            continue

        needs_confirm = decision_status == "ask" or (
            not callbacks.permission_manages_tool(call_name)
            and callbacks.requires_confirmation(call, tool_def, config.options)
        )

        if needs_confirm and not (config.skip_first_confirmation and first_call):
            rotate_before_pause = (
                bool(config.rotate_message_on_pause_after_progress)
                and not first_call
                and callable(callbacks.next_assistant_message_id)
                and callable(callbacks.emit_step_finish)
                and callable(callbacks.assistant_checkpoint_meta)
            )
            previous_assistant_message_id = str(config.assistant_message_id or "").strip() or None
            persisted_message_id = (
                str(
                    _runtime_item_value(decision_request, "tool", {}).get("messageID") or ""
                ).strip()
                if isinstance(_runtime_item_value(decision_request, "tool", None), dict)
                else ""
            )
            if rotate_before_pause:
                finish_reason = "tool-calls"
                yield from callbacks.emit_step_finish(
                    config.step_index + 1,
                    finish_reason,
                    config.step_usage,
                    config.step_snapshot,
                )
                yield prompt_event(
                    "session_assistant_commit",
                    {
                        "message_id": str(config.assistant_message_id or "").strip(),
                        "meta": callbacks.assistant_checkpoint_meta(
                            finish_reason,
                            config.step_usage,
                        ),
                    },
                )
                persisted_message_id = (
                    str(callbacks.next_assistant_message_id() or "").strip()
                    or f"message_{uuid4().hex}"
                )
                yield prompt_event(
                    "assistant_message_id",
                    {"message_id": persisted_message_id},
                )
            action_id = (
                str(_runtime_item_value(decision_request, "id", "") or "").strip()
                or uuid4().hex[:12]
            )
            request_tool_value = (
                _runtime_item_value(decision_request, "tool", {})
                if decision_request is not None
                else {}
            )
            request_tool = (
                dict(request_tool_value or {}) if isinstance(request_tool_value, dict) else {}
            )
            persisted_message_id = (
                persisted_message_id
                or str(request_tool.get("messageID") or "").strip()
                or str(config.assistant_message_id or "").strip()
                or f"message_{uuid4().hex}"
            )
            if decision_request is not None:
                request_tool["messageID"] = persisted_message_id
                _runtime_set_item_value(decision_request, "tool", request_tool)
            callbacks.store_pending_action(
                callbacks.create_pending_action(
                    ToolPendingActionConfig(
                        action_id=action_id,
                        options=config.options,
                        decision=decision,
                        messages=config.messages,
                        remaining_calls=remaining,
                        step_index=config.step_index,
                        step_snapshot=config.step_snapshot,
                        step_usage=copy.deepcopy(config.step_usage)
                        if isinstance(config.step_usage, dict)
                        else config.step_usage,
                        assistant_message_id=persisted_message_id,
                    )
                )
            )
            yield prompt_event(
                "action_confirm",
                {
                    "id": action_id,
                    "call_id": call_id,
                    "description": callbacks.summarize_action(call),
                    "tool": call_name,
                    "args": call_args,
                    "metadata": copy.deepcopy(call_metadata)
                    if isinstance(call_metadata, dict) and call_metadata
                    else None,
                    "assistant_message_id": persisted_message_id,
                    "permission": {
                        "permission": decision_permission,
                        "patterns": decision_patterns,
                        "always": decision_always,
                    }
                    if decision_status == "ask"
                    else None,
                    "previous_assistant_message_id": previous_assistant_message_id
                    if rotate_before_pause and previous_assistant_message_id
                    else None,
                },
            )
            return "paused", config.messages, config.step_index

        tool_result = yield from callbacks.execute_tool(call, config.options)
        if tool_result is None:
            tool_result = callbacks.make_tool_result(False, f"{call_name} 未返回结果", None)

        config.messages.append(callbacks.build_tool_message(call, tool_result))
        remaining.pop(0)
        first_call = False

    return "continue", config.messages, config.step_index + 1


@dataclass
class PromptLoopRuntimeConfig:
    options: Any
    initial_messages: list[dict[str, Any]]
    step_index: int
    assistant_message_id: str | None
    max_steps: int
    persistence_enabled: bool
    callbacks: PromptLoopCallbacks


@dataclass
class PromptLoopExecutor:
    options: Any
    initial_messages: list[dict[str, Any]]
    step_index: int
    assistant_message_id: str | None
    max_steps: int
    persistence_enabled: bool
    callbacks: PromptLoopCallbacks

    def _warn(self, message: str, exc: Exception) -> None:
        if callable(self.callbacks.on_warning):
            self.callbacks.on_warning(message, exc)

    def _reload_messages_if_needed(
        self,
        current_messages: list[dict[str, Any]],
        current_step: int,
    ) -> list[dict[str, Any]]:
        if (
            not self.persistence_enabled
            or current_step <= self.step_index
            or not callable(self.callbacks.load_persisted_messages)
        ):
            return current_messages
        persisted_messages = self.callbacks.load_persisted_messages()
        if persisted_messages:
            return self.callbacks.normalize_messages(persisted_messages)
        return current_messages

    def _emit_parent_if_present(self, parent_id: str | None) -> Iterator[str]:
        normalized_parent = str(parent_id or "").strip()
        if not normalized_parent:
            return
        yield from self.callbacks.emit_event(
            "session_parent",
            {"message_id": normalized_parent},
            publish_bus=True,
        )

    def _capture_step_snapshot(self) -> str | None:
        if not callable(self.callbacks.snapshot_root) or not callable(
            self.callbacks.capture_snapshot
        ):
            return None
        workspace_root = self.callbacks.snapshot_root()
        if not workspace_root:
            return None
        try:
            return self.callbacks.capture_snapshot(workspace_root)
        except Exception as exc:  # pragma: no cover - defensive path
            self._warn("Failed to capture prompt snapshot", exc)
            return None

    def _try_auto_compact(
        self,
        *,
        provider_id: str,
        model_id: str,
        overflow: bool,
        error_message: str,
        client_error_message: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not callable(self.callbacks.auto_compact):
            raise RuntimeError(client_error_message)
        try:
            return self.callbacks.auto_compact(provider_id, model_id, overflow)
        except Exception as exc:
            self._warn(error_message, exc)
            raise RuntimeError(f"{client_error_message}：{exc}") from exc

    def _should_hard_stop_tool_execution(self, current_step: int, turn_calls: list[Any]) -> bool:
        return _shared_should_hard_stop_after_tool_request(
            current_step,
            self.max_steps,
            requested_tool_calls=bool(turn_calls),
        )

    def _tool_call_signatures(self, turn_calls: list[Any]) -> tuple[str, ...]:
        signatures: list[str] = []
        for call in turn_calls:
            signatures.append(
                _shared_tool_call_signature(
                    str(_runtime_item_value(call, "name", "") or ""),
                    copy.deepcopy(_runtime_item_value(call, "arguments", None)),
                )
            )
        return tuple(signatures)

    def _should_hard_stop_repeated_tool_execution(
        self,
        current_step: int,
        previous_signatures: tuple[str, ...] | None,
        requested_signatures: tuple[str, ...],
    ) -> bool:
        return _shared_should_hard_stop_after_repeated_tool_calls(
            current_step,
            previous_signatures,
            requested_signatures,
        )

    def _synthetic_tool_skip_result(
        self,
        call: Any,
        *,
        summary: str,
        reason: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], Any]:
        call_id = str(_runtime_item_value(call, "id", "") or "")
        call_name = str(_runtime_item_value(call, "name", "") or "")
        result_data = {"skipped": True, "reason": reason}
        if isinstance(data, dict) and data:
            result_data.update(copy.deepcopy(data))
        result = self.callbacks.make_tool_result(
            False,
            summary,
            result_data,
        )
        payload = {
            "id": call_id,
            "name": call_name,
            "success": False,
            "summary": _runtime_item_value(result, "summary", ""),
            "data": copy.deepcopy(_runtime_item_value(result, "data", None)),
        }
        metadata = _runtime_item_value(call, "metadata", None)
        if isinstance(metadata, dict) and metadata:
            payload["metadata"] = copy.deepcopy(metadata)
        return payload, result

    def _step_limit_tool_skip_result(
        self,
        call: Any,
    ) -> tuple[dict[str, Any], Any]:
        return self._synthetic_tool_skip_result(
            call,
            summary="已达到本轮工具步骤上限，未执行此工具调用",
            reason="step_limit_reached",
            data={"tool_budget": int(self.max_steps or 0)},
        )

    def run(self) -> Iterator[str]:
        current_messages = copy.deepcopy(self.initial_messages)
        current_step = self.step_index
        auto_compaction_attempts = 0
        previous_tool_signatures: tuple[str, ...] | None = None
        current_assistant_message_id = (
            str(self.assistant_message_id or "").strip()
            or self.callbacks.next_assistant_message_id()
        )
        assistant_message_announced = False

        while True:
            if current_step > self.max_steps:
                yield from self.callbacks.emit_event(
                    "text_delta",
                    {"content": _shared_build_step_limit_reached_notice(self.max_steps) + "\n\n"},
                    publish_bus=True,
                )
                yield from self.callbacks.emit_stream(
                    self.callbacks.step_limit_summary(current_messages, self.max_steps),
                    publish_bus=True,
                )
                yield from self.callbacks.emit_done(publish_bus=True)
                return

            current_messages = self._reload_messages_if_needed(current_messages, current_step)

            if auto_compaction_attempts == 0 and callable(
                self.callbacks.latest_auto_compaction_target
            ):
                target = self.callbacks.latest_auto_compaction_target()
                if target is not None:
                    try:
                        compacted = self._try_auto_compact(
                            provider_id=str(target.get("providerID") or ""),
                            model_id=str(target.get("modelID") or ""),
                            overflow=bool(target.get("overflow", True)),
                            error_message="Automatic preflight compaction failed",
                            client_error_message="自动压缩上下文失败",
                        )
                    except RuntimeError as exc:
                        yield from self.callbacks.emit_event(
                            "error",
                            {"message": str(exc)},
                            publish_bus=True,
                        )
                        yield from self.callbacks.emit_done(publish_bus=True)
                        return
                    compacted_messages, parent_id = compacted
                    auto_compaction_attempts += 1
                    current_messages = self.callbacks.normalize_messages(compacted_messages)
                    yield from self._emit_parent_if_present(parent_id)

            if not assistant_message_announced:
                yield from self.callbacks.emit_event(
                    "assistant_message_id",
                    {"message_id": current_assistant_message_id},
                    publish_bus=True,
                )
                assistant_message_announced = True

            if callable(self.callbacks.publish_step_start):
                self.callbacks.publish_step_start(current_step + 1)
            step_snapshot = self._capture_step_snapshot()
            yield from self.callbacks.emit_event(
                "session_step_start",
                {"step": current_step + 1, "snapshot": step_snapshot},
                publish_bus=not callable(self.callbacks.publish_step_start),
            )

            prepared_messages = self.callbacks.prepare_loop_messages(
                current_messages,
                current_step,
                self.max_steps,
            )
            turn = yield from self.callbacks.emit_stream(
                self.callbacks.run_model_turn(prepared_messages),
                publish_bus=True,
            )

            if turn.status == "compact":
                if (
                    not str(getattr(self.options, "session_id", "") or "").strip()
                    or auto_compaction_attempts >= 2
                ):
                    yield from self.callbacks.emit_event(
                        "error",
                        {
                            "message": turn.error_message
                            or "上下文超出模型限制，且自动压缩未能继续执行"
                        },
                        publish_bus=True,
                    )
                    yield from self.callbacks.emit_done(publish_bus=True)
                    return
                yield from self.callbacks.emit_event(
                    "session_assistant_reset",
                    {"message_id": current_assistant_message_id},
                    publish_bus=True,
                )
                identity = self.callbacks.resolve_model_identity()
                try:
                    compacted = self._try_auto_compact(
                        provider_id=identity["providerID"],
                        model_id=identity["modelID"],
                        overflow=True,
                        error_message="Automatic overflow compaction failed",
                        client_error_message="上下文超出模型限制，自动压缩失败",
                    )
                except RuntimeError as exc:
                    yield from self.callbacks.emit_event(
                        "error",
                        {"message": str(exc)},
                        publish_bus=True,
                    )
                    yield from self.callbacks.emit_done(publish_bus=True)
                    return
                compacted_messages, parent_id = compacted
                auto_compaction_attempts += 1
                current_messages = self.callbacks.normalize_messages(compacted_messages)
                yield from self._emit_parent_if_present(parent_id)
                current_assistant_message_id = self.callbacks.next_assistant_message_id()
                assistant_message_announced = False
                continue

            if turn.status != "continue":
                if turn.status == "error" and turn.error_message and not turn.error_emitted:
                    yield from self.callbacks.emit_event(
                        "error",
                        {"message": turn.error_message},
                        publish_bus=True,
                    )
                yield from self.callbacks.emit_done(publish_bus=True)
                return

            turn_calls = turn.tool_calls
            assistant_turn_calls = turn.assistant_tool_calls or turn_calls
            if turn.content or assistant_turn_calls or turn.reasoning_content:
                current_messages.append(
                    self.callbacks.build_assistant_message(
                        turn.content,
                        assistant_turn_calls,
                        text_parts=turn.text_parts,
                        reasoning_content=turn.reasoning_content,
                        reasoning_parts=turn.reasoning_parts,
                        provider_metadata=turn.provider_metadata,
                    )
                )
            if turn.tool_messages:
                current_messages.extend(copy.deepcopy(turn.tool_messages))

            finish_reason = "tool-calls" if turn_calls else "stop"
            turn_signatures = self._tool_call_signatures(turn_calls)
            if self._should_hard_stop_tool_execution(current_step, turn_calls):
                for call in turn_calls:
                    payload, result = self._step_limit_tool_skip_result(call)
                    yield from self.callbacks.emit_event(
                        "tool_result",
                        payload,
                        publish_bus=True,
                    )
                    current_messages.append(self.callbacks.build_tool_message(call, result))
                yield from self.callbacks.emit_event(
                    "text_delta",
                    {"content": _shared_build_step_limit_reached_notice(self.max_steps) + "\n\n"},
                    publish_bus=True,
                )
                yield from self.callbacks.emit_stream(
                    self.callbacks.step_limit_summary(current_messages, self.max_steps),
                    publish_bus=True,
                )
                yield from self.callbacks.emit_stream(
                    self.callbacks.emit_step_finish(
                        current_step + 1,
                        "stop",
                        turn.usage,
                        step_snapshot,
                    ),
                    publish_bus=True,
                )
                yield from self.callbacks.emit_done(publish_bus=True)
                return
            if self._should_hard_stop_repeated_tool_execution(
                current_step,
                previous_tool_signatures,
                turn_signatures,
            ):
                for call in turn_calls:
                    payload, result = self._synthetic_tool_skip_result(
                        call,
                        summary="检测到连续重复的工具请求，未再次执行此工具调用",
                        reason="duplicate_tool_call",
                    )
                    yield from self.callbacks.emit_event(
                        "tool_result",
                        payload,
                        publish_bus=True,
                    )
                    current_messages.append(self.callbacks.build_tool_message(call, result))
                history_results = _shared_collect_tool_result_items_from_messages(
                    current_messages,
                    limit=5,
                    include_skipped=False,
                )
                followup = _shared_resolve_tool_result_followup_text(
                    turn.content,
                    history_results,
                )
                final_text = _shared_build_repeated_tool_call_notice()
                if followup.final_text:
                    final_text = f"{final_text}\n\n{followup.final_text}".strip()
                yield from self.callbacks.emit_event(
                    "text_delta",
                    {"content": final_text},
                    publish_bus=True,
                )
                yield from self.callbacks.emit_stream(
                    self.callbacks.emit_step_finish(
                        current_step + 1,
                        "stop",
                        turn.usage,
                        step_snapshot,
                    ),
                    publish_bus=True,
                )
                yield from self.callbacks.emit_done(publish_bus=True)
                return
            if not turn_calls:
                previous_tool_signatures = None
                yield from self.callbacks.emit_stream(
                    self.callbacks.emit_step_finish(
                        current_step + 1,
                        finish_reason,
                        turn.usage,
                        step_snapshot,
                    ),
                    publish_bus=True,
                )
                yield from self.callbacks.emit_done(publish_bus=True)
                return

            status, next_messages, next_step = yield from self.callbacks.emit_stream(
                self.callbacks.process_tool_calls(
                    current_messages,
                    turn_calls,
                    current_step,
                    step_snapshot,
                    turn.usage,
                    current_assistant_message_id,
                ),
                publish_bus=True,
            )
            if status == "paused":
                yield from self.callbacks.emit_done(publish_bus=True)
                return

            yield from self.callbacks.emit_stream(
                self.callbacks.emit_step_finish(
                    current_step + 1,
                    finish_reason,
                    turn.usage,
                    step_snapshot,
                ),
                publish_bus=True,
            )

            assistant_commit_meta = self.callbacks.assistant_checkpoint_meta(
                finish_reason,
                turn.usage,
            )
            yield from self.callbacks.emit_event(
                "session_assistant_commit",
                {
                    "message_id": current_assistant_message_id,
                    "meta": assistant_commit_meta,
                },
                publish_bus=True,
            )

            previous_tool_signatures = turn_signatures or None
            current_messages = next_messages or current_messages
            current_step = next_step
            next_assistant_message_id = self.callbacks.next_assistant_message_id()

            if auto_compaction_attempts >= self.callbacks.max_auto_compaction_attempts:
                current_assistant_message_id = next_assistant_message_id
                assistant_message_announced = False
                continue
            identity = self.callbacks.resolve_model_identity()
            if not self.callbacks.is_overflow_tokens(
                self.callbacks.usage_to_tokens(turn.usage),
                identity["providerID"],
                identity["modelID"],
            ):
                current_assistant_message_id = next_assistant_message_id
                assistant_message_announced = False
                continue
            try:
                compacted = self._try_auto_compact(
                    provider_id=identity["providerID"],
                    model_id=identity["modelID"],
                    overflow=False,
                    error_message="Automatic post-step compaction failed",
                    client_error_message="自动压缩上下文失败",
                )
            except RuntimeError as exc:
                yield from self.callbacks.emit_event(
                    "error",
                    {"message": str(exc)},
                    publish_bus=True,
                )
                yield from self.callbacks.emit_done(publish_bus=True)
                return
            compacted_messages, parent_id = compacted
            auto_compaction_attempts += 1
            current_messages = self.callbacks.normalize_messages(compacted_messages)
            yield from self._emit_parent_if_present(parent_id)
            current_assistant_message_id = next_assistant_message_id
            assistant_message_announced = False


def stream_prompt_loop(config: PromptLoopRuntimeConfig) -> Iterator[str]:
    executor = PromptLoopExecutor(
        options=config.options,
        initial_messages=copy.deepcopy(config.initial_messages),
        step_index=config.step_index,
        assistant_message_id=config.assistant_message_id,
        max_steps=config.max_steps,
        persistence_enabled=config.persistence_enabled,
        callbacks=config.callbacks,
    )
    yield from executor.run()


@dataclass
class PermissionResponseConfig:
    action_id: str
    response: str
    message: str | None
    answers: list[list[str]] | None
    pending: Any
    resume_state: Any


@dataclass
class PermissionResponseCallbacks:
    emit_event: Callable[..., Iterator[str]]
    emit_done: Callable[..., Iterator[str]]
    observe_prompt_events: Callable[..., Iterator[str]]
    observe_stream: Callable[..., Iterator[str]]
    reply_permission: Callable[[str, str, str | None], Any]
    pop_pending_action: Callable[[str], Any]
    pending_tool_calls: Callable[[Any], list[Any]]
    pending_messages: Callable[[Any], list[dict[str, Any]]]
    build_rejected_tool_result: Callable[[Any, str | None], tuple[dict[str, Any], dict[str, Any]]]
    build_question_tool_result: Callable[
        [Any, list[dict[str, Any]], list[list[str]], str | None, str],
        tuple[dict[str, Any], dict[str, Any]],
    ]
    emit_step_finish: Callable[[int, str, dict[str, Any] | None, str | None], Iterator[PromptEvent]]
    assistant_checkpoint_meta: Callable[[str, dict[str, Any] | None], dict[str, Any]]
    process_tool_calls_stream: Callable[
        [list[dict[str, Any]], list[Any], int, str | None, dict[str, Any] | None, str | None],
        Iterator[str],
    ]
    resume_stream: Callable[[list[dict[str, Any]], int, str | None, bool], Iterator[str]]
    next_assistant_message_id: Callable[[], str] | None = None


def _pending_question_items(pending: Any) -> list[dict[str, Any]]:
    permission_request = (
        _runtime_item_value(pending, "permission_request", None) if pending is not None else None
    )
    if not isinstance(permission_request, dict):
        return []
    if str(permission_request.get("permission") or "").strip() != "question":
        return []
    metadata = (
        permission_request.get("metadata")
        if isinstance(permission_request.get("metadata"), dict)
        else {}
    )
    questions = metadata.get("questions") if isinstance(metadata.get("questions"), list) else []
    return [dict(item) for item in questions if isinstance(item, dict)]


def _normalize_question_answers(
    answers: list[list[str]] | None,
    questions: list[dict[str, Any]],
) -> list[list[str]]:
    raw_answers = answers if isinstance(answers, list) else []
    normalized: list[list[str]] = []
    for index, _question in enumerate(questions):
        value = (
            raw_answers[index]
            if index < len(raw_answers) and isinstance(raw_answers[index], list)
            else []
        )
        entry: list[str] = []
        for item in value:
            cleaned = runtime._clean_text(item)
            if cleaned and cleaned not in entry:
                entry.append(cleaned)
        normalized.append(entry)
    return normalized


def stream_permission_response_runtime(
    config: PermissionResponseConfig,
    callbacks: PermissionResponseCallbacks,
) -> Iterator[str]:
    def _next_resume_message_id() -> str | None:
        if not callable(callbacks.next_assistant_message_id):
            return None
        return runtime._clean_text(callbacks.next_assistant_message_id()) or None

    pending_tool_calls = callbacks.pending_tool_calls(config.pending)
    if config.response == "reject" and not pending_tool_calls:
        yield from callbacks.emit_event(
            "error",
            {"message": "待确认动作不存在或已失效"},
            publish_bus=True,
        )
        yield from callbacks.emit_done()
        return

    callbacks.reply_permission(config.action_id, config.response, config.message)
    callbacks.pop_pending_action(config.action_id)
    pending_tool_calls = callbacks.pending_tool_calls(config.pending)
    step_index = int(_runtime_item_value(config.resume_state, "step_index", 0) or 0)
    step_snapshot = (
        runtime._clean_text(_runtime_item_value(config.resume_state, "step_snapshot", None)) or None
    )
    step_usage = (
        dict(_runtime_item_value(config.resume_state, "step_usage", None) or {})
        if isinstance(_runtime_item_value(config.resume_state, "step_usage", None), dict)
        else None
    )
    assistant_message_id = (
        runtime._clean_text(_runtime_item_value(config.resume_state, "assistant_message_id", None))
        or None
    )
    question_items = _pending_question_items(config.pending)

    def _commit_current_assistant(finish_reason: str) -> Iterator[str]:
        yield from callbacks.emit_event(
            "session_assistant_commit",
            {
                "message_id": assistant_message_id,
                "meta": callbacks.assistant_checkpoint_meta(finish_reason, step_usage),
            },
            publish_bus=True,
        )

    if question_items:
        if not pending_tool_calls:
            yield from callbacks.emit_event(
                "error",
                {"message": "待回答问题不存在或已失效"},
                publish_bus=True,
            )
            yield from callbacks.emit_done()
            return
        first_call = pending_tool_calls[0]
        normalized_answers = _normalize_question_answers(config.answers, question_items)
        tool_result_payload, tool_message = callbacks.build_question_tool_result(
            first_call,
            question_items,
            normalized_answers,
            config.message,
            config.response,
        )
        yield from callbacks.emit_event(
            "tool_result",
            tool_result_payload,
            publish_bus=True,
        )
        yield from callbacks.observe_prompt_events(
            callbacks.emit_step_finish(
                step_index + 1,
                "tool-calls",
                step_usage,
                step_snapshot,
            ),
            publish_bus=True,
        )
        yield from _commit_current_assistant("tool-calls")
        resume_messages = callbacks.pending_messages(config.pending)
        resume_messages.append(tool_message)
        yield from callbacks.resume_stream(
            resume_messages,
            step_index + 1,
            _next_resume_message_id(),
            True,
        )
        return

    if config.response == "reject":
        if not pending_tool_calls:
            yield from callbacks.emit_event(
                "error",
                {"message": "待确认动作不存在或已失效"},
                publish_bus=True,
            )
            yield from callbacks.emit_done()
            return
        first_call = pending_tool_calls[0]
        tool_result_payload, tool_message = callbacks.build_rejected_tool_result(
            first_call, config.message
        )
        yield from callbacks.emit_event(
            "tool_result",
            tool_result_payload,
            publish_bus=True,
        )
        yield from callbacks.observe_prompt_events(
            callbacks.emit_step_finish(
                step_index + 1,
                "tool-calls",
                step_usage,
                step_snapshot,
            ),
            publish_bus=True,
        )
        yield from _commit_current_assistant("tool-calls")
        resume_messages = callbacks.pending_messages(config.pending)
        resume_messages.append(tool_message)
        yield from callbacks.resume_stream(
            resume_messages,
            step_index + 1,
            _next_resume_message_id(),
            True,
        )
        return

    status, _messages, next_step_index = yield from callbacks.observe_stream(
        callbacks.process_tool_calls_stream(
            callbacks.pending_messages(config.pending),
            pending_tool_calls,
            step_index,
            step_snapshot,
            step_usage,
            assistant_message_id,
        ),
        publish_bus=True,
    )
    if status == "paused":
        yield from callbacks.emit_done(publish_bus=True)
        return
    yield from callbacks.observe_prompt_events(
        callbacks.emit_step_finish(
            step_index + 1,
            "tool-calls",
            step_usage,
            step_snapshot,
        ),
        publish_bus=True,
    )
    yield from _commit_current_assistant("tool-calls")
    yield from callbacks.resume_stream(
        [],
        next_step_index,
        _next_resume_message_id(),
        False,
    )


def prompt_terminal_error(control: PromptStreamControl) -> str | None:
    if control.error_message:
        return control.error_message
    if control.cancelled:
        return "会话已中止"
    if not control.saw_done:
        return "前序会话未正常结束，已取消排队请求"
    return None


def iter_prompt_result_message_events(
    result: dict[str, Any] | None,
    *,
    include_message_id: bool = True,
    include_text: bool = True,
) -> Iterator[str]:
    if not isinstance(result, dict):
        return
    message = result.get("message")
    if not isinstance(message, dict):
        return
    info = dict(message.get("info") or {}) if isinstance(message.get("info"), dict) else {}
    message_id = str(info.get("id") or result.get("messageID") or "").strip()
    if include_message_id and message_id:
        yield runtime._format_sse_event("assistant_message_id", {"message_id": message_id})
    if not include_text:
        return
    parts = message.get("parts")
    if not isinstance(parts, list):
        return
    for part in parts:
        if not isinstance(part, dict):
            continue
        if str(part.get("type") or "") != "text":
            continue
        text = str(part.get("text") or "")
        if not text:
            continue
        payload = {"content": text}
        part_id = str(part.get("id") or "").strip()
        if part_id:
            payload["id"] = part_id
        yield runtime._format_sse_event("text_delta", payload)


def synthetic_prompt_result_message(
    result: dict[str, Any] | None,
    control: PromptStreamControl | None,
) -> dict[str, Any]:
    payload = dict(result or {}) if isinstance(result, dict) else {}
    if isinstance(payload.get("message"), dict):
        return payload
    if not isinstance(control, PromptStreamControl):
        return payload
    message_id = (
        str(payload.get("messageID") or "").strip()
        or str(control.assistant_message_id or "").strip()
    )
    parts: list[dict[str, Any]] = []
    for part in control.text_parts:
        if not isinstance(part, dict):
            continue
        text = str(part.get("text") or "")
        if not text:
            continue
        item: dict[str, Any] = {
            "type": "text",
            "text": text,
        }
        part_id = str(part.get("id") or "").strip()
        if part_id:
            item["id"] = part_id
        metadata = part.get("metadata")
        if isinstance(metadata, dict) and metadata:
            item["metadata"] = copy.deepcopy(metadata)
        parts.append(item)
    if not parts:
        if message_id and "messageID" not in payload:
            payload["messageID"] = message_id
        return payload
    info: dict[str, Any] = {"role": "assistant"}
    if message_id:
        info["id"] = message_id
        payload.setdefault("messageID", message_id)
    payload["message"] = {
        "info": info,
        "parts": parts,
    }
    return payload


def iter_prompt_pause_events(
    control: PromptStreamControl | None,
    *,
    include_message_id: bool = True,
    fallback_message_id: str | None = None,
) -> Iterator[str]:
    if not isinstance(control, PromptStreamControl):
        return
    payload = (
        copy.deepcopy(control.action_confirm) if isinstance(control.action_confirm, dict) else {}
    )
    if not payload:
        return
    message_id = (
        str(payload.get("assistant_message_id") or payload.get("assistantMessageID") or "").strip()
        or str(fallback_message_id or "").strip()
        or str(control.assistant_message_id or "").strip()
    )
    if include_message_id and message_id:
        yield runtime._format_sse_event("assistant_message_id", {"message_id": message_id})
    if (
        message_id
        and not str(
            payload.get("assistant_message_id") or payload.get("assistantMessageID") or ""
        ).strip()
    ):
        payload["assistant_message_id"] = message_id
    yield runtime._format_sse_event("action_confirm", payload)


def iter_callback_stream(callback: Any) -> Iterator[str]:
    observed = PromptStreamControl()
    saw_error = False
    saw_done = False
    saw_message_id = False
    saw_text_delta = False
    saw_action_confirm = False
    emitted_items = 0
    for item in callback.iter_items():
        emitted_items += 1
        parsed = runtime._parse_sse_event(item)
        if parsed:
            if parsed[0] == "assistant_message_id":
                saw_message_id = True
            if parsed[0] == "text_delta":
                saw_text_delta = True
            if parsed[0] == "action_confirm":
                saw_action_confirm = True
            if parsed[0] == "error":
                saw_error = True
            if parsed[0] == "done":
                saw_done = True
            observed.observe(item)
        yield item
    observed.absorb(getattr(callback, "control", None))
    if not observed.error_message and callback.error:
        observed.error_message = str(callback.error)
        if observed.error_message == "会话已中止":
            observed.cancelled = True
    callback_result = synthetic_prompt_result_message(
        callback.result if isinstance(callback.result, dict) else None,
        observed,
    )
    if callback.outcome == "resolved" and (emitted_items == 0 or not saw_text_delta):
        for item in iter_prompt_result_message_events(
            callback_result,
            include_message_id=not saw_message_id,
            include_text=not saw_text_delta,
        ):
            parsed = runtime._parse_sse_event(item)
            if parsed:
                if parsed[0] == "assistant_message_id":
                    saw_message_id = True
                if parsed[0] == "text_delta":
                    saw_text_delta = True
                if parsed[0] == "action_confirm":
                    saw_action_confirm = True
            yield item
    fallback_message_id = (
        observed.assistant_message_id or str(callback_result.get("messageID") or "").strip() or None
    )
    if fallback_message_id and not saw_message_id:
        yield runtime._format_sse_event("assistant_message_id", {"message_id": fallback_message_id})
        saw_message_id = True
    if observed.paused and not saw_action_confirm:
        for item in iter_prompt_pause_events(
            observed,
            include_message_id=not saw_message_id,
            fallback_message_id=fallback_message_id,
        ):
            parsed = runtime._parse_sse_event(item)
            if parsed:
                if parsed[0] == "assistant_message_id":
                    saw_message_id = True
                if parsed[0] == "action_confirm":
                    saw_action_confirm = True
            yield item
    if not saw_done:
        terminal_error = prompt_terminal_error(observed)
        if terminal_error and not saw_error:
            yield runtime._format_sse_event("error", {"message": terminal_error})
        yield runtime._format_sse_event("done", {})


class _ProcessorState:
    def __init__(
        self,
        *,
        session_id: str,
        parent_id: str | None,
        assistant_meta: dict[str, Any] | None,
        assistant_message_id: str | None,
    ) -> None:
        self.session_id = session_id
        self.parent_id = runtime._clean_text(parent_id) or None
        self.assistant_meta = copy.deepcopy(assistant_meta or {})
        self.current_message_id = runtime._clean_text(assistant_message_id) or None
        self.current_parent_id = self.parent_id
        self.current_message_meta: dict[str, Any] = copy.deepcopy(self.assistant_meta)
        self.current_message_meta.setdefault("finish", None)
        self.pending_finish: str | None = None
        self.pending_error: dict[str, Any] | None = None
        self.pending_step_snapshot: str | None = None
        self.seen_action_confirm = False
        self.seen_done = False
        self.open_text_part_id: str | None = None
        self.open_reasoning_part_id: str | None = None

    def _open_part_attr(self, part_type: str) -> str:
        return "open_reasoning_part_id" if part_type == "reasoning" else "open_text_part_id"

    def get_open_part_id(self, part_type: str) -> str | None:
        return runtime._clean_text(getattr(self, self._open_part_attr(part_type), None)) or None

    def set_open_part_id(self, part_type: str, part_id: str | None) -> None:
        setattr(self, self._open_part_attr(part_type), runtime._clean_text(part_id) or None)

    def clear_stream_parts(self) -> None:
        self.open_text_part_id = None
        self.open_reasoning_part_id = None

    def reserve_stream_part_id(self, part_type: str, explicit_id: str | None = None) -> str:
        part_id = runtime._clean_text(explicit_id)
        if not part_id:
            part_id = self.get_open_part_id(part_type) or runtime._part_id(None)
        self.set_open_part_id(part_type, part_id)
        other_part_type = "reasoning" if part_type == "text" else "text"
        if self.get_open_part_id(other_part_type):
            self.set_open_part_id(other_part_type, None)
        return part_id

    def close_stream_part(self, part_type: str, explicit_id: str | None = None) -> str | None:
        part_id = runtime._clean_text(explicit_id) or self.get_open_part_id(part_type)
        if not part_id:
            return None
        if not explicit_id or part_id == self.get_open_part_id(part_type):
            self.set_open_part_id(part_type, None)
        return part_id

    def ensure_message(self) -> str:
        message_id = self.current_message_id or f"message_{uuid4().hex}"
        existing, _ = runtime._load_message_parts(self.session_id, message_id)
        if existing is None:
            runtime.append_session_message(
                session_id=self.session_id,
                role="assistant",
                content="",
                parent_id=self.current_parent_id,
                meta=self.current_message_meta,
                parts=[],
                message_id=message_id,
            )
        self.current_message_id = message_id
        return message_id

    def load_parts(self) -> list[dict[str, Any]]:
        message_id = self.ensure_message()
        _, parts = runtime._load_message_parts(self.session_id, message_id)
        return parts

    def save_parts(
        self,
        parts: list[dict[str, Any]],
        *,
        meta: dict[str, Any] | object = ...,
        publish: bool = True,
    ) -> dict[str, Any]:
        message_id = self.ensure_message()
        content = runtime._aggregate_message_content(parts, role="assistant")
        if meta is not ...:
            self.current_message_meta = copy.deepcopy(meta)
        return runtime._update_message_parts(
            session_id=self.session_id,
            message_id=message_id,
            parts=parts,
            meta=self.current_message_meta if meta is not ... else ...,
            content=content,
            publish=publish,
        )

    def commit_current_message(
        self, *, finish: str | None = None, meta: dict[str, Any] | None = None
    ) -> None:
        if self.current_message_id is None:
            return
        message, parts = runtime._load_message_parts(self.session_id, self.current_message_id)
        if message is None:
            return
        merged_meta = copy.deepcopy(self.current_message_meta)
        if isinstance(meta, dict):
            merged_meta.update(copy.deepcopy(meta))
        if finish is not None:
            merged_meta["finish"] = finish
        self.current_message_meta = merged_meta
        self.save_parts(parts, meta=merged_meta)

    def reset_current_message(self) -> None:
        if not self.current_message_id:
            return
        message, _parts = runtime._load_message_parts(self.session_id, self.current_message_id)
        if message is not None:
            with session_scope() as session:
                AgentSessionMessageRepository(session).delete_by_ids([self.current_message_id])
            runtime._publish_message_deleted(self.session_id, self.current_message_id)
        self.current_message_id = None
        self.pending_finish = None
        self.pending_error = None
        self.pending_step_snapshot = None
        self.seen_action_confirm = False
        self.current_message_meta = copy.deepcopy(self.assistant_meta)
        self.current_message_meta.setdefault("finish", None)
        self.clear_stream_parts()

    def roll_to_message(self, message_id: str | None) -> None:
        self.current_message_id = runtime._clean_text(message_id) or None
        self.pending_finish = None
        self.pending_error = None
        self.pending_step_snapshot = None
        self.seen_action_confirm = False
        self.current_message_meta = copy.deepcopy(self.assistant_meta)
        self.current_message_meta.setdefault("finish", None)
        self.clear_stream_parts()


class SessionProcessor:
    """Processor-owned assistant message mutator used by native session flow."""

    def __init__(
        self,
        *,
        session_id: str,
        parent_id: str | None = None,
        assistant_meta: dict[str, Any] | None = None,
        assistant_message_id: str | None = None,
    ) -> None:
        sid = runtime._session_id(session_id)
        runtime.ensure_session_record(sid)
        self.session_id = sid
        self.state = _ProcessorState(
            session_id=sid,
            parent_id=parent_id,
            assistant_meta=assistant_meta,
            assistant_message_id=assistant_message_id,
        )

    def start(self) -> None:
        runtime.set_session_status(self.session_id, {"type": "busy"})

    def _append_pending_snapshot_patch(self, parts: list[dict[str, Any]]) -> None:
        snapshot_hash = runtime._clean_text(self.state.pending_step_snapshot)
        if not snapshot_hash:
            return
        session_record = runtime.get_session_record(self.session_id) or {}
        workspace_server_id = runtime._clean_text(
            session_record.get("workspace_server_id")
            or self.state.current_message_meta.get("workspace_server_id")
            or self.state.assistant_meta.get("workspace_server_id")
        )
        workspace_path = runtime._clean_text(
            session_record.get("workspace_path")
            or self.state.current_message_meta.get("cwd")
            or self.state.assistant_meta.get("cwd")
        )
        if not workspace_path:
            self.state.pending_step_snapshot = None
            return
        try:
            payload = {
                "hash": snapshot_hash,
                "workspace_path": workspace_path,
                "workspace_server_id": workspace_server_id or None,
            }
            payload["diffs"] = session_snapshot.diff_current_full(
                workspace_path,
                snapshot_hash,
            )
        except Exception:
            runtime.logger.exception("Failed to materialize pending snapshot patch")
            self.state.pending_step_snapshot = None
            return
        parts.append(
            session_message_v2.patch_part(
                part_id=runtime._part_id(None),
                session_id=self.session_id,
                message_id=self.state.ensure_message(),
                payload=payload,
            )
        )
        self.state.pending_step_snapshot = None

    def _apply_error(self, message: str) -> None:
        parts = self.state.load_parts()
        self._append_pending_snapshot_patch(parts)
        tool_parts = [part for part in parts if str(part.get("type") or "") == "tool"]
        if tool_parts:
            tool = tool_parts[-1]
            tool_state = dict(tool.get("state") or {})
            tool_state["status"] = "error"
            tool_state["error"] = "Tool execution aborted" if "中止" in message else message
            time_payload = dict(tool_state.get("time") or {})
            time_payload["end"] = runtime._now_ms()
            tool_state["time"] = time_payload
            tool["state"] = tool_state
        error_payload = normalize_error(message)
        self.state.pending_error = error_payload
        meta = session_message_v2.merge_error_meta(
            self.state.current_message_meta,
            error=error_payload,
            finish="aborted" if error_payload.get("name") == "AbortedError" else "error",
        )
        self.state.save_parts(parts, meta=meta)

    def _handle_part_start(self, event_name: str, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        part_type = "reasoning" if event_name.startswith("reasoning") else "text"
        part_id = self.state.reserve_stream_part_id(part_type, str(data.get("id") or ""))
        runtime._upsert_text_like_part(
            parts,
            part_id=part_id,
            part_type=part_type,
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
        )
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_part_delta(self, event_name: str, data: dict[str, Any]) -> None:
        self.state.ensure_message()
        part_type = "reasoning" if event_name.startswith("reasoning") else "text"
        part_id = self.state.reserve_stream_part_id(part_type, str(data.get("id") or ""))
        delta = str(data.get("content") or "")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else None
        parts = self.state.load_parts()
        part, created = runtime._upsert_text_like_part(
            parts,
            part_id=part_id,
            part_type=part_type,
            metadata=metadata,
        )
        if created:
            self.state.save_parts(parts, meta=self.state.current_message_meta)
        if part_type == "reasoning":
            part["text"] = session_message_v2.append_reasoning_fragment(
                str(part.get("text") or ""), delta
            )
        else:
            part["text"] = str(part.get("text") or "") + delta
        time_payload = dict(part.get("time") or {})
        time_payload.setdefault("start", runtime._now_ms())
        time_payload["end"] = runtime._now_ms()
        part["time"] = time_payload
        if isinstance(metadata, dict) and metadata:
            part["metadata"] = copy.deepcopy(metadata)
        self.state.save_parts(parts, meta=self.state.current_message_meta, publish=False)
        runtime._publish_part_delta(
            self.session_id,
            str(self.state.current_message_id or ""),
            part_id,
            field="text",
            delta=delta,
        )

    def _handle_part_end(self, event_name: str, data: dict[str, Any]) -> None:
        part_type = "reasoning" if event_name.startswith("reasoning") else "text"
        part_id = self.state.close_stream_part(part_type, str(data.get("id") or ""))
        if part_id is None:
            return
        parts = self.state.load_parts()
        for part in parts:
            if str(part.get("id") or "") != part_id:
                continue
            time_payload = dict(part.get("time") or {})
            time_payload.setdefault("start", runtime._now_ms())
            time_payload["end"] = runtime._now_ms()
            part["time"] = time_payload
            break
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_tool_input_start(self, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        call_id = runtime._clean_text(data.get("id"))
        part, _ = runtime._upsert_tool_part(
            parts,
            call_id=call_id,
            tool_name=runtime._clean_text(data.get("toolName")),
            provider_executed=bool(data.get("providerExecuted")),
        )
        part["state"] = {
            "status": "pending",
            "raw": "",
            "time": {"start": runtime._now_ms(), "end": runtime._now_ms()},
        }
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_tool_input_delta(self, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        call_id = runtime._clean_text(data.get("id"))
        delta = str(data.get("delta") or "")
        part_id: str | None = None
        for part in parts:
            if str(part.get("type") or "") != "tool" or str(part.get("callID") or "") != call_id:
                continue
            part_id = str(part.get("id") or "").strip() or None
            tool_state = dict(part.get("state") or {})
            tool_state["raw"] = str(tool_state.get("raw") or "") + delta
            parsed_input = runtime._parse_json_maybe(str(tool_state.get("raw") or ""))
            if isinstance(parsed_input, dict):
                tool_state["input"] = parsed_input
            time_payload = dict(tool_state.get("time") or {})
            time_payload.setdefault("start", runtime._now_ms())
            time_payload["end"] = runtime._now_ms()
            tool_state["time"] = time_payload
            part["state"] = tool_state
            break
        self.state.save_parts(parts, meta=self.state.current_message_meta, publish=False)
        if part_id is not None and self.state.current_message_id:
            runtime._publish_part_delta(
                self.session_id,
                str(self.state.current_message_id),
                part_id,
                field="state.raw",
                delta=delta,
            )

    def _handle_tool_input_end(self, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        call_id = runtime._clean_text(data.get("id"))
        for part in parts:
            if str(part.get("type") or "") != "tool" or str(part.get("callID") or "") != call_id:
                continue
            tool_state = dict(part.get("state") or {})
            parsed_input = runtime._parse_json_maybe(str(tool_state.get("raw") or ""))
            if isinstance(parsed_input, dict):
                tool_state["input"] = parsed_input
            time_payload = dict(tool_state.get("time") or {})
            time_payload.setdefault("start", runtime._now_ms())
            time_payload["end"] = runtime._now_ms()
            tool_state["time"] = time_payload
            part["state"] = tool_state
            break
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_tool_start(self, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        call_id = runtime._clean_text(data.get("id"))
        part, _ = runtime._upsert_tool_part(
            parts,
            call_id=call_id,
            tool_name=runtime._clean_text(data.get("name")),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
            provider_executed=bool(data.get("providerExecuted")),
        )
        tool_state = dict(part.get("state") or {})
        tool_state["status"] = "running"
        tool_state["input"] = copy.deepcopy(data.get("args") or {})
        tool_state["time"] = {"start": runtime._now_ms(), "end": runtime._now_ms()}
        part["state"] = tool_state
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_tool_result(self, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        call_id = runtime._clean_text(data.get("id"))
        success = bool(True if data.get("success") is None else data.get("success"))
        base_data = copy.deepcopy(data.get("data") or {})
        display_data = (
            copy.deepcopy(data.get("display_data") or {})
            if isinstance(data.get("display_data"), dict)
            else {}
        )
        if isinstance(base_data, dict) and display_data:
            resolved_data: dict[str, Any] = {**base_data, **display_data}
        elif isinstance(base_data, dict):
            resolved_data = base_data
        else:
            resolved_data = display_data
        for part in parts:
            if str(part.get("type") or "") != "tool" or str(part.get("callID") or "") != call_id:
                continue
            if runtime._clean_text(data.get("name")):
                part["tool"] = runtime._clean_text(data.get("name"))
            part["summary"] = data.get("summary")
            part["data"] = resolved_data
            if isinstance(data.get("metadata"), dict) and data["metadata"]:
                part["metadata"] = copy.deepcopy(data["metadata"])
            if data.get("providerExecuted"):
                part["providerExecuted"] = True
            tool_state = dict(part.get("state") or {})
            tool_state["status"] = "completed" if success else "error"
            data_payload = part.get("data")
            if isinstance(data_payload, dict):
                if isinstance(data_payload.get("stdout"), str):
                    tool_state["output"] = data_payload.get("stdout")
                tool_state["metadata"] = copy.deepcopy(data_payload)
            tool_state["title"] = data.get("summary")
            time_payload = dict(tool_state.get("time") or {})
            time_payload.setdefault("start", runtime._now_ms())
            time_payload["end"] = runtime._now_ms()
            tool_state["time"] = time_payload
            part["state"] = tool_state
            break
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_usage(self, data: dict[str, Any]) -> None:
        meta = copy.deepcopy(self.state.current_message_meta)
        meta["tokens"] = runtime._merge_tokens(
            meta.get("tokens") if isinstance(meta.get("tokens"), dict) else None,
            data,
        )
        if runtime._clean_text(data.get("model")):
            meta["modelID"] = runtime._clean_text(data.get("model"))
        if isinstance(data.get("metadata"), dict) and data["metadata"]:
            meta["providerMetadata"] = copy.deepcopy(data["metadata"])
        self.state.current_message_meta = meta
        parts = self.state.load_parts()
        self.state.save_parts(parts, meta=meta)

    def _handle_session_retry(self, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        parts.append(
            session_message_v2.retry_part(
                part_id=runtime._part_id(None),
                session_id=self.session_id,
                message_id=self.state.ensure_message(),
                attempt=int(data.get("attempt") or 0),
                message=str(data.get("message") or "") or None,
                delay_ms=int(data.get("delay_ms") or 0),
                error=copy.deepcopy(data.get("error") or {}),
                time={"start": runtime._now_ms(), "end": runtime._now_ms()},
            )
        )
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_step_start(self, data: dict[str, Any]) -> None:
        self.state.pending_step_snapshot = runtime._clean_text(data.get("snapshot")) or None
        parts = self.state.load_parts()
        parts.append(
            session_message_v2.step_start_part(
                part_id=runtime._part_id(None),
                session_id=self.session_id,
                message_id=self.state.ensure_message(),
                step=int(data.get("step") or 0),
                snapshot=str(data.get("snapshot") or "").strip() or None,
                time={"start": runtime._now_ms(), "end": runtime._now_ms()},
            )
        )
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_step_finish(self, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        usage = dict(data.get("usage") or {}) if isinstance(data.get("usage"), dict) else {}
        parts.append(
            session_message_v2.step_finish_part(
                part_id=runtime._part_id(None),
                session_id=self.session_id,
                message_id=self.state.ensure_message(),
                step=int(data.get("step") or 0),
                reason=str(data.get("reason") or "stop"),
                tokens=runtime._merge_tokens({}, usage),
                cost=data.get("cost"),
                snapshot=str(data.get("snapshot") or "").strip() or None,
                time={"start": runtime._now_ms(), "end": runtime._now_ms()},
            )
        )
        self.state.pending_finish = str(data.get("reason") or "stop")
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def _handle_patch(self, data: dict[str, Any]) -> None:
        parts = self.state.load_parts()
        patch_items = data.get("patches") if isinstance(data.get("patches"), list) else [data]
        for patch in patch_items:
            if not isinstance(patch, dict):
                continue
            payload = copy.deepcopy(patch)
            if isinstance(payload.get("hash"), str) and runtime._clean_text(
                payload.get("workspace_path")
            ):
                try:
                    payload["diffs"] = session_snapshot.diff_current_full(
                        payload["workspace_path"],
                        payload["hash"],
                        files=[
                            str(item)
                            for item in (payload.get("files") or [])
                            if runtime._clean_text(item)
                        ]
                        or None,
                    )
                except Exception:
                    runtime.logger.exception("Failed to materialize session patch diffs")
            parts.append(
                session_message_v2.patch_part(
                    part_id=runtime._part_id(None),
                    session_id=self.session_id,
                    message_id=self.state.ensure_message(),
                    payload=payload,
                )
            )
        message = self.state.save_parts(parts, meta=self.state.current_message_meta)
        self.state.pending_step_snapshot = None
        for part in message["parts"]:
            if str(part.get("type") or "") == "patch":
                runtime._publish_part_updated(self.session_id, part)

    def _handle_assistant_commit(self, data: dict[str, Any]) -> None:
        meta = copy.deepcopy(self.state.current_message_meta)
        if isinstance(data.get("meta"), dict):
            meta.update(copy.deepcopy(data["meta"]))
        finish = (
            runtime._clean_text(meta.get("finish")) or self.state.pending_finish or "tool-calls"
        )
        meta["finish"] = finish
        self.state.commit_current_message(finish=finish, meta=meta)

    def _handle_action_confirm(self, data: dict[str, Any]) -> None:
        self.state.seen_action_confirm = True
        self.state.ensure_message()
        call_id = runtime._clean_text(data.get("call_id"))
        assistant_message_id_payload = runtime._clean_text(data.get("assistant_message_id"))
        previous_assistant_message_id = runtime._clean_text(
            data.get("previous_assistant_message_id")
        )
        if (
            previous_assistant_message_id
            and previous_assistant_message_id != assistant_message_id_payload
            and call_id
        ):
            previous_message, previous_parts = runtime._load_message_parts(
                self.session_id,
                previous_assistant_message_id,
            )
            if previous_message is not None:
                removed_ids: list[str] = []
                remaining_parts: list[dict[str, Any]] = []
                for part in previous_parts:
                    if (
                        str(part.get("type") or "") == "tool"
                        and str(part.get("callID") or "") == call_id
                    ):
                        removed_ids.append(str(part.get("id") or "").strip())
                        continue
                    remaining_parts.append(part)
                if removed_ids:
                    previous_info = (
                        previous_message.get("info")
                        if isinstance(previous_message.get("info"), dict)
                        else {}
                    )
                    runtime._update_message_parts(
                        session_id=self.session_id,
                        message_id=previous_assistant_message_id,
                        parts=remaining_parts,
                        meta=runtime._message_meta_from_info(previous_info) or None,
                    )
                    for removed_id in removed_ids:
                        if removed_id:
                            runtime._publish_part_deleted(self.session_id, removed_id)
        if assistant_message_id_payload:
            self.state.current_message_id = assistant_message_id_payload
        parts = self.state.load_parts()
        part, _ = runtime._upsert_tool_part(
            parts,
            call_id=call_id,
            tool_name=runtime._clean_text(data.get("tool")),
        )
        tool_state = dict(part.get("state") or {})
        tool_state["status"] = "pending"
        if isinstance(data.get("args"), dict):
            tool_state["input"] = copy.deepcopy(data["args"])
        if "raw" not in tool_state and isinstance(tool_state.get("input"), dict):
            tool_state["raw"] = json.dumps(
                tool_state["input"], ensure_ascii=False, separators=(",", ":")
            )
        time_payload = dict(tool_state.get("time") or {})
        time_payload.setdefault("start", runtime._now_ms())
        time_payload["end"] = runtime._now_ms()
        tool_state["time"] = time_payload
        part["state"] = tool_state
        self.state.save_parts(parts, meta=self.state.current_message_meta)

    def set_assistant_message_id(self, message_id: str | None) -> None:
        candidate = runtime._clean_text(message_id)
        if candidate:
            self.state.roll_to_message(candidate)

    def set_parent_message_id(self, message_id: str | None) -> None:
        candidate = runtime._clean_text(message_id)
        if candidate:
            self.state.current_parent_id = candidate

    def reset_assistant_message(self) -> None:
        self.state.reset_current_message()

    def begin_stream_part(
        self,
        part_type: str,
        *,
        part_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_type = (
            "reasoning" if str(part_type or "").strip().lower() == "reasoning" else "text"
        )
        self._handle_part_start(
            f"{normalized_type}-start",
            {
                "id": runtime._clean_text(part_id) or None,
                "metadata": copy.deepcopy(metadata)
                if isinstance(metadata, dict) and metadata
                else None,
            },
        )

    def append_stream_part_delta(
        self,
        part_type: str,
        *,
        part_id: str | None = None,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_type = (
            "reasoning" if str(part_type or "").strip().lower() == "reasoning" else "text"
        )
        self._handle_part_delta(
            f"{normalized_type}_delta",
            {
                "id": runtime._clean_text(part_id) or None,
                "content": str(content or ""),
                "metadata": copy.deepcopy(metadata)
                if isinstance(metadata, dict) and metadata
                else None,
            },
        )

    def end_stream_part(self, part_type: str, *, part_id: str | None = None) -> None:
        normalized_type = (
            "reasoning" if str(part_type or "").strip().lower() == "reasoning" else "text"
        )
        self._handle_part_end(
            f"{normalized_type}-end",
            {
                "id": runtime._clean_text(part_id) or None,
            },
        )

    def begin_tool_input(
        self,
        *,
        call_id: str,
        tool_name: str | None = None,
        provider_executed: bool = False,
    ) -> None:
        self._handle_tool_input_start(
            {
                "id": runtime._clean_text(call_id),
                "toolName": runtime._clean_text(tool_name) or None,
                "providerExecuted": bool(provider_executed),
            }
        )

    def append_tool_input_delta(
        self,
        *,
        call_id: str,
        delta: str,
        provider_executed: bool = False,
    ) -> None:
        self._handle_tool_input_delta(
            {
                "id": runtime._clean_text(call_id),
                "delta": str(delta or ""),
                "providerExecuted": bool(provider_executed),
            }
        )

    def end_tool_input(self, *, call_id: str, provider_executed: bool = False) -> None:
        self._handle_tool_input_end(
            {
                "id": runtime._clean_text(call_id),
                "providerExecuted": bool(provider_executed),
            }
        )

    def begin_tool_call(
        self,
        *,
        call_id: str,
        name: str,
        args: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_executed: bool = False,
    ) -> None:
        self._handle_tool_start(
            {
                "id": runtime._clean_text(call_id),
                "name": runtime._clean_text(name),
                "args": copy.deepcopy(args) if isinstance(args, dict) else {},
                "metadata": copy.deepcopy(metadata)
                if isinstance(metadata, dict) and metadata
                else None,
                "providerExecuted": bool(provider_executed),
            }
        )

    def finish_tool_call(
        self,
        *,
        call_id: str,
        name: str | None = None,
        success: bool,
        summary: Any = None,
        data: Any = None,
        display_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        provider_executed: bool = False,
    ) -> None:
        self._handle_tool_result(
            {
                "id": runtime._clean_text(call_id),
                "name": runtime._clean_text(name) or None,
                "success": bool(success),
                "summary": summary,
                "data": copy.deepcopy(data) if isinstance(data, dict) else data,
                "display_data": copy.deepcopy(display_data)
                if isinstance(display_data, dict) and display_data
                else None,
                "metadata": copy.deepcopy(metadata)
                if isinstance(metadata, dict) and metadata
                else None,
                "providerExecuted": bool(provider_executed),
            }
        )

    def apply_usage(self, usage: dict[str, Any]) -> None:
        self._handle_usage(copy.deepcopy(usage) if isinstance(usage, dict) else {})

    def record_retry(self, data: dict[str, Any]) -> None:
        self._handle_session_retry(copy.deepcopy(data) if isinstance(data, dict) else {})

    def begin_step(self, data: dict[str, Any]) -> None:
        self._handle_step_start(copy.deepcopy(data) if isinstance(data, dict) else {})

    def finish_step(self, data: dict[str, Any]) -> None:
        self._handle_step_finish(copy.deepcopy(data) if isinstance(data, dict) else {})

    def append_patch(self, data: dict[str, Any]) -> None:
        self._handle_patch(copy.deepcopy(data) if isinstance(data, dict) else {})

    def commit_assistant(self, meta: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {}
        if isinstance(meta, dict) and meta:
            payload["meta"] = copy.deepcopy(meta)
        self._handle_assistant_commit(payload)

    def record_action_confirm(self, data: dict[str, Any]) -> None:
        self._handle_action_confirm(copy.deepcopy(data) if isinstance(data, dict) else {})

    def apply_error_message(self, message: str) -> None:
        self._apply_error(str(message or "对话请求失败"))

    def finalize_done(self) -> list[tuple[str, dict[str, Any]]]:
        synthetic_events: list[tuple[str, dict[str, Any]]] = []
        self.state.seen_done = True
        if runtime.is_session_aborted(self.session_id):
            self._apply_error("会话已中止")
            synthetic_events.append(("error", {"message": "会话已中止"}))
        elif (
            self.state.current_message_id
            and not self.state.seen_action_confirm
            and self.state.pending_error is None
        ):
            finish = (
                self.state.pending_finish
                or runtime._clean_text(self.state.current_message_meta.get("finish"))
                or "stop"
            )
            self.state.commit_current_message(finish=finish)
        return synthetic_events

    def apply_event(
        self, event_name: str, data: dict[str, Any]
    ) -> list[tuple[str, dict[str, Any]]]:
        synthetic_events: list[tuple[str, dict[str, Any]]] = []
        if event_name == "assistant_message_id":
            self.set_assistant_message_id(data.get("message_id") or data.get("messageID"))
            self.state.ensure_message()
        elif event_name == "session_parent":
            self.set_parent_message_id(data.get("message_id") or data.get("messageID"))
        elif event_name == "session_assistant_reset":
            self.reset_assistant_message()
        elif event_name in {"reasoning-start", "text-start"}:
            self.begin_stream_part(
                "reasoning" if event_name.startswith("reasoning") else "text",
                part_id=data.get("id"),
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
            )
        elif event_name in {"reasoning_delta", "text_delta"}:
            self.append_stream_part_delta(
                "reasoning" if event_name.startswith("reasoning") else "text",
                part_id=data.get("id"),
                content=str(data.get("content") or ""),
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
            )
        elif event_name in {"reasoning-end", "text-end"}:
            self.end_stream_part(
                "reasoning" if event_name.startswith("reasoning") else "text",
                part_id=data.get("id"),
            )
        elif event_name == "tool-input-start":
            self.begin_tool_input(
                call_id=str(data.get("id") or ""),
                tool_name=str(data.get("toolName") or ""),
                provider_executed=bool(data.get("providerExecuted")),
            )
        elif event_name == "tool-input-delta":
            self.append_tool_input_delta(
                call_id=str(data.get("id") or ""),
                delta=str(data.get("delta") or ""),
                provider_executed=bool(data.get("providerExecuted")),
            )
        elif event_name == "tool-input-end":
            self.end_tool_input(
                call_id=str(data.get("id") or ""),
                provider_executed=bool(data.get("providerExecuted")),
            )
        elif event_name == "tool_start":
            self.begin_tool_call(
                call_id=str(data.get("id") or ""),
                name=str(data.get("name") or ""),
                args=data.get("args") if isinstance(data.get("args"), dict) else {},
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
                provider_executed=bool(data.get("providerExecuted")),
            )
        elif event_name == "tool_result":
            self.finish_tool_call(
                call_id=str(data.get("id") or ""),
                name=str(data.get("name") or ""),
                success=bool(True if data.get("success") is None else data.get("success")),
                summary=data.get("summary"),
                data=data.get("data"),
                display_data=data.get("display_data")
                if isinstance(data.get("display_data"), dict)
                else None,
                metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else None,
                provider_executed=bool(data.get("providerExecuted")),
            )
        elif event_name == "usage":
            self.apply_usage(data)
        elif event_name == "session_retry":
            self.record_retry(data)
        elif event_name == "session_step_start":
            self.begin_step(data)
        elif event_name == "session_step_finish":
            self.finish_step(data)
        elif event_name == "session_patch":
            self.append_patch(data)
        elif event_name == "session_assistant_commit":
            self.commit_assistant(data.get("meta") if isinstance(data.get("meta"), dict) else None)
        elif event_name == "action_confirm":
            self.record_action_confirm(data)
        elif event_name == "error":
            self.apply_error_message(str(data.get("message") or "对话请求失败"))
        elif event_name == "done":
            synthetic_events.extend(self.finalize_done())
        return synthetic_events

    def consume(self, raw: Any) -> list[str]:
        parsed = runtime._coerce_runtime_event(raw)
        if parsed is None:
            return []
        event_name, data = parsed
        return [
            runtime._format_sse_event(name, payload)
            for name, payload in self.apply_event(event_name, data)
        ]

    def stream(
        self,
        raw_stream: Iterator[Any],
        *,
        manage_lifecycle: bool = True,
        control: Any | None = None,
        lifecycle_kind: str = "prompt",
        step_index: int = 0,
        publish_bus: bool = False,
    ) -> Iterator[str]:
        stream_control = control if isinstance(control, PromptStreamControl) else None
        driver = (
            PromptEventStreamDriver(
                control=stream_control,
                session_id=self.session_id,
                lifecycle_kind=lifecycle_kind,
                step_index=step_index,
            )
            if stream_control is not None
            else None
        )
        if manage_lifecycle:
            self.start()
        delta_persistence_buffer = _DeltaPersistenceBuffer(self)

        def _emit_output(item: PromptEvent | str) -> Iterator[str]:
            if driver is None:
                if isinstance(item, PromptEvent):
                    yield runtime._format_sse_event(item.event, item.data)
                else:
                    yield str(item)
                return
            yield from driver.emit_raw(item, publish_bus=publish_bus)

        def _flush_delta_persistence() -> Iterator[str]:
            for emitted in delta_persistence_buffer.flush():
                yield from _emit_output(emitted)

        try:
            for raw in raw_stream:
                parsed = runtime._coerce_runtime_event(raw)
                if parsed is None:
                    yield from _flush_delta_persistence()
                    serialized = str(raw)
                    yield from _emit_output(serialized)
                    continue
                event_name, data = parsed
                if _DeltaPersistenceBuffer.can_buffer(event_name, data):
                    for emitted in delta_persistence_buffer.push(event_name, data):
                        yield from _emit_output(emitted)
                    yield from _emit_output(prompt_event(event_name, data))
                    continue
                yield from _flush_delta_persistence()
                for name, payload in self.apply_event(event_name, data):
                    yield from _emit_output(prompt_event(name, payload))
                yield from _emit_output(prompt_event(event_name, data))
            yield from _flush_delta_persistence()
        except GeneratorExit:
            delta_persistence_buffer.flush()
            raise
        finally:
            delta_persistence_buffer.flush()
            self.finalize(
                manage_lifecycle=manage_lifecycle,
                handed_off=bool(getattr(control, "handed_off", False)),
            )

    def finalize(self, *, manage_lifecycle: bool = True, handed_off: bool = False) -> None:
        if (
            runtime.is_session_aborted(self.session_id)
            and self.state.pending_error is None
            and self.state.current_message_id
        ):
            self._apply_error("会话已中止")
        if manage_lifecycle and not handed_off:
            runtime.set_session_status(self.session_id, {"type": "idle"})
        if manage_lifecycle:
            runtime.clear_session_abort(self.session_id)
