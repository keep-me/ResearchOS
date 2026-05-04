"""Standalone ACP registry and execution service for ResearchOS."""

from __future__ import annotations

import contextlib
import http.cookiejar
import json
import os
import queue
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from packages.agent.workspace.workspace_remote import (
    clean_text,
    open_ssh_session,
    remote_stat,
    resolve_remote_workspace_path,
)
from packages.config import get_settings

_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _ROOT / "data"
_REGISTRY_PATH = _DATA_DIR / "assistant_acp_registry.json"
_DEFAULT_TIMEOUT_SEC = 60
_MAX_STDERR_CHARS = 8000


def _load_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _slugify(value: str) -> str:
    chars: list[str] = []
    for char in str(value or "").strip().lower():
        if char.isalnum() or char in {"_", "-", "."}:
            chars.append(char)
        else:
            chars.append("-")
    return "".join(chars).strip("-") or "acp"


def _trim_text(value: str | None, limit: int = _MAX_STDERR_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _now_ts() -> float:
    return time.time()


def _shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts if str(part).strip())


def _stream_write_text(handle: Any, text: str) -> None:
    try:
        handle.write(text)
    except TypeError:
        handle.write(text.encode("utf-8"))
    handle.flush()


def _stream_read_text(raw_line: Any) -> str:
    if raw_line is None:
        return ""
    if isinstance(raw_line, bytes):
        return raw_line.decode("utf-8", errors="replace")
    return str(raw_line)


def _workspace_server_store_path() -> Path:
    settings = get_settings()
    base_dir = settings.pdf_storage_root.parent.resolve()
    return base_dir / "assistant_workspace_servers.json"


