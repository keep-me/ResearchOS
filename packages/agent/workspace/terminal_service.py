from __future__ import annotations

import os
import queue
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Protocol

from packages.agent.workspace.workspace_remote import (
    clean_text,
    open_ssh_session,
    resolve_remote_workspace_path,
)
from packages.agent.workspace.workspace_executor import WorkspaceAccessError, resolve_workspace_dir

if os.name != "nt":
    import errno
    import fcntl
    import pty
    import termios


try:
    from winpty import PtyProcess
except ImportError:  # pragma: no cover - exercised only when dependency is missing
    PtyProcess = None


TERMINAL_EVENT_QUEUE_SIZE = 256
TERMINAL_HISTORY_LIMIT = 200_000
TERMINAL_MIN_COLS = 40
TERMINAL_MAX_COLS = 320
TERMINAL_MIN_ROWS = 8
TERMINAL_MAX_ROWS = 120


def _normalize_terminal_size(cols: int, rows: int) -> tuple[int, int]:
    safe_cols = max(TERMINAL_MIN_COLS, min(int(cols or 0) or 120, TERMINAL_MAX_COLS))
    safe_rows = max(TERMINAL_MIN_ROWS, min(int(rows or 0) or 32, TERMINAL_MAX_ROWS))
    return safe_cols, safe_rows


def _build_local_shell_argv() -> list[str]:
    if os.name == "nt":
        shell = (
            shutil.which("pwsh")
            or shutil.which("pwsh.exe")
            or next(
                (
                    str(candidate)
                    for candidate in (
                        Path(os.environ.get("ProgramW6432") or r"C:\Program Files") / "PowerShell" / "7" / "pwsh.exe",
                        Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "PowerShell" / "7" / "pwsh.exe",
                        Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)") / "PowerShell" / "7" / "pwsh.exe",
                    )
                    if candidate.exists()
                ),
                None,
            )
        )
        if not shell:
            raise WorkspaceAccessError("未找到 PowerShell 7（pwsh），无法启动真实终端")
        return [shell, "-NoLogo"]
    shell = os.environ.get("SHELL") or "/bin/bash"
    return [shell, "-l"]


def _build_terminal_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("COLORTERM", "truecolor")
    env.setdefault("PYTHONUTF8", "1")
    return env


class TerminalBackend(Protocol):
    kind: str
    shell_label: str
    workspace_path: str

    def read(self, size: int = 4096) -> str:
        ...

    def write(self, data: str) -> None:
        ...

    def resize(self, cols: int, rows: int) -> None:
        ...

    def is_alive(self) -> bool:
        ...

    def exit_code(self) -> int | None:
        ...

    def close(self) -> None:
        ...


class _WindowsTerminalBackend:
    kind = "local"

    def __init__(self, workspace_path: str, cols: int, rows: int) -> None:
        if PtyProcess is None:
            raise WorkspaceAccessError("未安装或未打包 pywinpty，无法启动 Windows PTY")
        root = resolve_workspace_dir(workspace_path)
        argv = _build_local_shell_argv()
        self.workspace_path = str(root)
        self.shell_label = Path(argv[0]).name
        try:
            self._proc = PtyProcess.spawn(
                argv,
                cwd=self.workspace_path,
                env=_build_terminal_env(),
                dimensions=(rows, cols),
            )
        except Exception as exc:  # pragma: no cover - depends on local shell runtime
            raise WorkspaceAccessError(f"启动本地终端失败: {exc}") from exc

    def read(self, size: int = 4096) -> str:
        try:
            return self._proc.read(size)
        except EOFError:
            return ""
        except OSError as exc:
            message = str(exc).lower()
            if "closed" in message or "eof" in message:
                return ""
            raise

    def write(self, data: str) -> None:
        self._proc.write(data)

    def resize(self, cols: int, rows: int) -> None:
        self._proc.setwinsize(rows, cols)

    def is_alive(self) -> bool:
        return bool(self._proc.isalive())

    def exit_code(self) -> int | None:
        code = getattr(self._proc, "exitstatus", None)
        if code is not None:
            return int(code)
        if self._proc.isalive():
            return None
        try:
            waited = self._proc.wait()
        except Exception:
            return None
        return int(waited) if waited is not None else None

    def close(self) -> None:
        try:
            self._proc.close(force=True)
        except Exception:
            pass


