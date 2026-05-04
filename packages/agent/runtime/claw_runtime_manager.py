"""Persistent claw bridge-daemon manager for ResearchOS assistant sessions."""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import queue
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from packages.agent.mcp.claw_mcp_registry import (
    CLAW_CONTEXT_MODE_ENV,
    CLAW_CONTEXT_SESSION_ID_ENV,
    CLAW_CONTEXT_WORKSPACE_PATH_ENV,
    CLAW_CONTEXT_WORKSPACE_SERVER_ID_ENV,
    bridge_qualified_tool_names,
)
from packages.agent.runtime.agent_runtime_policy import (
    CLAW_AUTO_COMPACTION_THRESHOLD_ENV_VAR,
)
from packages.agent.runtime.agent_runtime_policy import (
    apply_claw_runtime_policy_env as _shared_apply_claw_runtime_policy_env,
)
from packages.agent.runtime.cli_agent_service import (
    _chunk_items,
    _claw_config_home_dir,
    _ensure_claw_binary,
    _ensure_claw_bridge_workspace,
    _ensure_claw_workspace_settings,
    _ensure_local_cli_env,
    _infer_claw_provider,
    _normalize_claw_base_url,
    _session_mode_for_claw,
    _slugify,
)
from packages.agent.workspace.workspace_remote import clean_text

logger = logging.getLogger(__name__)

_CREDENTIAL_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "XAI_API_KEY",
    "XAI_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
)