def _load_workspace_server_entry(server_id: str | None) -> dict[str, Any] | None:
    normalized_id = _slugify(server_id or "")
    if not normalized_id:
        return None
    payload = _load_json_file(_workspace_server_store_path())
    items = payload if isinstance(payload, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        current_id = _slugify(str(item.get("id") or item.get("label") or item.get("host") or ""))
        if current_id == normalized_id:
            return item
    return None


def _open_remote_session(server_entry: dict[str, Any]):
    manager = open_ssh_session(server_entry)
    session = manager.__enter__()
    return manager, session


class _RpcClient(Protocol):
    def request(self, method: str, params: dict[str, Any], timeout_sec: int) -> Any: ...

    def prompt(
        self,
        session_id: str,
        prompt_blocks: list[dict[str, Any]],
        timeout_sec: int,
    ) -> tuple[Any, list[dict[str, Any]], list[str]]: ...

    def close(self) -> None: ...

    def stderr_snapshot(self) -> str: ...


@runtime_checkable
class _InteractiveRpcClient(Protocol):
    def start_prompt(
        self, session_id: str, prompt_blocks: list[dict[str, Any]]
    ) -> _StreamingPromptHandle: ...

    def wait_prompt_event(
        self,
        handle: _StreamingPromptHandle,
        timeout_sec: int,
    ) -> tuple[str, Any, list[dict[str, Any]], list[str]]: ...

    def respond_permission(self, request_id: int, *, option_id: str | None = None) -> None: ...


@dataclass
class _StreamingPromptHandle:
    session_id: str
    request_id: int
    response_queue: queue.Queue[tuple[bool, Any]]


class _StreamingRpcClient:
    def __init__(
        self,
        *,
        stdin_handle: Any,
        stdout_handle: Any,
        stderr_handle: Any | None,
        close_callback,
    ) -> None:
        self._stdin = stdin_handle
        self._stdout = stdout_handle
        self._stderr = stderr_handle
        self._close_callback = close_callback
        self._lock = threading.RLock()
        self._pending: dict[int, queue.Queue[tuple[bool, Any]]] = {}
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._permission_notes: dict[str, list[str]] = {}
        self._permission_events: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._closed = False
        self._request_id = 0
        self._stderr_chunks: list[str] = []
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        self._stderr_reader: threading.Thread | None = None
        if self._stderr is not None:
            self._stderr_reader = threading.Thread(target=self._stderr_loop, daemon=True)
            self._stderr_reader.start()

    def request(self, method: str, params: dict[str, Any], timeout_sec: int) -> Any:
        request_id = self._next_request_id()
        response_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            if self._closed:
                raise RuntimeError("ACP 连接已关闭")
            self._pending[request_id] = response_queue
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            try:
                _stream_write_text(self._stdin, json.dumps(payload, ensure_ascii=False) + "\n")
            except Exception as exc:
                self._pending.pop(request_id, None)
                raise RuntimeError(f"发送 ACP 请求失败：{exc}") from exc

        try:
            ok, value = response_queue.get(timeout=max(1, timeout_sec))
        except queue.Empty as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise RuntimeError(f"ACP 请求超时：{method}") from exc

        if ok:
            return value
        raise RuntimeError(str(value))

    def prompt(
        self,
        session_id: str,
        prompt_blocks: list[dict[str, Any]],
        timeout_sec: int,
    ) -> tuple[Any, list[dict[str, Any]], list[str]]:
        handle = self.start_prompt(session_id, prompt_blocks)
        aggregated_updates: list[dict[str, Any]] = []
        aggregated_notes: list[str] = []
        try:
            while True:
                status, payload, updates, notes = self.wait_prompt_event(handle, timeout_sec)
                aggregated_updates.extend(updates)
                aggregated_notes.extend(notes)
                if status == "permission":
                    option_id = self._default_permission_option_id(payload)
                    self.respond_permission(
                        int(payload.get("request_id") or 0),
                        option_id=option_id,
                    )
                    if option_id:
                        aggregated_notes.append(
                            "ACP 智能体请求权限，ResearchOS 已自动返回默认结果。"
                        )
                    else:
                        aggregated_notes.append(
                            "ACP 智能体请求权限，但未找到可选项，已取消该请求。"
                        )
                    continue
                return payload, aggregated_updates, aggregated_notes
        except Exception:
            _updates, _notes = self._end_capture(session_id)
            aggregated_updates.extend(_updates)
            aggregated_notes.extend(_notes)
            if aggregated_notes:
                raise RuntimeError("; ".join(dict.fromkeys(aggregated_notes)))
            raise

    def start_prompt(
        self, session_id: str, prompt_blocks: list[dict[str, Any]]
    ) -> _StreamingPromptHandle:
        request_id = self._next_request_id()
        response_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            if self._closed:
                raise RuntimeError("ACP 连接已关闭")
            self._pending[request_id] = response_queue
            self._permission_events[session_id] = queue.Queue()
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": prompt_blocks,
                },
            }
            self._begin_capture(session_id)
            try:
                _stream_write_text(self._stdin, json.dumps(payload, ensure_ascii=False) + "\n")
            except Exception as exc:
                self._pending.pop(request_id, None)
                self._end_capture(session_id)
                raise RuntimeError(f"发送 ACP 请求失败：{exc}") from exc
        return _StreamingPromptHandle(
            session_id=session_id,
            request_id=request_id,
            response_queue=response_queue,
        )

    def wait_prompt_event(
        self,
        handle: _StreamingPromptHandle,
        timeout_sec: int,
    ) -> tuple[str, Any, list[dict[str, Any]], list[str]]:
        deadline = time.monotonic() + max(1, timeout_sec)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._end_capture(handle.session_id)
                with self._lock:
                    self._pending.pop(handle.request_id, None)
                raise RuntimeError("ACP 请求超时：session/prompt")

            try:
                ok, value = handle.response_queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                ok = None
                value = None

            if ok is not None:
                updates, notes = self._end_capture(handle.session_id)
                if ok:
                    return "completed", value, updates, notes
                raise RuntimeError(str(value))

            with self._lock:
                permission_queue = self._permission_events.setdefault(
                    handle.session_id, queue.Queue()
                )
            try:
                permission_event = permission_queue.get_nowait()
            except queue.Empty:
                continue
            updates, notes = self._drain_capture(handle.session_id)
            return "permission", permission_event, updates, notes

    def respond_permission(self, request_id: int, *, option_id: str | None = None) -> None:
        if request_id <= 0:
            raise RuntimeError("无效的 ACP 权限请求标识")
        if option_id:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": option_id,
                    }
                },
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "outcome": {
                        "outcome": "cancelled",
                    }
                },
            }
        with self._lock:
            if self._closed:
                raise RuntimeError("ACP 连接已关闭")
            try:
                _stream_write_text(self._stdin, json.dumps(response, ensure_ascii=False) + "\n")
            except Exception as exc:
                raise RuntimeError(f"发送 ACP 权限响应失败：{exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._reject_all_pending("ACP 连接已关闭")
        with contextlib.suppress(Exception):
            self._stdin.close()
        with contextlib.suppress(Exception):
            self._stdout.close()
        if self._stderr is not None:
            with contextlib.suppress(Exception):
                self._stderr.close()
        with contextlib.suppress(Exception):
            self._close_callback()

    def stderr_snapshot(self) -> str:
        with self._lock:
            return _trim_text("".join(self._stderr_chunks))

    def _next_request_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _reader_loop(self) -> None:
        try:
            while True:
                raw_line = self._stdout.readline()
                if raw_line in {"", b""}:
                    break
                line = _stream_read_text(raw_line).strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._handle_message(message)
        except Exception as exc:
            self._reject_all_pending(f"ACP 读取失败：{exc}")
            return
        self._reject_all_pending("ACP 进程已退出")

    def _stderr_loop(self) -> None:
        try:
            while self._stderr is not None:
                raw_line = self._stderr.readline()
                if raw_line in {"", b""}:
                    break
                line = _stream_read_text(raw_line)
                if not line:
                    continue
                with self._lock:
                    self._stderr_chunks.append(line)
                    joined = "".join(self._stderr_chunks)
                    if len(joined) > _MAX_STDERR_CHARS:
                        self._stderr_chunks = [joined[-_MAX_STDERR_CHARS:]]
        except Exception:
            return

    def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and "method" not in message:
            request_id = int(message.get("id") or 0)
            with self._lock:
                pending = self._pending.pop(request_id, None)
            if pending is None:
                return
            if message.get("error"):
                error_payload = message.get("error") or {}
                pending.put((False, str(error_payload.get("message") or "ACP 请求失败")))
            else:
                pending.put((True, message.get("result")))
            return

        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "session/update":
            session_id = clean_text((params or {}).get("sessionId"))
            update = (params or {}).get("update")
            if session_id and isinstance(update, dict):
                with self._lock:
                    self._buffers.setdefault(session_id, []).append(dict(update))
            return

        if method == "session/request_permission" and "id" in message:
            session_id = clean_text((params or {}).get("sessionId"))
            permission_event = {
                "request_id": int(message.get("id") or 0),
                "session_id": session_id,
                "params": dict(params or {}),
                "options": [
                    dict(option)
                    for option in ((params or {}).get("options") or [])
                    if isinstance(option, dict)
                ],
            }
            with self._lock:
                if session_id:
                    self._permission_events.setdefault(session_id, queue.Queue()).put(
                        permission_event
                    )
                    self._permission_notes.setdefault(session_id, []).append(
                        "ACP 智能体请求权限，等待确认。"
                    )

    def _begin_capture(self, session_id: str) -> None:
        with self._lock:
            self._buffers[session_id] = []
            self._permission_notes[session_id] = []

    def _end_capture(self, session_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        with self._lock:
            updates = list(self._buffers.pop(session_id, []))
            notes = list(self._permission_notes.pop(session_id, []))
            self._permission_events.pop(session_id, None)
        return updates, notes

    def _drain_capture(self, session_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        with self._lock:
            updates = list(self._buffers.get(session_id, []))
            notes = list(self._permission_notes.get(session_id, []))
            self._buffers[session_id] = []
            self._permission_notes[session_id] = []
        return updates, notes

    def _default_permission_option_id(self, payload: dict[str, Any]) -> str | None:
        options = payload.get("options") if isinstance(payload.get("options"), list) else []
        selected_id: str | None = None
        for option in options:
            if not isinstance(option, dict):
                continue
            kind = clean_text(option.get("kind"))
            option_id = clean_text(option.get("optionId"))
            if kind.startswith("reject") and option_id:
                selected_id = option_id
                break
        if selected_id:
            return selected_id
        for option in options:
            if not isinstance(option, dict):
                continue
            option_id = clean_text(option.get("optionId"))
            if option_id:
                return option_id
        return None

    def _reject_all_pending(self, message: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            with contextlib.suppress(Exception):
                item.put((False, message))


class _HttpRpcClient:
    def __init__(self, *, url: str, headers: dict[str, str], timeout_sec: int) -> None:
        self._timeout_sec = max(5, timeout_sec)
        self._client = httpx.Client(
            headers=headers,
            timeout=self._timeout_sec,
            transport=httpx.HTTPTransport(retries=0),
            cookies=httpx.Cookies(),
        )
        self._url = url
        self._lock = threading.RLock()
        self._request_id = 0
        self._stderr_chunks: list[str] = []
        self._cookie_jar = http.cookiejar.CookieJar()
        self._closed = False
        self._buffers: dict[str, list[dict[str, Any]]] = {}
        self._permission_notes: dict[str, list[str]] = {}
        self._permission_events: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._prompt_results: dict[int, queue.Queue[tuple[bool, Any]]] = {}

    def request(self, method: str, params: dict[str, Any], timeout_sec: int) -> Any:
        result, _updates, notes = self._execute(
            method,
            params,
            timeout_sec=timeout_sec,
            capture_session_id=None,
        )
        if notes:
            raise RuntimeError("; ".join(notes))
        return result

    def prompt(
        self,
        session_id: str,
        prompt_blocks: list[dict[str, Any]],
        timeout_sec: int,
    ) -> tuple[Any, list[dict[str, Any]], list[str]]:
        return self._execute(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": prompt_blocks,
            },
            timeout_sec=timeout_sec,
            capture_session_id=session_id,
        )

    def start_prompt(
        self, session_id: str, prompt_blocks: list[dict[str, Any]]
    ) -> _StreamingPromptHandle:
        request_id = self._next_request_id()
        response_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "session/prompt",
            "params": {
                "sessionId": session_id,
                "prompt": prompt_blocks,
            },
        }
        with self._lock:
            if self._closed:
                raise RuntimeError("ACP 连接已关闭")
            self._prompt_results[request_id] = response_queue
            self._permission_events[session_id] = queue.Queue()
            self._begin_capture(session_id)
        thread = threading.Thread(
            target=self._prompt_stream_loop,
            args=(request_id, session_id, payload, response_queue),
            daemon=True,
        )
        thread.start()
        return _StreamingPromptHandle(
            session_id=session_id,
            request_id=request_id,
            response_queue=response_queue,
        )

    def wait_prompt_event(
        self,
        handle: _StreamingPromptHandle,
        timeout_sec: int,
    ) -> tuple[str, Any, list[dict[str, Any]], list[str]]:
        deadline = time.monotonic() + max(1, timeout_sec)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._end_capture(handle.session_id)
                with self._lock:
                    self._prompt_results.pop(handle.request_id, None)
                raise RuntimeError("ACP 请求超时：session/prompt")

            try:
                ok, value = handle.response_queue.get(timeout=min(0.1, remaining))
            except queue.Empty:
                ok = None
                value = None

            if ok is not None:
                updates, notes = self._end_capture(handle.session_id)
                if ok:
                    return "completed", value, updates, notes
                raise RuntimeError(str(value))

            with self._lock:
                permission_queue = self._permission_events.setdefault(
                    handle.session_id, queue.Queue()
                )
            try:
                permission_event = permission_queue.get_nowait()
            except queue.Empty:
                continue
            updates, notes = self._drain_capture(handle.session_id)
            return "permission", permission_event, updates, notes

    def respond_permission(self, request_id: int, *, option_id: str | None = None) -> None:
        if request_id <= 0:
            raise RuntimeError("无效的 ACP 权限请求标识")
        if option_id:
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": option_id,
                    }
                },
            }
        else:
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "outcome": {
                        "outcome": "cancelled",
                    }
                },
            }
        try:
            response = self._client.post(
                self._url,
                json=payload,
                timeout=self._request_timeout(self._timeout_sec),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"发送 ACP 权限响应失败：{exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._reject_all_pending("ACP 连接已关闭")
        self._client.close()

    def stderr_snapshot(self) -> str:
        return _trim_text("".join(self._stderr_chunks))

    def _next_request_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _execute(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_sec: int,
        capture_session_id: str | None,
    ) -> tuple[Any, list[dict[str, Any]], list[str]]:
        request_id = self._next_request_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        updates: list[dict[str, Any]] = []
        notes: list[str] = []
        result: Any = None
        error_message: str | None = None
        try:
            with self._client.stream(
                "POST",
                self._url,
                json=payload,
                timeout=self._request_timeout(timeout_sec),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "id" in message and "method" not in message:
                        if int(message.get("id") or 0) != request_id:
                            continue
                        if message.get("error"):
                            error_payload = message.get("error") or {}
                            error_message = str(error_payload.get("message") or "ACP 请求失败")
                        else:
                            result = message.get("result")
                        continue
                    current_method = str(message.get("method") or "")
                    params_payload = (
                        message.get("params") if isinstance(message.get("params"), dict) else {}
                    )
                    if current_method == "session/update":
                        session_id = clean_text((params_payload or {}).get("sessionId"))
                        update = (params_payload or {}).get("update")
                        if (
                            capture_session_id
                            and session_id == capture_session_id
                            and isinstance(update, dict)
                        ):
                            updates.append(dict(update))
                        continue
                    if current_method == "session/request_permission":
                        notes.append("HTTP ACP 返回了权限请求，当前版本还没有接通交互式确认。")
        except httpx.HTTPError as exc:
            raise RuntimeError(f"HTTP ACP 请求失败：{exc}") from exc

        if error_message:
            raise RuntimeError(error_message)
        if result is None:
            raise RuntimeError(f"HTTP ACP 没有返回 {method} 的结果")
        return result, updates, notes

    def _request_timeout(
        self, timeout_sec: int, *, read_timeout: float | None = None
    ) -> httpx.Timeout:
        base_timeout = max(5, timeout_sec)
        return httpx.Timeout(
            connect=base_timeout,
            write=base_timeout,
            pool=base_timeout,
            read=read_timeout if read_timeout is not None else base_timeout,
        )

    def _prompt_stream_loop(
        self,
        request_id: int,
        session_id: str,
        payload: dict[str, Any],
        response_queue: queue.Queue[tuple[bool, Any]],
    ) -> None:
        try:
            with self._client.stream(
                "POST",
                self._url,
                json=payload,
                timeout=self._request_timeout(self._timeout_sec, read_timeout=None),
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if self._handle_prompt_stream_message(
                        request_id=request_id,
                        session_id=session_id,
                        message=message,
                        response_queue=response_queue,
                    ):
                        return
        except httpx.HTTPError as exc:
            self._complete_prompt(request_id, response_queue, False, f"HTTP ACP 请求失败：{exc}")
            return
        except Exception as exc:
            self._complete_prompt(request_id, response_queue, False, f"HTTP ACP 读取失败：{exc}")
            return
        self._complete_prompt(request_id, response_queue, False, "HTTP ACP 在返回结果前关闭了连接")

    def _handle_prompt_stream_message(
        self,
        *,
        request_id: int,
        session_id: str,
        message: dict[str, Any],
        response_queue: queue.Queue[tuple[bool, Any]],
    ) -> bool:
        if "id" in message and "method" not in message:
            if int(message.get("id") or 0) != request_id:
                return False
            if message.get("error"):
                error_payload = message.get("error") or {}
                self._complete_prompt(
                    request_id,
                    response_queue,
                    False,
                    str(error_payload.get("message") or "ACP 请求失败"),
                )
            else:
                self._complete_prompt(request_id, response_queue, True, message.get("result"))
            return True

        current_method = str(message.get("method") or "")
        params_payload = message.get("params") if isinstance(message.get("params"), dict) else {}
        if current_method == "session/update":
            update_session_id = clean_text((params_payload or {}).get("sessionId"))
            update = (params_payload or {}).get("update")
            if update_session_id == session_id and isinstance(update, dict):
                with self._lock:
                    self._buffers.setdefault(session_id, []).append(dict(update))
            return False
        if current_method == "session/request_permission" and "id" in message:
            permission_session_id = clean_text((params_payload or {}).get("sessionId"))
            if permission_session_id == session_id:
                permission_event = {
                    "request_id": int(message.get("id") or 0),
                    "session_id": permission_session_id,
                    "params": dict(params_payload or {}),
                    "options": [
                        dict(option)
                        for option in ((params_payload or {}).get("options") or [])
                        if isinstance(option, dict)
                    ],
                }
                with self._lock:
                    self._permission_events.setdefault(session_id, queue.Queue()).put(
                        permission_event
                    )
                    self._permission_notes.setdefault(session_id, []).append(
                        "ACP 智能体请求权限，等待确认。"
                    )
            return False
        return False

    def _complete_prompt(
        self,
        request_id: int,
        response_queue: queue.Queue[tuple[bool, Any]],
        ok: bool,
        value: Any,
    ) -> None:
        with self._lock:
            self._prompt_results.pop(request_id, None)
        with contextlib.suppress(Exception):
            response_queue.put_nowait((ok, value))

    def _begin_capture(self, session_id: str) -> None:
        with self._lock:
            self._buffers[session_id] = []
            self._permission_notes[session_id] = []

    def _end_capture(self, session_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        with self._lock:
            updates = list(self._buffers.pop(session_id, []))
            notes = list(self._permission_notes.pop(session_id, []))
            self._permission_events.pop(session_id, None)
        return updates, notes

    def _drain_capture(self, session_id: str) -> tuple[list[dict[str, Any]], list[str]]:
        with self._lock:
            updates = list(self._buffers.get(session_id, []))
            notes = list(self._permission_notes.get(session_id, []))
            self._buffers[session_id] = []
            self._permission_notes[session_id] = []
        return updates, notes

    def _reject_all_pending(self, message: str) -> None:
        with self._lock:
            pending = list(self._prompt_results.items())
            self._prompt_results.clear()
        for _request_id, item in pending:
            with contextlib.suppress(Exception):
                item.put_nowait((False, message))


@dataclass
class ManagedAcpConnection:
    name: str
    label: str
    transport: str
    client: _RpcClient
    connected_at: float
    workspace_server_id: str | None

    def close(self) -> None:
        self.client.close()


@dataclass
class PendingAcpPrompt:
    action_id: str
    server_name: str
    server_label: str
    transport: str
    workspace_path: str
    workspace_server_id: str | None
    prompt_handle: _StreamingPromptHandle
    acp_session_id: str
    permission_request_id: int
    permission_options: list[dict[str, Any]]
    timeout_sec: int


class AcpRegistryService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._connections: dict[str, ManagedAcpConnection] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._pending_prompts: dict[str, PendingAcpPrompt] = {}

    def _ensure_store(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not _REGISTRY_PATH.exists():
            _REGISTRY_PATH.write_text(
                json.dumps(
                    {"version": 1, "default_server": None, "servers": {}},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

    def _normalize_server(self, name: str, raw: dict[str, Any]) -> dict[str, Any]:
        transport = clean_text(raw.get("transport") or "stdio").lower()
        if transport not in {"stdio", "http"}:
            raise ValueError(f"ACP 服务 {name} 的 transport 仅支持 stdio 或 http")

        server = {
            "name": name,
            "label": clean_text(raw.get("label")) or name,
            "transport": transport,
            "command": clean_text(raw.get("command")) or None,
            "args": [str(item).strip() for item in (raw.get("args") or []) if str(item).strip()],
            "cwd": clean_text(raw.get("cwd")) or None,
            "env": {
                str(key): str(value)
                for key, value in (raw.get("env") or {}).items()
                if str(key).strip()
            },
            "url": clean_text(raw.get("url")) or None,
            "headers": {
                str(key): str(value)
                for key, value in (raw.get("headers") or {}).items()
                if str(key).strip()
            },
            "enabled": bool(raw.get("enabled", True)),
            "workspace_server_id": clean_text(raw.get("workspace_server_id")) or None,
            "timeout_sec": max(5, min(int(raw.get("timeout_sec") or _DEFAULT_TIMEOUT_SEC), 900)),
        }
        if transport == "stdio" and not server["command"]:
            raise ValueError(f"ACP 服务 {name} 缺少 command")
        if transport == "http" and not server["url"]:
            raise ValueError(f"ACP 服务 {name} 缺少 url")
        return server

    def _load_registry(self) -> dict[str, Any]:
        self._ensure_store()
        payload = _load_json_file(_REGISTRY_PATH)
        if not isinstance(payload, dict):
            payload = {"version": 1, "default_server": None, "servers": {}}
        raw_servers = payload.get("servers")
        normalized_servers: dict[str, dict[str, Any]] = {}
        if isinstance(raw_servers, dict):
            for name, raw in raw_servers.items():
                if not isinstance(raw, dict):
                    continue
                normalized_name = _slugify(name)
                normalized_servers[normalized_name] = self._normalize_server(normalized_name, raw)

        default_server = clean_text(payload.get("default_server")) or None
        if default_server and default_server not in normalized_servers:
            default_server = None
        if not default_server:
            for name, server in normalized_servers.items():
                if server.get("enabled"):
                    default_server = name
                    break
        return {
            "version": 1,
            "default_server": default_server,
            "servers": normalized_servers,
        }

    def _save_registry(self, config: dict[str, Any]) -> None:
        self._ensure_store()
        persisted_servers = {
            name: {
                key: value for key, value in server.items() if key != "name" and value is not None
            }
            for name, server in (config.get("servers") or {}).items()
        }
        _REGISTRY_PATH.write_text(
            json.dumps(
                {
                    "version": 1,
                    "default_server": config.get("default_server"),
                    "servers": persisted_servers,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def get_config(self) -> dict[str, Any]:
        return self._load_registry()

    def update_config(self, next_config: dict[str, Any]) -> dict[str, Any]:
        previous = self.get_config()
        raw_servers = next_config.get("servers") if isinstance(next_config, dict) else {}
        if not isinstance(raw_servers, dict):
            raise ValueError("ACP 配置格式不正确")
        normalized_servers: dict[str, dict[str, Any]] = {}
        for name, raw in raw_servers.items():
            if not isinstance(raw, dict):
                continue
            normalized_name = _slugify(name)
            normalized_servers[normalized_name] = self._normalize_server(normalized_name, raw)

        default_server = clean_text(next_config.get("default_server")) or None
        if default_server:
            default_server = _slugify(default_server)
        if default_server and default_server not in normalized_servers:
            raise ValueError("默认 ACP 服务不存在")
        if not default_server:
            for name, server in normalized_servers.items():
                if server.get("enabled"):
                    default_server = name
                    break

        merged = {
            "version": 1,
            "default_server": default_server,
            "servers": normalized_servers,
        }
        self._save_registry(merged)

        changed_or_removed: set[str] = set()
        for name, server in previous.get("servers", {}).items():
            next_server = normalized_servers.get(name)
            if next_server is None:
                changed_or_removed.add(name)
                continue
            if server != next_server:
                changed_or_removed.add(name)
                continue
            if not next_server.get("enabled", True):
                changed_or_removed.add(name)
        for name in changed_or_removed:
            self.disconnect_server(name)
        return self.get_config()

    def list_servers(self) -> list[dict[str, Any]]:
        config = self.get_config()
        with self._lock:
            connections = dict(self._connections)
            states = dict(self._states)
        items: list[dict[str, Any]] = []
        for name, server in sorted(config["servers"].items()):
            connection = connections.get(name)
            state = states.get(name, {})
            connected = connection is not None
            items.append(
                {
                    **server,
                    "status": "connected"
                    if connected
                    else "disabled"
                    if not server["enabled"]
                    else "disconnected",
                    "connected": connected,
                    "last_error": state.get("last_error"),
                    "last_connected_at": connection.connected_at
                    if connection
                    else state.get("last_connected_at"),
                    "last_disconnected_at": state.get("last_disconnected_at"),
                    "default": config.get("default_server") == name,
                }
            )
        return items

    def runtime_snapshot(self) -> dict[str, Any]:
        items = self.list_servers()
        connected_count = sum(1 for item in items if item["connected"])
        enabled_count = sum(1 for item in items if item["enabled"])
        config = self.get_config()
        return {
            "available": True,
            "connected_count": connected_count,
            "enabled_count": enabled_count,
            "server_count": len(items),
            "default_server": config.get("default_server"),
            "message": "ACP 服务已独立于 API key 和 opencode runtime 进行管理。",
        }

    def connect_server(self, name: str) -> dict[str, Any]:
        config = self.get_config()
        normalized_name = _slugify(name)
        server = config["servers"].get(normalized_name)
        if not server:
            raise ValueError(f"未找到 ACP 服务：{name}")
        if not server["enabled"]:
            raise ValueError(f"ACP 服务已禁用：{name}")

        with self._lock:
            existing = self._connections.get(normalized_name)
        if existing is not None:
            return next(item for item in self.list_servers() if item["name"] == normalized_name)

        if server["transport"] == "stdio":
            client = self._connect_stdio_server(server)
        else:
            client = self._connect_http_server(server)

        timeout_sec = int(server.get("timeout_sec") or _DEFAULT_TIMEOUT_SEC)
        try:
            client.request(
                "initialize",
                {
                    "protocolVersion": 1,
                    "clientCapabilities": {
                        "fs": {
                            "readTextFile": True,
                            "writeTextFile": True,
                        }
                    },
                },
                timeout_sec,
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                client.close()
            with self._lock:
                state = self._states.setdefault(normalized_name, {})
                state["last_error"] = str(exc)
                state["last_disconnected_at"] = _now_ts()
            raise

        managed = ManagedAcpConnection(
            name=normalized_name,
            label=str(server["label"]),
            transport=str(server["transport"]),
            client=client,
            connected_at=_now_ts(),
            workspace_server_id=clean_text(server.get("workspace_server_id")) or None,
        )
        with self._lock:
            self._connections[normalized_name] = managed
            self._states[normalized_name] = {
                "last_error": None,
                "last_connected_at": managed.connected_at,
                "last_disconnected_at": None,
            }
        return next(item for item in self.list_servers() if item["name"] == normalized_name)

    def disconnect_server(self, name: str) -> dict[str, Any]:
        normalized_name = _slugify(name)
        with self._lock:
            connection = self._connections.pop(normalized_name, None)
            stale_action_ids = [
                action_id
                for action_id, pending in self._pending_prompts.items()
                if pending.server_name == normalized_name
            ]
            for action_id in stale_action_ids:
                self._pending_prompts.pop(action_id, None)
        if connection is not None:
            connection.close()
            with self._lock:
                state = self._states.setdefault(normalized_name, {})
                state["last_disconnected_at"] = _now_ts()
        return next(
            (item for item in self.list_servers() if item["name"] == normalized_name),
            {
                "name": normalized_name,
                "status": "disconnected",
                "connected": False,
            },
        )

    def test_server(
        self,
        name: str,
        *,
        prompt: str,
        workspace_path: str | None = None,
        workspace_server_id: str | None = None,
        timeout_sec: int = 180,
    ) -> dict[str, Any]:
        return self.execute_prompt(
            prompt=prompt,
            workspace_path=workspace_path,
            workspace_server_id=workspace_server_id,
            timeout_sec=timeout_sec,
            server_name=name,
        )

    def execute_prompt(
        self,
        *,
        prompt: str,
        workspace_path: str | None,
        workspace_server_id: str | None = None,
        timeout_sec: int = 600,
        server_name: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        if not clean_text(prompt):
            raise ValueError("prompt 不能为空")

        config = self.get_config()
        selected_name = _slugify(server_name or config.get("default_server") or "")
        if not selected_name:
            raise ValueError("当前还没有配置可用的 ACP 服务")
        server = config["servers"].get(selected_name)
        if not server:
            raise ValueError(f"未找到 ACP 服务：{selected_name}")
        if not server.get("enabled", True):
            raise ValueError(f"ACP 服务已禁用：{selected_name}")

        if server["transport"] == "stdio":
            session_cwd, effective_server_id = self._resolve_stdio_session_cwd(
                server,
                workspace_path=workspace_path,
                workspace_server_id=workspace_server_id,
            )
        else:
            session_cwd, effective_server_id = self._resolve_http_session_cwd(
                workspace_path=workspace_path,
                workspace_server_id=workspace_server_id,
            )

        connection = self._ensure_connection(selected_name)
        try:
            if session_id and isinstance(connection.client, _InteractiveRpcClient):
                interactive_result = self._run_prompt_once_interactive(
                    connection=connection,
                    session_cwd=session_cwd,
                    prompt=prompt,
                    timeout_sec=timeout_sec,
                )
                if interactive_result.get("paused"):
                    return {
                        "config_id": "custom_acp",
                        "agent_type": "custom_acp",
                        "label": "Custom ACP",
                        "server_name": selected_name,
                        "server_label": server["label"],
                        "transport": server["transport"],
                        "workspace_path": session_cwd,
                        "workspace_server_id": effective_server_id,
                        "execution_mode": "ssh" if effective_server_id else "local",
                        "duration_ms": int(interactive_result.get("duration_ms") or 0),
                        "success": False,
                        "paused": True,
                        "content": str(interactive_result.get("content") or ""),
                        "updates": list(interactive_result.get("updates") or []),
                        "notes": list(interactive_result.get("notes") or []),
                        "stop_reason": None,
                        "stderr": connection.client.stderr_snapshot(),
                        "pending_action_id": interactive_result.get("pending_action_id"),
                        "permission_request": interactive_result.get("permission_request"),
                    }
                prompt_result = interactive_result["prompt_result"]
                content = str(interactive_result.get("content") or "")
                updates = list(interactive_result.get("updates") or [])
                notes = list(interactive_result.get("notes") or [])
                duration_ms = int(interactive_result.get("duration_ms") or 0)
            else:
                prompt_result, content, updates, notes, duration_ms = self._run_prompt_once(
                    connection=connection,
                    session_cwd=session_cwd,
                    prompt=prompt,
                    timeout_sec=timeout_sec,
                )
        except Exception as exc:
            if self._should_retry_after_error(exc):
                self.disconnect_server(selected_name)
                connection = self._ensure_connection(selected_name)
                try:
                    if session_id and isinstance(connection.client, _InteractiveRpcClient):
                        interactive_result = self._run_prompt_once_interactive(
                            connection=connection,
                            session_cwd=session_cwd,
                            prompt=prompt,
                            timeout_sec=timeout_sec,
                        )
                        if interactive_result.get("paused"):
                            return {
                                "config_id": "custom_acp",
                                "agent_type": "custom_acp",
                                "label": "Custom ACP",
                                "server_name": selected_name,
                                "server_label": server["label"],
                                "transport": server["transport"],
                                "workspace_path": session_cwd,
                                "workspace_server_id": effective_server_id,
                                "execution_mode": "ssh" if effective_server_id else "local",
                                "duration_ms": int(interactive_result.get("duration_ms") or 0),
                                "success": False,
                                "paused": True,
                                "content": str(interactive_result.get("content") or ""),
                                "updates": list(interactive_result.get("updates") or []),
                                "notes": list(interactive_result.get("notes") or []),
                                "stop_reason": None,
                                "stderr": connection.client.stderr_snapshot(),
                                "pending_action_id": interactive_result.get("pending_action_id"),
                                "permission_request": interactive_result.get("permission_request"),
                            }
                        prompt_result = interactive_result["prompt_result"]
                        content = str(interactive_result.get("content") or "")
                        updates = list(interactive_result.get("updates") or [])
                        notes = list(interactive_result.get("notes") or [])
                        duration_ms = int(interactive_result.get("duration_ms") or 0)
                    else:
                        prompt_result, content, updates, notes, duration_ms = self._run_prompt_once(
                            connection=connection,
                            session_cwd=session_cwd,
                            prompt=prompt,
                            timeout_sec=timeout_sec,
                        )
                except Exception as retry_exc:
                    with self._lock:
                        state = self._states.setdefault(selected_name, {})
                        state["last_error"] = str(retry_exc)
                    raise
            else:
                with self._lock:
                    state = self._states.setdefault(selected_name, {})
                    state["last_error"] = str(exc)
                raise

        with self._lock:
            state = self._states.setdefault(selected_name, {})
            state["last_error"] = None
            state["last_connected_at"] = connection.connected_at

        return {
            "config_id": "custom_acp",
            "agent_type": "custom_acp",
            "label": "Custom ACP",
            "server_name": selected_name,
            "server_label": server["label"],
            "transport": server["transport"],
            "workspace_path": session_cwd,
            "workspace_server_id": effective_server_id,
            "execution_mode": "ssh" if effective_server_id else "local",
            "duration_ms": duration_ms,
            "success": True,
            "content": content,
            "updates": updates,
            "stop_reason": (prompt_result or {}).get("stopReason"),
            "stderr": connection.client.stderr_snapshot(),
            "notes": notes,
        }

    def respond_to_pending_permission(
        self,
        action_id: str,
        *,
        response: str,
    ) -> dict[str, Any]:
        with self._lock:
            pending = self._pending_prompts.get(action_id)
        if pending is None:
            raise RuntimeError("ACP 权限请求已失效，请重新发起对话。")

        connection = self._ensure_connection(pending.server_name)
        client = connection.client
        if not isinstance(client, _InteractiveRpcClient):
            raise RuntimeError("当前 ACP 连接不支持交互式权限恢复。")

        option_id = self._select_permission_option_id(
            pending.permission_options,
            response=response,
        )
        client.respond_permission(pending.permission_request_id, option_id=option_id)

        started_at = time.perf_counter()
        status, payload, updates, notes = client.wait_prompt_event(
            pending.prompt_handle,
            pending.timeout_sec,
        )
        content = self._collect_agent_text(updates)
        if status == "permission":
            next_pending = PendingAcpPrompt(
                action_id=action_id,
                server_name=pending.server_name,
                server_label=pending.server_label,
                transport=pending.transport,
                workspace_path=pending.workspace_path,
                workspace_server_id=pending.workspace_server_id,
                prompt_handle=pending.prompt_handle,
                acp_session_id=pending.acp_session_id,
                permission_request_id=int(payload.get("request_id") or 0),
                permission_options=[
                    dict(option)
                    for option in (payload.get("options") or [])
                    if isinstance(option, dict)
                ],
                timeout_sec=pending.timeout_sec,
            )
            self._store_pending_prompt(next_pending)
            return {
                "paused": True,
                "pending_action_id": action_id,
                "content": content,
                "updates": updates,
                "notes": notes,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "permission_request": self._serialize_permission_request(payload),
                "transport": pending.transport,
                "server_name": pending.server_name,
                "server_label": pending.server_label,
                "workspace_path": pending.workspace_path,
                "workspace_server_id": pending.workspace_server_id,
            }

        self._delete_pending_prompt(action_id)
        return {
            "paused": False,
            "prompt_result": payload,
            "content": content,
            "updates": updates,
            "notes": notes,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
            "transport": pending.transport,
            "server_name": pending.server_name,
            "server_label": pending.server_label,
            "workspace_path": pending.workspace_path,
            "workspace_server_id": pending.workspace_server_id,
        }

    def discard_pending_permission(self, action_id: str) -> bool:
        normalized = clean_text(action_id)
        if not normalized:
            return False
        with self._lock:
            pending = self._pending_prompts.pop(normalized, None)
        return pending is not None

    def get_backend_summary(self) -> dict[str, Any]:
        config = self.get_config()
        items = self.list_servers()
        default_server = clean_text(config.get("default_server")) or None
        default_item = next((item for item in items if item["name"] == default_server), None)
        summary = {
            "server_count": len(items),
            "enabled_count": sum(1 for item in items if item["enabled"]),
            "connected_count": sum(1 for item in items if item["connected"]),
            "default_server": default_server,
            "default_server_label": default_item.get("label") if default_item else None,
            "default_transport": default_item.get("transport") if default_item else None,
            "default_connected": bool(default_item and default_item.get("connected")),
            "default_workspace_server_id": default_item.get("workspace_server_id")
            if default_item
            else None,
            "chat_supported": True,
            "chat_ready": False,
            "chat_status": "requires_service",
            "chat_status_label": "未绑定 ACP",
            "chat_blocked_reason": "Custom ACP 还没有绑定独立 ACP 服务。",
        }
        if not default_item:
            return summary
        if not default_item.get("enabled"):
            summary["chat_status_label"] = "ACP 已禁用"
            summary["chat_blocked_reason"] = f"{default_item['label']} 当前已禁用。"
            return summary
        summary["chat_ready"] = True
        summary["chat_status"] = "ready"
        summary["chat_status_label"] = (
            f"ACP 已连接 · {default_item['label']}"
            if default_item.get("connected")
            else f"ACP 待连接 · {default_item['label']}"
        )
        summary["chat_blocked_reason"] = None
        return summary

    def close_all(self) -> None:
        with self._lock:
            names = list(self._connections)
        for name in names:
            self.disconnect_server(name)

    def _run_prompt_once(
        self,
        *,
        connection: ManagedAcpConnection,
        session_cwd: str,
        prompt: str,
        timeout_sec: int,
    ) -> tuple[Any, str, list[dict[str, Any]], list[str], int]:
        started_at = time.perf_counter()
        session_result = connection.client.request(
            "session/new",
            {
                "cwd": session_cwd,
                "mcpServers": [],
            },
            timeout_sec,
        )
        session_id = clean_text((session_result or {}).get("sessionId"))
        if not session_id:
            raise RuntimeError("ACP 没有返回 sessionId")
        prompt_result, updates, notes = connection.client.prompt(
            session_id,
            [{"type": "text", "text": prompt}],
            timeout_sec,
        )
        content = self._collect_agent_text(updates)
        if not content:
            raise RuntimeError("ACP 没有返回可用的 agent_message_chunk")
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        return prompt_result, content, updates, notes, duration_ms

    def _run_prompt_once_interactive(
        self,
        *,
        connection: ManagedAcpConnection,
        session_cwd: str,
        prompt: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        client = connection.client
        if not isinstance(client, _InteractiveRpcClient):
            raise RuntimeError("当前 ACP 连接不支持交互式权限确认。")

        started_at = time.perf_counter()
        session_result = client.request(
            "session/new",
            {
                "cwd": session_cwd,
                "mcpServers": [],
            },
            timeout_sec,
        )
        session_id = clean_text((session_result or {}).get("sessionId"))
        if not session_id:
            raise RuntimeError("ACP 没有返回 sessionId")

        handle = client.start_prompt(
            session_id,
            [{"type": "text", "text": prompt}],
        )
        status, payload, updates, notes = client.wait_prompt_event(handle, timeout_sec)
        content = self._collect_agent_text(updates)
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        if status == "permission":
            action_id = f"acp_permission_{_slugify(connection.name)}_{session_id}_{int(payload.get('request_id') or 0)}"
            pending = PendingAcpPrompt(
                action_id=action_id,
                server_name=connection.name,
                server_label=connection.label,
                transport=connection.transport,
                workspace_path=session_cwd,
                workspace_server_id=connection.workspace_server_id,
                prompt_handle=handle,
                acp_session_id=session_id,
                permission_request_id=int(payload.get("request_id") or 0),
                permission_options=[
                    dict(option)
                    for option in (payload.get("options") or [])
                    if isinstance(option, dict)
                ],
                timeout_sec=timeout_sec,
            )
            self._store_pending_prompt(pending)
            return {
                "paused": True,
                "pending_action_id": action_id,
                "content": content,
                "updates": updates,
                "notes": notes,
                "duration_ms": duration_ms,
                "permission_request": self._serialize_permission_request(payload),
            }

        return {
            "paused": False,
            "prompt_result": payload,
            "content": content,
            "updates": updates,
            "notes": notes,
            "duration_ms": duration_ms,
        }

    def _store_pending_prompt(self, pending: PendingAcpPrompt) -> None:
        with self._lock:
            self._pending_prompts[pending.action_id] = pending

    def _delete_pending_prompt(self, action_id: str) -> None:
        with self._lock:
            self._pending_prompts.pop(action_id, None)

    def _serialize_permission_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        options = [
            {
                "option_id": clean_text(option.get("optionId")),
                "name": clean_text(option.get("name"))
                or clean_text(option.get("kind"))
                or "Option",
                "kind": clean_text(option.get("kind")),
            }
            for option in (payload.get("options") or [])
            if isinstance(option, dict)
        ]
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        raw_input = (
            dict(tool_call.get("rawInput") or {})
            if isinstance(tool_call.get("rawInput"), dict)
            else {}
        )
        tool_name = clean_text(tool_call.get("kind")) or "custom_acp"
        title = (
            clean_text(tool_call.get("title"))
            or clean_text(tool_call.get("kind"))
            or "ACP 权限请求"
        )
        return {
            "request_id": int(payload.get("request_id") or 0),
            "description": f"{title}（来自 ACP）",
            "tool_name": tool_name,
            "tool_call_id": clean_text(tool_call.get("toolCallId"))
            or f"acp_permission_{int(payload.get('request_id') or 0)}",
            "raw_input": raw_input,
            "options": options,
        }

    def _select_permission_option_id(
        self,
        options: list[dict[str, Any]],
        *,
        response: str,
    ) -> str | None:
        normalized = str(response or "").strip().lower()
        if normalized == "reject":
            for option in options:
                if not isinstance(option, dict):
                    continue
                kind = clean_text(option.get("kind"))
                option_id = clean_text(option.get("optionId"))
                if kind.startswith("reject") and option_id:
                    return option_id
            return None

        if normalized == "always":
            for option in options:
                if not isinstance(option, dict):
                    continue
                kind = clean_text(option.get("kind"))
                option_id = clean_text(option.get("optionId"))
                if "always" in kind and option_id:
                    return option_id

        for option in options:
            if not isinstance(option, dict):
                continue
            kind = clean_text(option.get("kind"))
            option_id = clean_text(option.get("optionId"))
            if kind.startswith("allow") and option_id:
                if normalized == "always" and "always" not in kind:
                    continue
                return option_id
        for option in options:
            if not isinstance(option, dict):
                continue
            option_id = clean_text(option.get("optionId"))
            if option_id:
                return option_id
        return None

    def _should_retry_after_error(self, exc: Exception) -> bool:
        message = str(exc)
        retry_markers = (
            "ACP 连接已关闭",
            "ACP 进程已退出",
            "ACP 读取失败",
            "Broken pipe",
            "Connection reset",
            "connection closed",
            "HTTP ACP 请求失败",
        )
        return any(marker in message for marker in retry_markers)

    def _ensure_connection(self, name: str) -> ManagedAcpConnection:
        normalized_name = _slugify(name)
        with self._lock:
            existing = self._connections.get(normalized_name)
        if existing is not None:
            return existing
        self.connect_server(normalized_name)
        with self._lock:
            connection = self._connections.get(normalized_name)
        if connection is None:
            raise RuntimeError("ACP 连接建立失败")
        return connection

    def _resolve_stdio_session_cwd(
        self,
        server: dict[str, Any],
        *,
        workspace_path: str | None,
        workspace_server_id: str | None,
    ) -> tuple[str, str | None]:
        bound_server_id = clean_text(server.get("workspace_server_id")) or None
        requested_server_id = clean_text(workspace_server_id) or None
        effective_server_id = bound_server_id or requested_server_id
        if bound_server_id and requested_server_id and bound_server_id != requested_server_id:
            raise ValueError(
                f"当前 ACP 服务绑定在 SSH 服务器 {bound_server_id}，与会话目标 {requested_server_id} 不一致。"
            )
        if effective_server_id:
            server_entry = _load_workspace_server_entry(effective_server_id)
            if server_entry is None:
                raise ValueError("未找到 ACP 绑定的 SSH 工作区服务器")
            with open_ssh_session(server_entry) as session:
                if clean_text(workspace_path):
                    cwd = resolve_remote_workspace_path(server_entry, str(workspace_path), session)
                else:
                    cwd = resolve_remote_workspace_path(
                        server_entry,
                        clean_text(server_entry.get("workspace_root")) or ".",
                        session,
                    )
                if remote_stat(session.sftp, cwd) is None:
                    raise ValueError(f"远程工作区不存在：{cwd}")
            return cwd, effective_server_id

        cwd = Path(workspace_path or Path.cwd()).expanduser().resolve()
        if not cwd.exists():
            raise ValueError(f"本地工作区不存在：{cwd}")
        if not cwd.is_dir():
            raise ValueError(f"本地工作区不是目录：{cwd}")
        return str(cwd), None

    def _resolve_http_session_cwd(
        self,
        *,
        workspace_path: str | None,
        workspace_server_id: str | None,
    ) -> tuple[str, str | None]:
        raw_path = clean_text(workspace_path)
        if raw_path:
            return raw_path, clean_text(workspace_server_id) or None
        return str(Path.cwd().resolve()), clean_text(workspace_server_id) or None

    def _collect_agent_text(self, updates: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for update in updates:
            if clean_text(update.get("sessionUpdate")) != "agent_message_chunk":
                continue
            content = update.get("content")
            if isinstance(content, dict):
                text = str(content.get("text") or "")
                if text:
                    parts.append(text)
        return "".join(parts).strip()

    def _connect_stdio_server(self, server: dict[str, Any]) -> _StreamingRpcClient:
        workspace_server_id = clean_text(server.get("workspace_server_id")) or None
        if workspace_server_id:
            return self._connect_remote_stdio_server(server, workspace_server_id)
        return self._connect_local_stdio_server(server)

    def _connect_local_stdio_server(self, server: dict[str, Any]) -> _StreamingRpcClient:
        command = [str(server["command"]), *[str(item) for item in (server.get("args") or [])]]
        spawn_cwd = clean_text(server.get("cwd")) or str(Path.cwd().resolve())
        env = os.environ.copy()
        env.update({str(key): str(value) for key, value in (server.get("env") or {}).items()})
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=spawn_cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None:
            process.kill()
            raise RuntimeError("本地 ACP 进程未能建立 stdio")

        def _close() -> None:
            if process.poll() is None:
                with contextlib.suppress(Exception):
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(Exception):
                        process.kill()

        return _StreamingRpcClient(
            stdin_handle=process.stdin,
            stdout_handle=process.stdout,
            stderr_handle=process.stderr,
            close_callback=_close,
        )

    def _connect_remote_stdio_server(
        self,
        server: dict[str, Any],
        workspace_server_id: str,
    ) -> _StreamingRpcClient:
        server_entry = _load_workspace_server_entry(workspace_server_id)
        if server_entry is None:
            raise ValueError("未找到 ACP 绑定的 SSH 工作区服务器")

        manager, session = _open_remote_session(server_entry)
        spawn_cwd = self._resolve_remote_spawn_cwd(server, server_entry, session)
        command_parts = [
            str(server["command"]),
            *[str(item) for item in (server.get("args") or [])],
        ]
        env_parts = [
            f"{key}={shlex.quote(str(value))}"
            for key, value in (server.get("env") or {}).items()
            if str(key).strip()
        ]
        remote_command = _shell_join(command_parts)
        final_command = remote_command
        if env_parts:
            final_command = f"{' '.join(env_parts)} {final_command}"
        final_command = f"cd {shlex.quote(spawn_cwd)} && {final_command}"

        stdin, stdout, stderr = session.client.exec_command(final_command, get_pty=False)

        def _close() -> None:
            with contextlib.suppress(Exception):
                stdin.close()
            with contextlib.suppress(Exception):
                stdout.close()
            with contextlib.suppress(Exception):
                stderr.close()
            with contextlib.suppress(Exception):
                manager.__exit__(None, None, None)

        return _StreamingRpcClient(
            stdin_handle=stdin,
            stdout_handle=stdout,
            stderr_handle=stderr,
            close_callback=_close,
        )

    def _resolve_remote_spawn_cwd(
        self,
        server: dict[str, Any],
        server_entry: dict[str, Any],
        session,
    ) -> str:
        raw_cwd = clean_text(server.get("cwd"))
        if raw_cwd:
            return resolve_remote_workspace_path(server_entry, raw_cwd, session)
        configured_root = clean_text(server_entry.get("workspace_root")) or "."
        return resolve_remote_workspace_path(server_entry, configured_root, session)

    def _connect_http_server(self, server: dict[str, Any]) -> _HttpRpcClient:
        headers = {
            str(key): str(value)
            for key, value in (server.get("headers") or {}).items()
            if str(key).strip()
        }
        return _HttpRpcClient(
            url=str(server["url"]),
            headers=headers,
            timeout_sec=int(server.get("timeout_sec") or _DEFAULT_TIMEOUT_SEC),
        )


@lru_cache(maxsize=1)
def get_acp_registry_service() -> AcpRegistryService:
    return AcpRegistryService()