class _PosixTerminalBackend:
    kind = "local"

    def __init__(self, workspace_path: str, cols: int, rows: int) -> None:
        root = resolve_workspace_dir(workspace_path)
        argv = _build_local_shell_argv()
        self.workspace_path = str(root)
        self.shell_label = Path(argv[0]).name
        self._master_fd, slave_fd = pty.openpty()
        try:
            self._proc = subprocess.Popen(
                argv,
                cwd=self.workspace_path,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=_build_terminal_env(),
                start_new_session=True,
            )
        except Exception:
            os.close(self._master_fd)
            os.close(slave_fd)
            raise
        os.close(slave_fd)
        flags = fcntl.fcntl(self._master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self._master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.resize(cols, rows)

    def read(self, size: int = 4096) -> str:
        try:
            data = os.read(self._master_fd, size)
        except BlockingIOError:
            return ""
        except OSError as exc:
            if exc.errno in {errno.EIO, errno.EBADF}:
                return ""
            raise
        return data.decode("utf-8", errors="replace")

    def write(self, data: str) -> None:
        os.write(self._master_fd, data.encode("utf-8", errors="replace"))

    def resize(self, cols: int, rows: int) -> None:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def exit_code(self) -> int | None:
        return self._proc.poll()

    def close(self) -> None:
        try:
            if self._proc.poll() is None:
                self._proc.terminate()
                self._proc.wait(timeout=2)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        try:
            os.close(self._master_fd)
        except OSError:
            pass


class _SshTerminalBackend:
    kind = "ssh"

    def __init__(self, server_entry: dict, requested_path: str, cols: int, rows: int) -> None:
        self._context = open_ssh_session(server_entry)
        self._session = self._context.__enter__()
        self.workspace_path = resolve_remote_workspace_path(server_entry, requested_path, self._session)
        username = clean_text(server_entry.get("username")) or "user"
        host = clean_text(server_entry.get("host")) or "remote"
        self.shell_label = f"ssh://{username}@{host}"
        try:
            self._channel = self._session.client.invoke_shell(
                term="xterm-256color",
                width=cols,
                height=rows,
            )
            self._channel.settimeout(0.2)
            self.write(f"cd {shlex.quote(self.workspace_path)}\n")
        except Exception as exc:
            self.close()
            raise WorkspaceAccessError(f"启动远程终端失败: {exc}") from exc
        self._exit_code: int | None = None

    def read(self, size: int = 4096) -> str:
        if self._channel.closed:
            return ""
        try:
            data = self._channel.recv(size)
        except socket.timeout:
            return ""
        except Exception as exc:
            raise WorkspaceAccessError(f"读取远程终端失败: {exc}") from exc
        if not data:
            return ""
        return data.decode("utf-8", errors="replace")

    def write(self, data: str) -> None:
        try:
            self._channel.send(data)
        except Exception as exc:
            raise WorkspaceAccessError(f"写入远程终端失败: {exc}") from exc

    def resize(self, cols: int, rows: int) -> None:
        try:
            self._channel.resize_pty(width=cols, height=rows)
        except Exception as exc:
            raise WorkspaceAccessError(f"调整远程终端尺寸失败: {exc}") from exc

    def is_alive(self) -> bool:
        transport = self._session.client.get_transport()
        return not self._channel.closed and bool(transport and transport.is_active())

    def exit_code(self) -> int | None:
        if self._exit_code is not None:
            return self._exit_code
        if self._channel.exit_status_ready():
            try:
                self._exit_code = int(self._channel.recv_exit_status())
            except Exception:
                self._exit_code = None
        return self._exit_code

    def close(self) -> None:
        try:
            self._channel.close()
        except Exception:
            pass
        try:
            self._context.__exit__(None, None, None)
        except Exception:
            pass


def _build_terminal_backend(
    workspace_path: str,
    *,
    server_id: str,
    server_entry: dict | None,
    cols: int,
    rows: int,
) -> TerminalBackend:
    if clean_text(server_id) and clean_text(server_id) != "local":
        if server_entry is None:
            raise WorkspaceAccessError("缺少远程终端服务器配置")
        return _SshTerminalBackend(server_entry, workspace_path, cols, rows)
    if os.name == "nt":
        return _WindowsTerminalBackend(workspace_path, cols, rows)
    return _PosixTerminalBackend(workspace_path, cols, rows)


class TerminalSession:
    def __init__(
        self,
        session_id: str,
        backend: TerminalBackend,
        *,
        server_id: str,
        cols: int,
        rows: int,
    ) -> None:
        self.session_id = session_id
        self.server_id = clean_text(server_id) or "local"
        self.kind = backend.kind
        self.workspace_path = backend.workspace_path
        self.shell = backend.shell_label
        self.cols = cols
        self.rows = rows
        self.created_at = time.time()
        self.updated_at = self.created_at
        self._backend = backend
        self._lock = threading.RLock()
        self._subscribers: dict[str, queue.Queue[dict]] = {}
        self._history = ""
        self._closed = False
        self._close_requested = threading.Event()
        self._exit_code: int | None = None
        self._error: str | None = None
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"terminal-session-{session_id}",
            daemon=True,
        )
        self._reader_thread.start()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "session_id": self.session_id,
                "server_id": self.server_id,
                "kind": self.kind,
                "workspace_path": self.workspace_path,
                "shell": self.shell,
                "cols": self.cols,
                "rows": self.rows,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "history": self._history,
                "closed": self._closed,
                "exit_code": self._exit_code,
                "error": self._error,
            }

    def subscribe(self) -> tuple[str, dict]:
        subscriber_id = secrets.token_urlsafe(12)
        subscriber_queue: queue.Queue[dict] = queue.Queue(maxsize=TERMINAL_EVENT_QUEUE_SIZE)
        with self._lock:
            self._subscribers[subscriber_id] = subscriber_queue
            snapshot = self.snapshot()
        return subscriber_id, snapshot

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def wait_for_event(self, subscriber_id: str, timeout: float = 0.25) -> dict | None:
        with self._lock:
            subscriber_queue = self._subscribers.get(subscriber_id)
        if subscriber_queue is None:
            return None
        try:
            return subscriber_queue.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def write(self, data: str) -> None:
        if self.is_closed():
            raise WorkspaceAccessError("终端会话已关闭")
        self._backend.write(data)
        with self._lock:
            self.updated_at = time.time()

    def resize(self, cols: int, rows: int) -> None:
        safe_cols, safe_rows = _normalize_terminal_size(cols, rows)
        self._backend.resize(safe_cols, safe_rows)
        with self._lock:
            self.cols = safe_cols
            self.rows = safe_rows
            self.updated_at = time.time()

    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        if self.is_closed():
            return
        self._close_requested.set()
        self._backend.close()
        self._reader_thread.join(timeout=1.5)
        with self._lock:
            self._closed = True
            self.updated_at = time.time()

    def _append_history(self, chunk: str) -> None:
        if not chunk:
            return
        with self._lock:
            self._history += chunk
            if len(self._history) > TERMINAL_HISTORY_LIMIT:
                self._history = self._history[-TERMINAL_HISTORY_LIMIT:]
            self.updated_at = time.time()

    def _publish(self, event: dict) -> None:
        with self._lock:
            queues = list(self._subscribers.values())
        for subscriber_queue in queues:
            try:
                subscriber_queue.put_nowait(event)
            except queue.Full:
                try:
                    subscriber_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber_queue.put_nowait(event)
                except queue.Full:
                    pass

    def _mark_closed(self, *, exit_code: int | None, error: str | None) -> None:
        with self._lock:
            if self._closed and self._exit_code == exit_code and self._error == error:
                return
            self._closed = True
            self._exit_code = exit_code
            self._error = error
            self.updated_at = time.time()
        if error:
            self._publish({"type": "error", "message": error})
        self._publish({"type": "exit", "exit_code": exit_code})

    def _reader_loop(self) -> None:
        error_message: str | None = None
        exit_code: int | None = None
        try:
            while not self._close_requested.is_set():
                chunk = self._backend.read(4096)
                if chunk:
                    self._append_history(chunk)
                    self._publish({"type": "output", "data": chunk})
                    continue
                if self._close_requested.is_set():
                    break
                if not self._backend.is_alive():
                    exit_code = self._backend.exit_code()
                    break
                time.sleep(0.02)
        except Exception as exc:
            error_message = str(exc or "").strip() or "终端会话异常退出"
        finally:
            try:
                self._backend.close()
            except Exception:
                pass
            exit_code = self._backend.exit_code() if exit_code is None else exit_code
            self._mark_closed(exit_code=exit_code, error=error_message)