def _trim_text(value: Any, limit: int = 8000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _stream_write_text(handle: Any, text: str) -> None:
    try:
        handle.write(text)
    except TypeError:
        handle.write(text.encode("utf-8"))
    handle.flush()


def _file_signature(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_mtime_ns}:{stat.st_size}"


@dataclass(frozen=True)
class _ClawRuntimeSpec:
    identity: str
    fingerprint: str
    session_ref: str
    workspace_dir: Path
    command: tuple[str, ...]
    env: dict[str, str]


class _ClawRuntimeProcess:
    def __init__(self, spec: _ClawRuntimeSpec) -> None:
        self.spec = spec
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._pending: dict[str, queue.Queue[tuple[bool, Any]]] = {}
        self._stderr_chunks: list[str] = []

    def stream_prompt(self, prompt: str, timeout_sec: int) -> Iterator[dict[str, Any]]:
        request_id = uuid4().hex
        response_queue: queue.Queue[tuple[bool, Any]] = queue.Queue()
        payload = {"id": request_id, "prompt": str(prompt or "")}
        with self._lock:
            self._ensure_started_locked()
            assert self._process is not None
            self._pending[request_id] = response_queue
            try:
                _stream_write_text(
                    self._process.stdin,
                    json.dumps(payload, ensure_ascii=False) + "\n",
                )
            except Exception as exc:
                self._pending.pop(request_id, None)
                raise RuntimeError(f"发送 claw daemon 请求失败：{exc}") from exc

        deadline = time.monotonic() + max(10, int(timeout_sec or 0))
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("claw daemon 请求超时")
                try:
                    ok, value = response_queue.get(timeout=min(0.5, remaining))
                except queue.Empty:
                    with self._lock:
                        if self._process is None or self._process.poll() is not None:
                            raise RuntimeError(self._compose_exit_message_locked())
                    continue
                if not ok:
                    raise RuntimeError(str(value))
                payload = value if isinstance(value, dict) else {}
                yield payload
                if str(payload.get("event") or "").strip() == "done":
                    return
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._reject_all_queued_locked("claw daemon 已停止")
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except Exception:
            pass
        try:
            process.wait(timeout=2)
        except Exception:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    def _ensure_started_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._process = subprocess.Popen(
            list(self.spec.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.spec.workspace_dir),
            env=self.spec.env,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._stderr_chunks = []
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_reader.start()
        logger.info(
            "Started claw daemon pid=%s session_ref=%s workspace=%s",
            self._process.pid,
            self.spec.session_ref,
            self.spec.workspace_dir,
        )

    def _reader_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = str(raw_line or "").strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    self._append_stderr(f"[claw stdout] {line}")
                    continue
                request_id = (
                    clean_text(payload.get("request_id"))
                    or clean_text(payload.get("requestId"))
                    or clean_text(payload.get("id"))
                )
                if not request_id:
                    with self._lock:
                        if len(self._pending) == 1:
                            pending = next(iter(self._pending.values()))
                        else:
                            pending = None
                    if pending is not None:
                        pending.put((True, payload))
                    else:
                        self._append_stderr(f"[claw stdout] unroutable event: {line}")
                    continue
                with self._lock:
                    pending = self._pending.get(request_id)
                    if pending is None and len(self._pending) == 1:
                        # bridge-daemon text/tool events reuse `id` for part/tool ids
                        # instead of the outer request id, so fall back to the sole
                        # active request when exactly one request is in flight.
                        pending = next(iter(self._pending.values()))
                if pending is not None:
                    pending.put((True, payload))
        finally:
            self._handle_process_exit()

    def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw_line in process.stderr:
            line = str(raw_line or "").rstrip()
            if line:
                self._append_stderr(line)

    def _append_stderr(self, line: str) -> None:
        with self._lock:
            self._stderr_chunks.append(line)
            if len(self._stderr_chunks) > 120:
                self._stderr_chunks = self._stderr_chunks[-120:]

    def _handle_process_exit(self) -> None:
        with self._lock:
            if self._process is None:
                return
            message = self._compose_exit_message_locked()
            self._reject_all_queued_locked(message)

    def _compose_exit_message_locked(self) -> str:
        returncode = self._process.poll() if self._process is not None else None
        stderr_tail = "\n".join(self._stderr_chunks[-40:]).strip()
        if stderr_tail:
            return _trim_text(stderr_tail)
        if returncode is None:
            return "claw daemon 连接已关闭"
        return f"claw daemon 已退出，exit_code={returncode}"

    def _reject_all_queued_locked(self, message: str) -> None:
        for pending in self._pending.values():
            pending.put((False, message))
        self._pending.clear()


class ClawRuntimeManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._processes: dict[str, _ClawRuntimeProcess] = {}
        self._identity_map: dict[str, str] = {}

    def stream_prompt(
        self,
        config: dict[str, Any],
        *,
        prompt: str,
        workspace_path: str | None,
        workspace_server_id: str | None,
        timeout_sec: int,
        session_id: str | None,
    ) -> Iterator[dict[str, Any]]:
        spec = _build_runtime_spec(
            config,
            workspace_path=workspace_path,
            workspace_server_id=workspace_server_id,
            session_id=session_id,
            timeout_sec=timeout_sec,
        )
        process_to_stop: _ClawRuntimeProcess | None = None
        with self._lock:
            previous_key = self._identity_map.get(spec.identity)
            if previous_key and previous_key != spec.fingerprint:
                process_to_stop = self._processes.pop(previous_key, None)
            self._identity_map[spec.identity] = spec.fingerprint
            runtime_process = self._processes.get(spec.fingerprint)
            if runtime_process is None:
                runtime_process = _ClawRuntimeProcess(spec)
                self._processes[spec.fingerprint] = runtime_process
        if process_to_stop is not None:
            process_to_stop.stop()
        yield from runtime_process.stream_prompt(prompt, timeout_sec)

    def stop_all(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
            self._processes = {}
            self._identity_map = {}
        for process in processes:
            process.stop()


def _build_runtime_spec(
    config: dict[str, Any],
    *,
    workspace_path: str | None,
    workspace_server_id: str | None,
    session_id: str | None,
    timeout_sec: int,
) -> _ClawRuntimeSpec:
    bound_server_id = clean_text(workspace_server_id)
    target_workspace_path = clean_text(workspace_path) or None
    if bound_server_id and bound_server_id.lower() != "local":
        workspace_dir = _ensure_claw_bridge_workspace(target_workspace_path, bound_server_id)
    else:
        workspace_dir = Path(workspace_path or Path.cwd()).expanduser()
        if not workspace_dir.exists():
            raise ValueError(f"本地工作区不存在：{workspace_dir}")
        if not workspace_dir.is_dir():
            raise ValueError(f"本地工作区不是目录：{workspace_dir}")

    executable = clean_text(config.get("command_path")) or clean_text(config.get("command"))
    binary_path = Path(executable).expanduser() if executable else None
    if binary_path is None or not binary_path.exists():
        binary_path = _ensure_claw_binary(timeout_sec=max(timeout_sec, 1800))

    settings_path = _ensure_claw_workspace_settings(workspace_dir)
    bridge_session_ref = _slugify(f"researchos-{session_id or workspace_dir.name}")
    command: list[str] = [str(binary_path)]
    for chunk in _chunk_items(bridge_qualified_tool_names(), 24):
        command.extend(["--allowedTools", ",".join(chunk)])
    if clean_text(config.get("default_model")):
        command.extend(["--model", str(config["default_model"])])
    command.extend(["bridge-daemon", bridge_session_ref])

    env = _shared_apply_claw_runtime_policy_env(_ensure_local_cli_env(os.environ.copy()))
    for key in _CREDENTIAL_ENV_KEYS:
        env.pop(key, None)
    env["CLAW_CONFIG_HOME"] = str(_claw_config_home_dir())
    provider_kind = _infer_claw_provider(
        clean_text(config.get("default_model")) or None,
        clean_text(config.get("protocol")) or None,
        clean_text(config.get("provider")) or None,
        clean_text(config.get("base_url")) or None,
    )
    normalized_base_url = _normalize_claw_base_url(config.get("base_url"), provider_kind)
    if clean_text(config.get("api_key")):
        if provider_kind == "openai":
            env["OPENAI_API_KEY"] = str(config["api_key"])
        elif provider_kind == "xai":
            env["XAI_API_KEY"] = str(config["api_key"])
        else:
            env["ANTHROPIC_AUTH_TOKEN"] = str(config["api_key"])
            env.setdefault("ANTHROPIC_API_KEY", str(config["api_key"]))
    if normalized_base_url:
        if provider_kind == "openai":
            env["OPENAI_BASE_URL"] = normalized_base_url
        elif provider_kind == "xai":
            env["XAI_BASE_URL"] = normalized_base_url
        else:
            env["ANTHROPIC_BASE_URL"] = normalized_base_url
    if provider_kind == "anthropic" and clean_text(config.get("default_model")):
        env["ANTHROPIC_MODEL"] = str(config["default_model"])
    env[CLAW_CONTEXT_SESSION_ID_ENV] = clean_text(session_id) or bridge_session_ref
    env[CLAW_CONTEXT_MODE_ENV] = _session_mode_for_claw(session_id)
    env[CLAW_CONTEXT_WORKSPACE_PATH_ENV] = target_workspace_path or str(workspace_dir)
    if bound_server_id:
        env[CLAW_CONTEXT_WORKSPACE_SERVER_ID_ENV] = bound_server_id
    else:
        env.pop(CLAW_CONTEXT_WORKSPACE_SERVER_ID_ENV, None)

    identity = hashlib.sha1(
        json.dumps(
            {
                "workspace_dir": str(workspace_dir),
                "session_ref": bridge_session_ref,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    fingerprint = hashlib.sha1(
        json.dumps(
            {
                "identity": identity,
                "command": command,
                "provider_kind": provider_kind,
                "default_model": clean_text(config.get("default_model")) or None,
                "base_url": normalized_base_url,
                "api_key": clean_text(config.get("api_key")) or None,
                "mode": env.get(CLAW_CONTEXT_MODE_ENV),
                "workspace_path": env.get(CLAW_CONTEXT_WORKSPACE_PATH_ENV),
                "workspace_server_id": env.get(CLAW_CONTEXT_WORKSPACE_SERVER_ID_ENV),
                "auto_compaction_threshold": env.get(CLAW_AUTO_COMPACTION_THRESHOLD_ENV_VAR),
                "binary_signature": _file_signature(binary_path),
                "settings_signature": _file_signature(settings_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    return _ClawRuntimeSpec(
        identity=identity,
        fingerprint=fingerprint,
        session_ref=bridge_session_ref,
        workspace_dir=workspace_dir,
        command=tuple(command),
        env=env,
    )


_MANAGER: ClawRuntimeManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_claw_runtime_manager() -> ClawRuntimeManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = ClawRuntimeManager()
            atexit.register(_MANAGER.stop_all)
        return _MANAGER
