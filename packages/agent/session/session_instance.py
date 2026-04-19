"""OpenCode-style instance lifecycle manager for project/session runtime state."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.agent import global_bus
from packages.agent.session.session_lifecycle import list_prompt_session_ids
from packages.agent.session.session_runtime import get_session_record, request_session_abort
from packages.storage.db import session_scope
from packages.storage.repositories import AgentProjectRepository, AgentSessionRepository


def _normalize_directory(directory: str | None) -> str:
    raw = str(directory or "").strip()
    if not raw:
        raw = str(Path.cwd())
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return raw


def _normalize_path(path: str | None) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return raw


def _contains(root: str, candidate: str) -> bool:
    normalized_root = _normalize_path(root)
    normalized_candidate = _normalize_path(candidate)
    if not normalized_root or not normalized_candidate:
        return False
    try:
        Path(normalized_candidate).relative_to(Path(normalized_root))
        return True
    except ValueError:
        return False


def _project_id_for(directory: str) -> str:
    digest = hashlib.sha1(f"local::{directory}".encode("utf-8")).hexdigest()[:16]
    return f"project_{digest}"


@dataclass
class InstanceContext:
    directory: str
    worktree: str
    project: dict[str, Any]


@dataclass
class _StateEntry:
    value: Any
    dispose: Any = None


class _ClassPropertyDescriptor:
    def __init__(self, getter) -> None:  # noqa: ANN001
        self._getter = getter

    def __get__(self, _instance, owner):  # noqa: ANN001, ANN204
        return self._getter(owner)


def _classproperty(getter):  # noqa: ANN001, ANN202
    return _ClassPropertyDescriptor(getter)


def _project_context_for(directory: str) -> InstanceContext:
    normalized_directory = _normalize_directory(directory)
    default_name = Path(normalized_directory).name or normalized_directory
    with session_scope() as session:
        repo = AgentProjectRepository(session)
        row = repo.get_by_worktree(normalized_directory)
        if row is None:
            row = repo.upsert(
                project_id=_project_id_for(normalized_directory),
                worktree=normalized_directory,
                name=default_name,
                sandboxes=[normalized_directory],
            )
        worktree = _normalize_directory(str(row.worktree or "").strip() or normalized_directory)
        sandboxes = [
            _normalize_directory(str(item or ""))
            for item in (row.sandboxes_json or [])
            if str(item or "").strip()
        ]
        if normalized_directory not in sandboxes:
            sandboxes.insert(0, normalized_directory)
        project = {
            "id": row.id,
            "worktree": worktree,
            "name": row.name or default_name,
            "vcs": row.vcs,
            "sandboxes": sandboxes,
        }
    return InstanceContext(
        directory=normalized_directory,
        worktree=worktree,
        project=project,
    )


def _session_ids_for_directory(directory: str) -> list[str]:
    normalized_directory = _normalize_directory(directory)
    with session_scope() as session:
        rows = AgentSessionRepository(session).list_all(
            directory=normalized_directory,
            limit=10000,
            archived=False,
        )
        session_ids = {str(row.id) for row in rows}
    for session_id in list_prompt_session_ids():
        record = get_session_record(session_id) or {}
        session_directory = _normalize_directory(
            str(record.get("directory") or record.get("workspace_path") or "").strip()
        )
        if session_directory == normalized_directory:
            session_ids.add(str(session_id))
    return sorted(session_ids)


def _emit_disposed(directory: str, session_ids: list[str]) -> None:
    global_bus.publish_event(
        directory,
        {
            "type": "server.instance.disposed",
            "properties": {
                "directory": directory,
                "session_ids": copy.deepcopy(session_ids),
            },
        },
    )


_LOCK = threading.RLock()
_CACHE: dict[str, InstanceContext] = {}
_STATE_CACHE: dict[str, dict[object, _StateEntry]] = {}
_CURRENT_CONTEXT: ContextVar[InstanceContext | None] = ContextVar("researchos_instance_context", default=None)
_DISPOSE_ALL_CONDITION = threading.Condition(_LOCK)
_DISPOSE_ALL_ACTIVE = False
_DISPOSE_ALL_RESULT: list[str] = []


def _run_disposer(dispose, value: Any) -> None:  # noqa: ANN001, ANN202
    if not callable(dispose):
        return
    result = dispose(value)
    if not inspect.isawaitable(result):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(result)
        return
    loop.create_task(result)


class Instance:
    @classmethod
    def _resolve_context(
        cls,
        directory: str | None,
        *,
        project: dict[str, Any] | None = None,
        worktree: str | None = None,
    ) -> InstanceContext:
        resolved_directory = _normalize_directory(directory)
        if project is not None or worktree is not None:
            current = _project_context_for(resolved_directory)
            return InstanceContext(
                directory=resolved_directory,
                worktree=_normalize_directory(worktree or current.worktree),
                project=copy.deepcopy(project) if isinstance(project, dict) else current.project,
            )
        with _LOCK:
            cached = _CACHE.get(resolved_directory)
        if cached is not None:
            return cached
        return _project_context_for(resolved_directory)

    @classmethod
    def _boot(
        cls,
        directory: str | None,
        *,
        init=None,  # noqa: ANN001
        project: dict[str, Any] | None = None,
        worktree: str | None = None,
        replace: bool = False,
    ) -> InstanceContext:
        resolved_directory = _normalize_directory(directory)
        with _LOCK:
            cached = _CACHE.get(resolved_directory)
        if cached is not None and not replace and project is None and worktree is None:
            return cached

        ctx = cls._resolve_context(
            resolved_directory,
            project=project,
            worktree=worktree,
        )
        token = _CURRENT_CONTEXT.set(ctx)
        try:
            if callable(init):
                init()
        finally:
            _CURRENT_CONTEXT.reset(token)
        with _LOCK:
            _CACHE[resolved_directory] = ctx
        return ctx

    @classmethod
    def _require_context(cls) -> InstanceContext:
        ctx = _CURRENT_CONTEXT.get()
        if ctx is None:
            raise RuntimeError("Instance context is not active")
        return ctx

    @_classproperty
    def directory(cls) -> str:  # noqa: N805
        return cls._require_context().directory

    @_classproperty
    def worktree(cls) -> str:  # noqa: N805
        return cls._require_context().worktree

    @_classproperty
    def project(cls) -> dict[str, Any]:  # noqa: N805
        return copy.deepcopy(cls._require_context().project)

    @classmethod
    def provide(
        cls,
        *,
        directory: str | None,
        fn,
        init=None,  # noqa: ANN001
        project: dict[str, Any] | None = None,
        worktree: str | None = None,
    ):  # noqa: ANN201
        ctx = cls._boot(
            directory,
            init=init,
            project=project,
            worktree=worktree,
        )
        token = _CURRENT_CONTEXT.set(ctx)
        try:
            return fn()
        finally:
            _CURRENT_CONTEXT.reset(token)

    @classmethod
    def contains_path(cls, filepath: str, *, directory: str | None = None) -> bool:
        current = _CURRENT_CONTEXT.get()
        if current is None:
            current = cls._resolve_context(directory)
        if _contains(current.directory, filepath):
            return True
        if current.worktree == "/":
            return False
        return _contains(current.worktree, filepath)

    containsPath = contains_path

    @classmethod
    def state(cls, init, dispose=None):  # noqa: ANN001, ANN202
        def _get():  # noqa: ANN202
            current = _CURRENT_CONTEXT.get()
            directory = current.directory if current is not None else _normalize_directory(None)
            with _LOCK:
                entries = _STATE_CACHE.setdefault(directory, {})
                existing = entries.get(init)
                if existing is not None:
                    return existing.value
            value = init()
            with _LOCK:
                entries = _STATE_CACHE.setdefault(directory, {})
                existing = entries.get(init)
                if existing is not None:
                    return existing.value
                entries[init] = _StateEntry(value=value, dispose=dispose)
            return value

        return _get

    @classmethod
    def _dispose_state(cls, directory: str) -> None:
        normalized_directory = _normalize_directory(directory)
        with _LOCK:
            entries = _STATE_CACHE.pop(normalized_directory, {})
        for entry in list(entries.values()):
            try:
                _run_disposer(entry.dispose, entry.value)
            except Exception:
                continue

    @classmethod
    def _teardown_directory(cls, directory: str | None) -> tuple[str, list[str]]:
        resolved_directory = _normalize_directory(directory)
        session_ids = _session_ids_for_directory(resolved_directory)
        for session_id in session_ids:
            request_session_abort(session_id)
        cls._dispose_state(resolved_directory)
        with _LOCK:
            _CACHE.pop(resolved_directory, None)
        return resolved_directory, session_ids

    @classmethod
    def _collect_known_directories(cls, *, extra_directories: list[str] | None = None) -> list[str]:
        directories: set[str] = set()
        with _LOCK:
            directories.update(_CACHE.keys())

        with session_scope() as session:
            project_rows = AgentProjectRepository(session).list_all(limit=10000)
            directories.update(
                _normalize_directory(str(row.worktree or "").strip())
                for row in project_rows
                if str(row.worktree or "").strip()
            )

        for session_id in list_prompt_session_ids():
            session_record = get_session_record(session_id) or {}
            directory = str(session_record.get("directory") or session_record.get("workspace_path") or "").strip()
            if directory:
                directories.add(_normalize_directory(directory))

        for directory in extra_directories or []:
            if str(directory or "").strip():
                directories.add(_normalize_directory(directory))
        return sorted(directories)

    @classmethod
    def reload(
        cls,
        directory: str | None,
        *,
        init=None,  # noqa: ANN001
        project: dict[str, Any] | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        resolved_directory, session_ids = cls._teardown_directory(directory)
        ctx = cls._boot(
            resolved_directory,
            init=init,
            project=project,
            worktree=worktree,
            replace=True,
        )
        _emit_disposed(resolved_directory, session_ids)
        return {
            "directory": ctx.directory,
            "worktree": ctx.worktree,
            "project": copy.deepcopy(ctx.project),
        }

    @classmethod
    def snapshot(cls, directory: str | None) -> dict[str, Any]:
        ctx = cls._boot(directory)
        return {
            "directory": ctx.directory,
            "worktree": ctx.worktree,
            "project": copy.deepcopy(ctx.project),
        }

    @classmethod
    def project_info(cls, directory: str | None) -> dict[str, Any]:
        return copy.deepcopy(cls.snapshot(directory)["project"])

    @classmethod
    def dispose(cls, directory: str | None = None) -> dict[str, Any]:
        resolved_directory, session_ids = cls._teardown_directory(
            directory or (_CURRENT_CONTEXT.get().directory if _CURRENT_CONTEXT.get() else None)
        )
        _emit_disposed(resolved_directory, session_ids)
        return {
            "directory": resolved_directory,
            "session_ids": session_ids,
        }

    @classmethod
    def dispose_all(cls, *, extra_directories: list[str] | None = None) -> list[str]:
        global _DISPOSE_ALL_ACTIVE, _DISPOSE_ALL_RESULT
        with _DISPOSE_ALL_CONDITION:
            if _DISPOSE_ALL_ACTIVE:
                while _DISPOSE_ALL_ACTIVE:
                    _DISPOSE_ALL_CONDITION.wait()
                return list(_DISPOSE_ALL_RESULT)
            _DISPOSE_ALL_ACTIVE = True

        disposed: list[str] = []
        try:
            for directory in cls._collect_known_directories(extra_directories=extra_directories):
                cls.dispose(directory)
                disposed.append(directory)
            return disposed
        finally:
            with _DISPOSE_ALL_CONDITION:
                _DISPOSE_ALL_RESULT = list(disposed)
                _DISPOSE_ALL_ACTIVE = False
                _DISPOSE_ALL_CONDITION.notify_all()

    disposeAll = dispose_all


def current_project_info(directory: str | None) -> dict[str, Any]:
    return Instance.project_info(directory)


def dispose_directory(directory: str | None) -> dict[str, Any]:
    return Instance.dispose(directory)


def dispose_all_instances(*, extra_directories: list[str] | None = None) -> list[str]:
    return Instance.dispose_all(extra_directories=extra_directories)