class TerminalService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, TerminalSession] = {}

    def create_session(
        self,
        workspace_path: str,
        *,
        server_id: str = "local",
        server_entry: dict | None = None,
        cols: int = 120,
        rows: int = 32,
    ) -> dict:
        safe_cols, safe_rows = _normalize_terminal_size(cols, rows)
        backend = _build_terminal_backend(
            workspace_path,
            server_id=server_id,
            server_entry=server_entry,
            cols=safe_cols,
            rows=safe_rows,
        )
        session_id = secrets.token_urlsafe(18)
        session = TerminalSession(
            session_id,
            backend,
            server_id=server_id,
            cols=safe_cols,
            rows=safe_rows,
        )
        with self._lock:
            self._sessions[session_id] = session
        return session.snapshot()

    def get_session(self, session_id: str) -> TerminalSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise WorkspaceAccessError(f"未找到终端会话: {session_id}")
        return session

    def close_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.close()

    def dispose_all(self) -> None:
        with self._lock:
            items = list(self._sessions.items())
            self._sessions.clear()
        for _session_id, session in items:
            session.close()


_terminal_service: TerminalService | None = None


def get_terminal_service() -> TerminalService:
    global _terminal_service
    if _terminal_service is None:
        _terminal_service = TerminalService()
    return _terminal_service

