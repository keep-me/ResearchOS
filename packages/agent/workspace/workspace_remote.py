"""Remote SSH workspace service helpers."""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import shlex
import socket
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

import paramiko

from packages.agent.workspace.workspace_executor import DEFAULT_IGNORES, TERMINAL_CWD_MARKER, WorkspaceAccessError

DEFAULT_SSH_PORT = 22
SSH_CONNECT_TIMEOUT_SEC = 20
SSH_BANNER_TIMEOUT_SEC = 45
SSH_AUTH_TIMEOUT_SEC = 20
SSH_BANNER_RETRY_COUNT = 2
SSH_BANNER_RETRY_DELAY_SEC = 0.35
SSH_BANNER_PROBE_CONNECT_TIMEOUT_SEC = 4
SSH_BANNER_PROBE_READ_TIMEOUT_SEC = 6
_SCREEN_SESSION_PATTERN = re.compile(r"^\s*(?P<pid>\d+)\.(?P<name>[^\s]+)\s+\((?P<state>[^)]+)\)")
_REMOTE_RUN_SYNC_PATTERNS = (
    ".git",
    ".auto-researcher",
    "data",
    "outputs",
    "checkpoints",
    "artifacts",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.bin",
    "*.npz",
    "*.npy",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.7z",
)


def clean_text(value: object) -> str:
    return str(value or "").strip()


def mask_secret(value: str | None) -> str | None:
    secret = clean_text(value)
    if not secret:
        return None
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-2:]}"


def is_ssh_banner_error(exc: Exception) -> bool:
    message = clean_text(exc).lower()
    return "ssh protocol banner" in message or "protocol banner" in message


def probe_ssh_banner(host: str, port: int) -> dict[str, str | None]:
    target_host = clean_text(host)
    if not target_host:
        return {"state": "invalid", "banner": None, "error": "missing host"}
    try:
        with socket.create_connection(
            (target_host, int(port)),
            timeout=SSH_BANNER_PROBE_CONNECT_TIMEOUT_SEC,
        ) as sock:
            sock.settimeout(SSH_BANNER_PROBE_READ_TIMEOUT_SEC)
            chunks: list[bytes] = []
            for _ in range(8):
                chunk = sock.recv(256)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk or b"\r" in chunk:
                    break
            banner = b"".join(chunks).decode("utf-8", errors="replace").strip()
            if not banner:
                return {"state": "empty", "banner": None, "error": None}
            if banner.upper().startswith("SSH-"):
                return {"state": "ssh", "banner": banner, "error": None}
            return {"state": "non_ssh", "banner": banner, "error": None}
    except (socket.timeout, TimeoutError):
        return {"state": "timeout", "banner": None, "error": "timeout"}
    except Exception as exc:  # pragma: no cover - network/environment dependent
        return {"state": "error", "banner": None, "error": clean_text(exc) or exc.__class__.__name__}


def format_ssh_exception(exc: Exception, *, host: str, port: int) -> str:
    raw_message = clean_text(exc) or exc.__class__.__name__
    lower_message = raw_message.lower()
    target = f"{host}:{port}"
    if is_ssh_banner_error(exc):
        banner_probe = probe_ssh_banner(host, port)
        probe_state = clean_text(banner_probe.get("state"))
        probe_banner = clean_text(banner_probe.get("banner"))
        if probe_state == "ssh":
            return (
                f"SSH 握手失败：{target} 当前确实返回了 SSH 协议标识"
                f"（{probe_banner or 'SSH banner'}），说明端口本身就是 SSH。"
                "更可能是服务端响应过慢、网关/防火墙中断，或 Paramiko 在 banner 阶段超时。"
                "请先在终端里手动执行一次 ssh 连接验证，再重试。"
            )
        if probe_state == "non_ssh":
            snippet = probe_banner[:160] if probe_banner else "unknown"
            return (
                f"SSH 握手失败：{target} 返回的不是 SSH 协议标识，而是：{snippet}。"
                "这通常表示端口填错，或目标端口前面还有网关/HTTP 服务。"
            )
        return (
            f"SSH 握手失败：无法从 {target} 读取 SSH 协议标识。"
            "这可能表示端口不对、服务端握手过慢，或被网关/防火墙中断。"
            "请确认你连接的是 SSH 端口（通常是 22），并先在终端里手动执行一次 ssh 连接验证。"
        )
    if "connection reset" in lower_message or "forcibly closed" in lower_message:
        return (
            f"SSH 连接被 {target} 主动断开。"
            "常见原因是端口不对、服务端拒绝当前来源 IP，或中间代理在握手前就断开了连接。"
        )
    if "connection refused" in lower_message:
        return f"SSH 连接被拒绝：{target} 当前没有可用的 SSH 服务，或端口未开放。"
    return f"SSH 连接异常: {raw_message}"


def trim_output(text: str, max_chars: int = 12000) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...[truncated]"


def build_diff_preview(before: str, after: str, *, max_lines: int = 220, max_chars: int = 6000) -> str:
    diff_lines = list(
        unified_diff(
            (before or "").splitlines(),
            (after or "").splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if not diff_lines:
        return ""
    return trim_output("\n".join(diff_lines[:max_lines]), max_chars=max_chars)


def git_unavailable(message: str, *, available: bool = True) -> dict:
    return {
        "available": available,
        "is_repo": False,
        "branch": None,
        "remotes": [],
        "entries": [],
        "changed_count": 0,
        "untracked_count": 0,
        "message": message,
    }


def looks_like_windows_path(value: str) -> bool:
    text = (value or "").strip()
    return bool(re.match(r"^[A-Za-z]:[\\/]", text)) or "\\" in text


def normalize_relative_remote_path(relative_path: str) -> str:
    raw = (relative_path or "").replace("\\", "/").strip()
    if not raw:
        raise WorkspaceAccessError("relative_path 不能为空")
    normalized = posixpath.normpath(raw)
    if normalized in {"", ".", "/"}:
        raise WorkspaceAccessError("relative_path 不能为空")
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        raise WorkspaceAccessError("relative_path 必须位于工作区内")
    if normalized.startswith("~") or re.match(r"^[A-Za-z]:", normalized):
        raise WorkspaceAccessError("relative_path 必须是相对路径")
    return normalized


def sanitize_screen_session_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", clean_text(value)).strip("-.")
    return sanitized[:64] or "aris-run"


def _parse_screen_sessions(output: str) -> list[dict]:
    sessions: list[dict] = []
    for raw_line in str(output or "").splitlines():
        match = _SCREEN_SESSION_PATTERN.match(raw_line)
        if not match:
            continue
        sessions.append(
            {
                "pid": int(match.group("pid")),
                "name": match.group("name"),
                "state": match.group("state"),
                "label": f"{match.group('pid')}.{match.group('name')}",
            }
        )
    return sessions


def _ensure_remote_directory(session: SSHWorkspaceSession, target_dir: str) -> None:
    if remote_stat(session.sftp, target_dir) is None:
        remote_make_dirs(session.sftp, target_dir)


def _remote_copy_overlay_command(source_path: str, target_path: str) -> str:
    rsync_excludes = " ".join(
        f"--exclude {shlex.quote(pattern)}"
        for pattern in _REMOTE_RUN_SYNC_PATTERNS
    )
    tar_excludes = " ".join(
        f"--exclude={shlex.quote(pattern)}"
        for pattern in _REMOTE_RUN_SYNC_PATTERNS
    )
    source_root = source_path.rstrip("/") + "/"
    target_root = target_path.rstrip("/") + "/"
    return (
        f"mkdir -p {shlex.quote(target_path)} && "
        "if command -v rsync >/dev/null 2>&1; then "
        f"rsync -a {rsync_excludes} {shlex.quote(source_root)} {shlex.quote(target_root)}; "
        "else "
        f"(cd {shlex.quote(source_path)} && tar cf - {tar_excludes} .) | "
        f"(cd {shlex.quote(target_path)} && tar xf -); "
        "fi"
    )


def _parse_nvidia_smi_inventory(output: str) -> list[dict]:
    items: list[dict] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            index = int(parts[0])
            memory_used_mb = int(parts[2] if len(parts) >= 5 else parts[1])
            memory_total_mb = int(parts[3] if len(parts) >= 5 else parts[2])
            utilization_gpu_pct = int(parts[4]) if len(parts) >= 5 else None
        except ValueError:
            continue
        name = parts[1] if len(parts) >= 5 else None
        items.append(
            {
                "index": index,
                "name": name,
                "memory_used_mb": memory_used_mb,
                "memory_total_mb": memory_total_mb,
                "utilization_gpu_pct": utilization_gpu_pct,
            }
        )
    return items


@dataclass
class SSHWorkspaceSession:
    client: paramiko.SSHClient
    sftp: paramiko.SFTPClient
    home_dir: str


def load_private_key(private_key_value: str, passphrase: str | None) -> paramiko.PKey:
    key_source = clean_text(private_key_value)
    if not key_source:
        raise WorkspaceAccessError("SSH 私钥为空")
    try:
        candidate_path = Path(key_source).expanduser()
        if candidate_path.exists() and candidate_path.is_file():
            key_source = candidate_path.read_text(encoding="utf-8")
    except OSError:
        pass
    password = clean_text(passphrase) or None
    buffer = io.StringIO(key_source)
    key_types = [paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey]
    errors: list[str] = []
    for key_type in key_types:
        buffer.seek(0)
        try:
            return key_type.from_private_key(buffer, password=password)
        except paramiko.PasswordRequiredException as exc:
            raise WorkspaceAccessError("SSH 私钥需要口令，请填写 passphrase") from exc
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))
    raise WorkspaceAccessError(f"无法解析 SSH 私钥: {errors[-1] if errors else 'unknown error'}")


@contextmanager
def open_ssh_session(server_entry: dict):
    if not bool(server_entry.get("enabled", True)):
        raise WorkspaceAccessError("当前 SSH 服务器已禁用")
    host = clean_text(server_entry.get("host"))
    port = int(server_entry.get("port") or DEFAULT_SSH_PORT)
    username = clean_text(server_entry.get("username"))
    if not host:
        raise WorkspaceAccessError("SSH 服务器缺少 host 配置")
    if not username:
        raise WorkspaceAccessError("SSH 服务器缺少 username 配置")
    connect_kwargs: dict = {
        "hostname": host,
        "port": port,
        "username": username,
        "look_for_keys": False,
        "allow_agent": False,
        "timeout": SSH_CONNECT_TIMEOUT_SEC,
        "banner_timeout": SSH_BANNER_TIMEOUT_SEC,
        "auth_timeout": SSH_AUTH_TIMEOUT_SEC,
    }
    password = clean_text(server_entry.get("password"))
    private_key_value = clean_text(server_entry.get("private_key"))
    passphrase = clean_text(server_entry.get("passphrase")) or None
    if private_key_value:
        connect_kwargs["pkey"] = load_private_key(private_key_value, passphrase)
    elif password:
        connect_kwargs["password"] = password
    else:
        raise WorkspaceAccessError("SSH 服务器缺少可用的认证信息")
    for attempt in range(SSH_BANNER_RETRY_COUNT):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        sftp: paramiko.SFTPClient | None = None
        try:
            client.connect(**connect_kwargs)
            sftp = client.open_sftp()
            try:
                home_dir = sftp.normalize(".")
            except Exception:
                home_dir = "/"
            yield SSHWorkspaceSession(client=client, sftp=sftp, home_dir=home_dir)
            return
        except paramiko.AuthenticationException as exc:
            raise WorkspaceAccessError(f"SSH 认证失败: {exc}") from exc
        except paramiko.ssh_exception.NoValidConnectionsError as exc:
            raise WorkspaceAccessError(f"SSH 连接失败: {exc}") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise WorkspaceAccessError(f"SSH 连接超时: {exc}") from exc
        except socket.gaierror as exc:
            raise WorkspaceAccessError(f"无法解析 SSH 主机: {exc}") from exc
        except paramiko.SSHException as exc:
            if is_ssh_banner_error(exc) and attempt + 1 < SSH_BANNER_RETRY_COUNT:
                time.sleep(SSH_BANNER_RETRY_DELAY_SEC)
                continue
            raise WorkspaceAccessError(format_ssh_exception(exc, host=host, port=port)) from exc
        except Exception as exc:
            if is_ssh_banner_error(exc) and attempt + 1 < SSH_BANNER_RETRY_COUNT:
                time.sleep(SSH_BANNER_RETRY_DELAY_SEC)
                continue
            raise
        finally:
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass
            client.close()


def resolve_remote_workspace_path(server_entry: dict, requested_path: str, session: SSHWorkspaceSession) -> str:
    requested = clean_text(requested_path)
    configured = clean_text(server_entry.get("workspace_root"))
    if requested and not looks_like_windows_path(requested):
        raw_root = requested if requested.startswith("/") or requested.startswith("~") else posixpath.join(configured, requested.replace("\\", "/")) if configured else requested.replace("\\", "/")
    elif configured:
        raw_root = configured
    elif requested:
        raise WorkspaceAccessError("SSH 服务器需要配置远程工作区目录")
    else:
        raise WorkspaceAccessError("远程工作区目录为空")
    value = raw_root.replace("\\", "/").strip() or "."
    if value == "~":
        value = session.home_dir
    elif value.startswith("~/"):
        value = posixpath.join(session.home_dir, value[2:])
    elif not value.startswith("/"):
        value = posixpath.join(session.home_dir, value)
    return posixpath.normpath(value)


def remote_stat(sftp: paramiko.SFTPClient, path: str):
    try:
        return sftp.stat(path)
    except (OSError, IOError):
        return None


def remote_is_dir(attr) -> bool:
    return bool(attr and stat.S_ISDIR(attr.st_mode))


def remote_make_dirs(sftp: paramiko.SFTPClient, target_dir: str) -> None:
    normalized = posixpath.normpath(target_dir)
    if normalized in {"", "/", "."}:
        return
    parts = [part for part in normalized.split("/") if part]
    current = "/" if normalized.startswith("/") else ""
    for part in parts:
        current = f"/{part}" if current == "/" else f"{current}/{part}" if current else part
        if remote_stat(sftp, current) is None:
            sftp.mkdir(current)


def run_remote_exec(
    session: SSHWorkspaceSession,
    command: str,
    *,
    cwd: str | None = None,
    timeout_sec: int = 120,
    capture_cwd: bool = False,
) -> dict:
    normalized_command = (command or "").strip()
    if not normalized_command:
        raise WorkspaceAccessError("命令为空")
    wrapped_command = normalized_command
    if capture_cwd:
        wrapped_command = (
            f"{normalized_command}; "
            "__researchos_exit=$?; "
            f"printf '%s%s\\n' '{TERMINAL_CWD_MARKER}' \"$PWD\"; "
            "exit $__researchos_exit"
        )
    final_command = f"cd {shlex.quote(cwd)} && {wrapped_command}" if cwd else wrapped_command
    transport = session.client.get_transport()
    peer_name = "remote"
    username = "user"
    if transport is not None:
        username = clean_text(getattr(transport, "get_username", lambda: None)()) or "user"
        try:
            peer_name = transport.getpeername()[0]
        except Exception:
            peer_name = "remote"
    try:
        stdin, stdout, stderr = session.client.exec_command(final_command, timeout=max(1, timeout_sec))
        try:
            stdin.close()
        except Exception:
            pass
        channel = stdout.channel
        channel.settimeout(max(1, timeout_sec))
        out_text = stdout.read().decode("utf-8", errors="replace")
        err_text = stderr.read().decode("utf-8", errors="replace")
        exit_code = channel.recv_exit_status()
    except socket.timeout as exc:
        raise WorkspaceAccessError(f"远程命令执行超时（{timeout_sec}s）") from exc
    except Exception as exc:
        raise WorkspaceAccessError(f"远程命令执行失败: {exc}") from exc
    cwd_value = None
    if capture_cwd:
        out_text, cwd_value = _extract_terminal_cwd(out_text)
    return {
        "exit_code": exit_code,
        "stdout": out_text,
        "stderr": err_text,
        "shell_command": ["ssh", f"{username}@{peer_name}", final_command],
        "cwd": cwd_value or cwd,
    }


def _extract_terminal_cwd(text: str) -> tuple[str, str | None]:
    raw = text or ""
    if TERMINAL_CWD_MARKER not in raw:
        return raw, None

    lines = raw.splitlines()
    marker_index = -1
    cwd_value: str | None = None
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx].strip()
        if line.startswith(TERMINAL_CWD_MARKER):
            marker_index = idx
            cwd_value = line[len(TERMINAL_CWD_MARKER):].strip() or None
            break
    if marker_index < 0:
        return raw, None
    cleaned = lines[:marker_index] + lines[marker_index + 1:]
    return "\n".join(cleaned), cwd_value


def remote_git_overview(session: SSHWorkspaceSession, workspace_path: str) -> dict:
    if remote_stat(session.sftp, workspace_path) is None:
        return git_unavailable("远程工作区不存在", available=True)
    probe = run_remote_exec(session, "git rev-parse --is-inside-work-tree", cwd=workspace_path, timeout_sec=60)
    if probe["exit_code"] != 0:
        return git_unavailable("当前目录尚未初始化 Git 仓库", available=True)
    branch = run_remote_exec(session, "git branch --show-current", cwd=workspace_path, timeout_sec=60)["stdout"].strip() or None
    remotes = [line.strip() for line in run_remote_exec(session, "git remote", cwd=workspace_path, timeout_sec=60)["stdout"].splitlines() if line.strip()]
    status_output = run_remote_exec(session, "git status --porcelain=v1", cwd=workspace_path, timeout_sec=60)["stdout"]
    entries: list[dict] = []
    changed_count = 0
    untracked_count = 0
    for line in status_output.splitlines():
        if len(line) < 3:
            continue
        code = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        entries.append({"path": path, "code": code, "index_status": code[0], "worktree_status": code[1]})
        if code == "??":
            untracked_count += 1
        elif code.strip():
            changed_count += 1
    return {"available": True, "is_repo": True, "branch": branch, "remotes": remotes, "entries": entries, "changed_count": changed_count, "untracked_count": untracked_count, "message": None}


def build_remote_overview(server_entry: dict, requested_path: str, *, depth: int, max_entries: int | None) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, requested_path, session)
        root_attr = remote_stat(session.sftp, workspace_path)
        if root_attr is None:
            return {"workspace_path": workspace_path, "tree": workspace_path, "files": [], "total_entries": 0, "truncated": False, "exists": False, "git": git_unavailable("远程工作区不存在", available=True)}
        if not remote_is_dir(root_attr):
            raise WorkspaceAccessError(f"远程工作区不是目录: {workspace_path}")
        tree_lines = [workspace_path]
        files: list[str] = []
        entry_count = 0
        truncated = False
        entry_limit = max_entries if max_entries and max_entries > 0 else None

        def walk(current_path: str, current_depth: int) -> None:
            nonlocal entry_count, truncated
            if current_depth > depth or (entry_limit is not None and entry_count >= entry_limit):
                truncated = truncated or bool(entry_limit is not None and entry_count >= entry_limit)
                return
            try:
                children = sorted(session.sftp.listdir_attr(current_path), key=lambda item: (not stat.S_ISDIR(item.st_mode), item.filename.lower()))
            except OSError:
                return
            for child in children:
                if entry_limit is not None and entry_count >= entry_limit:
                    truncated = True
                    return
                if child.filename in DEFAULT_IGNORES:
                    continue
                if child.filename.startswith(".") and current_depth > 0:
                    continue
                child_path = posixpath.join(current_path, child.filename)
                prefix = "  " * current_depth + "- "
                if stat.S_ISDIR(child.st_mode):
                    tree_lines.append(f"{prefix}{child.filename}/")
                    entry_count += 1
                    walk(child_path, current_depth + 1)
                else:
                    tree_lines.append(f"{prefix}{child.filename}")
                    files.append(posixpath.relpath(child_path, workspace_path))
                    entry_count += 1

        walk(workspace_path, 0)
        return {
            "workspace_path": workspace_path,
            "tree": "\n".join(tree_lines),
            "files": files if entry_limit is None else files[:entry_limit],
            "total_entries": entry_count,
            "truncated": truncated,
            "exists": True,
            "git": remote_git_overview(session, workspace_path),
        }


def remote_read_file(server_entry: dict, requested_path: str, relative_path: str, *, max_chars: int) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, requested_path, session)
        normalized_relative = normalize_relative_remote_path(relative_path)
        target_path = posixpath.join(workspace_path, normalized_relative)
        target_attr = remote_stat(session.sftp, target_path)
        if target_attr is None:
            raise WorkspaceAccessError(f"文件不存在: {relative_path}")
        if remote_is_dir(target_attr):
            raise WorkspaceAccessError(f"不是文件: {relative_path}")
        with session.sftp.file(target_path, "rb") as handle:
            content = handle.read().decode("utf-8", errors="replace")
        return {
            "workspace_path": workspace_path,
            "relative_path": normalized_relative,
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
            "size_bytes": int(target_attr.st_size),
        }


def remote_write_file(
    server_entry: dict,
    *,
    path: str,
    relative_path: str,
    content: str,
    create_dirs: bool = True,
    overwrite: bool = True,
) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        workspace_attr = remote_stat(session.sftp, workspace_path)
        if workspace_attr is None:
            raise WorkspaceAccessError(f"远程工作区不存在: {workspace_path}")
        if not remote_is_dir(workspace_attr):
            raise WorkspaceAccessError(f"远程工作区不是目录: {workspace_path}")
        normalized_relative = normalize_relative_remote_path(relative_path)
        target_path = posixpath.join(workspace_path, normalized_relative)
        target_attr = remote_stat(session.sftp, target_path)
        existed = target_attr is not None
        if existed and remote_is_dir(target_attr):
            raise WorkspaceAccessError(f"目标是目录，无法写入文件: {normalized_relative}")
        if existed and not overwrite:
            raise WorkspaceAccessError(f"文件已存在，未允许覆盖: {normalized_relative}")
        parent_dir = posixpath.dirname(target_path)
        if remote_stat(session.sftp, parent_dir) is None:
            if not create_dirs:
                raise WorkspaceAccessError(f"父目录不存在: {parent_dir}")
            remote_make_dirs(session.sftp, parent_dir)
        previous_text = ""
        previous_size = 0
        changed = True
        if existed:
            with session.sftp.file(target_path, "rb") as handle:
                previous_bytes = handle.read()
            previous_text = previous_bytes.decode("utf-8", errors="replace")
            previous_size = len(previous_bytes)
            changed = previous_text != content
        encoded = content.encode("utf-8")
        with session.sftp.file(target_path, "wb") as handle:
            handle.write(encoded)
        latest_attr = remote_stat(session.sftp, target_path)
        size_bytes = int(latest_attr.st_size) if latest_attr is not None else len(encoded)
        return {
            "workspace_path": workspace_path,
            "relative_path": normalized_relative,
            "created": not existed,
            "overwritten": existed,
            "changed": changed,
            "size_bytes": size_bytes,
            "previous_size_bytes": previous_size,
            "line_count": content.count("\n") + (0 if not content else 1),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "preview": trim_output(content, max_chars=2400),
            "diff_preview": build_diff_preview(previous_text, content) if existed and changed else "",
        }


def remote_restore_file(server_entry: dict, *, path: str, content: str | None) -> dict:
    target_path = clean_text(path).replace("\\", "/")
    if not target_path:
        raise WorkspaceAccessError("远程文件路径为空")
    with open_ssh_session(server_entry) as session:
        target_attr = remote_stat(session.sftp, target_path)
        exists_before = target_attr is not None and not remote_is_dir(target_attr)
        parent_dir = posixpath.dirname(target_path)
        if content is None:
            if exists_before:
                session.sftp.remove(target_path)
            return {
                "path": target_path,
                "deleted": exists_before,
                "exists": False,
            }
        if parent_dir and parent_dir not in {"", "."} and remote_stat(session.sftp, parent_dir) is None:
            remote_make_dirs(session.sftp, parent_dir)
        encoded = content.encode("utf-8")
        with session.sftp.file(target_path, "wb") as handle:
            handle.write(encoded)
        return {
            "path": target_path,
            "deleted": False,
            "exists": True,
            "size_bytes": len(encoded),
        }


def remote_upload_file(
    server_entry: dict,
    *,
    path: str,
    relative_path: str | None,
    filename: str,
    mime_type: str,
    content: bytes,
) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        workspace_attr = remote_stat(session.sftp, workspace_path)
        if workspace_attr is None:
            raise WorkspaceAccessError(f"远程工作区不存在: {workspace_path}")
        if not remote_is_dir(workspace_attr):
            raise WorkspaceAccessError(f"远程工作区不是目录: {workspace_path}")
        target_rel = normalize_relative_remote_path((relative_path or filename or "upload.bin").strip())
        target_path = posixpath.join(workspace_path, target_rel)
        parent_dir = posixpath.dirname(target_path)
        if remote_stat(session.sftp, parent_dir) is None:
            remote_make_dirs(session.sftp, parent_dir)
        existed = remote_stat(session.sftp, target_path) is not None
        with session.sftp.file(target_path, "wb") as handle:
            handle.write(content)
        latest_attr = remote_stat(session.sftp, target_path)
        return {
            "workspace_path": workspace_path,
            "relative_path": target_rel,
            "filename": filename or posixpath.basename(target_path),
            "mime_type": mime_type or "application/octet-stream",
            "size_bytes": int(latest_attr.st_size) if latest_attr is not None else len(content),
            "created": not existed,
            "overwritten": existed,
        }


def remote_terminal_result(server_entry: dict, *, path: str, command: str, timeout_sec: int) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        result = run_remote_exec(
            session,
            command,
            cwd=workspace_path,
            timeout_sec=max(1, timeout_sec),
            capture_cwd=True,
        )
        return {
            "workspace_path": workspace_path,
            "cwd": result.get("cwd") or workspace_path,
            "command": command,
            "shell_command": result["shell_command"],
            "exit_code": result["exit_code"],
            "stdout": trim_output(result["stdout"]),
            "stderr": trim_output(result["stderr"]),
            "success": result["exit_code"] == 0,
        }


def remote_probe_gpus(server_entry: dict, *, path: str) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        binary_probe = run_remote_exec(session, "command -v nvidia-smi", timeout_sec=20)
        if binary_probe["exit_code"] != 0:
            return {
                "workspace_path": workspace_path,
                "available": False,
                "success": False,
                "gpus": [],
                "reason": "nvidia-smi unavailable",
                "stdout": trim_output(binary_probe["stdout"]),
                "stderr": trim_output(binary_probe["stderr"]),
            }
        result = run_remote_exec(
            session,
            "nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader,nounits",
            timeout_sec=20,
        )
        gpus = _parse_nvidia_smi_inventory(result["stdout"])
        success = result["exit_code"] == 0 and bool(gpus)
        return {
            "workspace_path": workspace_path,
            "available": result["exit_code"] == 0,
            "success": success,
            "gpus": gpus,
            "reason": None if success else (trim_output(result["stderr"]) or "gpu inventory unavailable"),
            "stdout": trim_output(result["stdout"]),
            "stderr": trim_output(result["stderr"]),
        }


def remote_list_screen_sessions(
    server_entry: dict,
    *,
    session_name: str | None = None,
    session_prefix: str | None = None,
) -> dict:
    with open_ssh_session(server_entry) as session:
        result = run_remote_exec(session, "screen -ls", timeout_sec=20)
        stdout = trim_output(result["stdout"])
        stderr = trim_output(result["stderr"])
        sessions = _parse_screen_sessions(stdout)
        filtered = list(sessions)
        requested_name = clean_text(session_name)
        if requested_name:
            filtered = [item for item in filtered if item["name"] == requested_name]
        prefix = clean_text(session_prefix)
        if prefix:
            filtered = [item for item in filtered if str(item["name"]).startswith(prefix)]
        success = result["exit_code"] == 0 or ("no sockets found" in stdout.lower())
        return {
            "command": "screen -ls",
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": result["exit_code"],
            "success": success,
            "sessions": filtered,
            "session_count": len(filtered),
        }


def remote_capture_screen_session(
    server_entry: dict,
    *,
    session_name: str,
    lines: int = 80,
) -> dict:
    target_name = sanitize_screen_session_name(session_name)
    line_count = max(1, min(int(lines or 80), 400))
    with open_ssh_session(server_entry) as session:
        hardcopy_path = f"/tmp/researchos-screen-{target_name}.txt"
        result = run_remote_exec(
            session,
            (
                f"screen -S {shlex.quote(target_name)} -X hardcopy {shlex.quote(hardcopy_path)} "
                f"&& tail -n {line_count} {shlex.quote(hardcopy_path)}"
            ),
            timeout_sec=30,
        )
        return {
            "session_name": target_name,
            "hardcopy_path": hardcopy_path,
            "command": result["shell_command"][-1] if result.get("shell_command") else None,
            "exit_code": result["exit_code"],
            "stdout": trim_output(result["stdout"]),
            "stderr": trim_output(result["stderr"]),
            "success": result["exit_code"] == 0,
        }


def remote_prepare_run_environment(
    server_entry: dict,
    *,
    path: str,
    run_directory: str,
    session_name: str,
) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        workspace_attr = remote_stat(session.sftp, workspace_path)
        if workspace_attr is None:
            raise WorkspaceAccessError(f"远程工作区不存在: {workspace_path}")
        if not remote_is_dir(workspace_attr):
            raise WorkspaceAccessError(f"远程工作区不是目录: {workspace_path}")

        resolved_run_directory = resolve_remote_workspace_path(server_entry, run_directory, session)
        _ensure_remote_directory(session, resolved_run_directory)
        execution_workspace = posixpath.join(resolved_run_directory, "workspace")
        _ensure_remote_directory(session, posixpath.dirname(execution_workspace))

        prepared_session_name = sanitize_screen_session_name(session_name)
        prepare_steps: list[dict] = []
        isolation_mode = "copied_workspace"
        created_worktree = False

        git_probe = run_remote_exec(
            session,
            "git rev-parse --is-inside-work-tree",
            cwd=workspace_path,
            timeout_sec=60,
        )
        git_available = git_probe["exit_code"] == 0
        git_branch = None
        git_head = None
        if git_available:
            git_branch = run_remote_exec(
                session,
                "git branch --show-current",
                cwd=workspace_path,
                timeout_sec=30,
            )["stdout"].strip() or None
            git_head = run_remote_exec(
                session,
                "git rev-parse HEAD",
                cwd=workspace_path,
                timeout_sec=30,
            )["stdout"].strip() or None
            worktree_result = run_remote_exec(
                session,
                f"git worktree add --detach {shlex.quote(execution_workspace)} HEAD",
                cwd=workspace_path,
                timeout_sec=180,
            )
            prepare_steps.append(
                {
                    "step": "git_worktree_add",
                    "command": worktree_result["shell_command"][-1] if worktree_result.get("shell_command") else None,
                    "exit_code": worktree_result["exit_code"],
                    "stdout": trim_output(worktree_result["stdout"], max_chars=4000),
                    "stderr": trim_output(worktree_result["stderr"], max_chars=4000),
                }
            )
            if worktree_result["exit_code"] == 0:
                isolation_mode = "git_worktree"
                created_worktree = True
            else:
                worktree_attr = remote_stat(session.sftp, execution_workspace)
                if worktree_attr is not None and remote_is_dir(worktree_attr):
                    reuse_probe = run_remote_exec(
                        session,
                        "git rev-parse --is-inside-work-tree",
                        cwd=execution_workspace,
                        timeout_sec=30,
                    )
                    if reuse_probe["exit_code"] == 0:
                        isolation_mode = "git_worktree_reuse"

        overlay_result = run_remote_exec(
            session,
            _remote_copy_overlay_command(workspace_path, execution_workspace),
            timeout_sec=600,
        )
        prepare_steps.append(
            {
                "step": "overlay_workspace_state",
                "command": overlay_result["shell_command"][-1] if overlay_result.get("shell_command") else None,
                "exit_code": overlay_result["exit_code"],
                "stdout": trim_output(overlay_result["stdout"], max_chars=4000),
                "stderr": trim_output(overlay_result["stderr"], max_chars=4000),
            }
        )
        if overlay_result["exit_code"] != 0:
            raise WorkspaceAccessError(
                overlay_result["stderr"].strip()
                or overlay_result["stdout"].strip()
                or "远程隔离工作区准备失败"
            )

        return {
            "workspace_path": workspace_path,
            "run_directory": resolved_run_directory,
            "execution_workspace": execution_workspace,
            "session_name": prepared_session_name,
            "isolation_mode": isolation_mode,
            "git_available": git_available,
            "git_branch": git_branch,
            "git_head": git_head,
            "created_worktree": created_worktree,
            "prepare_steps": prepare_steps,
        }


def remote_launch_screen_job(
    server_entry: dict,
    *,
    path: str,
    session_name: str,
    command: str,
    log_path: str,
    env_vars: dict[str, str] | None = None,
    timeout_sec: int = 30,
) -> dict:
    normalized_command = clean_text(command)
    if not normalized_command:
        raise WorkspaceAccessError("实验命令为空")
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        workspace_attr = remote_stat(session.sftp, workspace_path)
        if workspace_attr is None or not remote_is_dir(workspace_attr):
            raise WorkspaceAccessError(f"远程执行目录不可用: {workspace_path}")

        resolved_log_path = resolve_remote_workspace_path(server_entry, log_path, session)
        _ensure_remote_directory(session, posixpath.dirname(resolved_log_path))
        prepared_session_name = sanitize_screen_session_name(session_name)

        screen_probe = run_remote_exec(session, "command -v screen", timeout_sec=20)
        if screen_probe["exit_code"] != 0:
            raise WorkspaceAccessError("远程服务器缺少 screen，无法后台启动实验")

        before = remote_list_screen_sessions(server_entry, session_name=prepared_session_name)
        already_running = any(item["name"] == prepared_session_name for item in before.get("sessions") or [])
        if already_running:
            return {
                "workspace_path": workspace_path,
                "session_name": prepared_session_name,
                "log_path": resolved_log_path,
                "command": normalized_command,
                "env_vars": dict(env_vars or {}),
                "launch_command": None,
                "already_running": True,
                "launched": False,
                "success": True,
                "sessions": before.get("sessions") or [],
            }

        env_prefix = ""
        if env_vars:
            env_prefix = " ".join(
                f"export {key}={shlex.quote(str(value))} &&"
                for key, value in env_vars.items()
                if clean_text(key)
            ).strip()
        if env_prefix:
            env_prefix = env_prefix + " "
        launch_payload = (
            f"cd {shlex.quote(workspace_path)} && "
            f"{env_prefix}{normalized_command} 2>&1 | tee -a {shlex.quote(resolved_log_path)}"
        )
        launch_command = (
            f"screen -dmS {shlex.quote(prepared_session_name)} "
            f"bash -lc {shlex.quote(launch_payload)}"
        )
        launch_result = run_remote_exec(
            session,
            launch_command,
            timeout_sec=max(20, timeout_sec),
        )
        if launch_result["exit_code"] != 0:
            raise WorkspaceAccessError(
                launch_result["stderr"].strip()
                or launch_result["stdout"].strip()
                or "远程后台实验启动失败"
            )

        after = remote_list_screen_sessions(server_entry, session_name=prepared_session_name)
        session_items = after.get("sessions") or []
        launched = any(item["name"] == prepared_session_name for item in session_items)
        return {
            "workspace_path": workspace_path,
            "session_name": prepared_session_name,
            "log_path": resolved_log_path,
            "command": normalized_command,
            "env_vars": dict(env_vars or {}),
            "launch_command": launch_command,
            "already_running": False,
            "launched": launched,
            "success": launched,
            "stdout": trim_output(launch_result["stdout"]),
            "stderr": trim_output(launch_result["stderr"]),
            "sessions": session_items,
            "screen_list_stdout": after.get("stdout"),
            "screen_list_stderr": after.get("stderr"),
        }


def remote_git_init(server_entry: dict, *, path: str) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        run_remote_exec(session, f"mkdir -p {shlex.quote(workspace_path)}", timeout_sec=30)
        result = run_remote_exec(session, "git init", cwd=workspace_path, timeout_sec=60)
        if result["exit_code"] != 0:
            raise WorkspaceAccessError(result["stderr"].strip() or "Git 初始化失败")
        return {
            "ok": True,
            "workspace_path": workspace_path,
            "result": {"stdout": result["stdout"].strip(), "stderr": result["stderr"].strip(), "exit_code": result["exit_code"]},
            "git": remote_git_overview(session, workspace_path),
        }


def remote_git_branch(server_entry: dict, *, path: str, branch_name: str, checkout: bool = True) -> dict:
    if not branch_name.strip():
        raise WorkspaceAccessError("分支名不能为空")
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        git = remote_git_overview(session, workspace_path)
        if not git["is_repo"]:
            raise WorkspaceAccessError("当前目录尚未初始化 Git 仓库")
        ref_name = shlex.quote(f"refs/heads/{branch_name}")
        exists = run_remote_exec(session, f"git show-ref --verify --quiet {ref_name}", cwd=workspace_path, timeout_sec=60)["exit_code"] == 0
        if exists:
            command = f"git checkout {shlex.quote(branch_name)}" if checkout else f"git branch --list {shlex.quote(branch_name)}"
        else:
            command = f"git checkout -b {shlex.quote(branch_name)}" if checkout else f"git branch {shlex.quote(branch_name)}"
        result = run_remote_exec(session, command, cwd=workspace_path, timeout_sec=60)
        if result["exit_code"] != 0:
            raise WorkspaceAccessError(result["stderr"].strip() or "分支创建失败")
        return {
            "ok": True,
            "workspace_path": workspace_path,
            "branch": branch_name,
            "created": not exists,
            "checked_out": checkout,
            "result": {"stdout": result["stdout"].strip(), "stderr": result["stderr"].strip(), "exit_code": result["exit_code"]},
            "git": remote_git_overview(session, workspace_path),
        }


def remote_git_diff(server_entry: dict, *, path: str, file_path: str | None = None, max_chars: int = 120000) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        git = remote_git_overview(session, workspace_path)
        if not git["available"] or not git["is_repo"]:
            return {"workspace_path": workspace_path, "file_path": file_path, "diff": "", "truncated": False, "git": git, "message": git.get("message")}
        command = "git diff --no-ext-diff"
        normalized_file_path = None
        if file_path:
            normalized_file_path = normalize_relative_remote_path(file_path)
            command += f" -- {shlex.quote(normalized_file_path)}"
        result = run_remote_exec(session, command, cwd=workspace_path, timeout_sec=60)
        diff = result["stdout"]
        truncated = len(diff) > max_chars
        return {
            "workspace_path": workspace_path,
            "file_path": normalized_file_path,
            "diff": diff[:max_chars] + ("\n...[truncated]" if truncated else ""),
            "truncated": truncated,
            "git": remote_git_overview(session, workspace_path),
            "message": result["stderr"].strip() or None,
        }


def _ensure_remote_git_repo(session: SSHWorkspaceSession, workspace_path: str) -> dict:
    git = remote_git_overview(session, workspace_path)
    if not git["available"]:
        raise WorkspaceAccessError(git.get("message") or "远程环境未安装 Git")
    if not git["is_repo"]:
        raise WorkspaceAccessError(git.get("message") or "当前目录尚未初始化 Git 仓库")
    return git


def _remote_git_response(
    session: SSHWorkspaceSession,
    workspace_path: str,
    *,
    action: str,
    result: dict,
    file_path: str | None = None,
) -> dict:
    if int(result.get("exit_code") or 0) != 0:
        raise WorkspaceAccessError(clean_text(result.get("stderr")) or clean_text(result.get("stdout")) or f"Git {action} 失败")
    return {
        "ok": True,
        "workspace_path": workspace_path,
        "action": action,
        "file_path": file_path,
        "result": {
            "stdout": clean_text(result.get("stdout")),
            "stderr": clean_text(result.get("stderr")),
            "exit_code": result.get("exit_code"),
        },
        "git": remote_git_overview(session, workspace_path),
    }


def _remote_git_unstage(
    session: SSHWorkspaceSession,
    workspace_path: str,
    file_path: str | None = None,
) -> dict:
    target = normalize_relative_remote_path(file_path) if file_path else "."
    result = run_remote_exec(session, f"git restore --staged -- {shlex.quote(target)}", cwd=workspace_path, timeout_sec=60)
    if result["exit_code"] == 0:
        return result
    stderr = clean_text(result.get("stderr")).lower()
    if "could not resolve head" in stderr or "unknown revision" in stderr or "did not match any file" in stderr:
        fallback = run_remote_exec(session, f"git rm --cached -r -- {shlex.quote(target)}", cwd=workspace_path, timeout_sec=60)
        if fallback["exit_code"] == 0:
            return fallback
    return result


def remote_git_stage(server_entry: dict, *, path: str, file_path: str | None = None) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        _ensure_remote_git_repo(session, workspace_path)
        normalized_file_path = normalize_relative_remote_path(file_path) if file_path else None
        if normalized_file_path:
            command = f"git add -- {shlex.quote(normalized_file_path)}"
        else:
            command = "git add -A"
        result = run_remote_exec(session, command, cwd=workspace_path, timeout_sec=60)
        return _remote_git_response(session, workspace_path, action="stage", result=result, file_path=normalized_file_path)


def remote_git_unstage(server_entry: dict, *, path: str, file_path: str | None = None) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        _ensure_remote_git_repo(session, workspace_path)
        normalized_file_path = normalize_relative_remote_path(file_path) if file_path else None
        result = _remote_git_unstage(session, workspace_path, normalized_file_path)
        return _remote_git_response(session, workspace_path, action="unstage", result=result, file_path=normalized_file_path)


def remote_git_discard(server_entry: dict, *, path: str, file_path: str | None = None) -> dict:
    normalized_file_path = normalize_relative_remote_path(file_path or "")
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        git = _ensure_remote_git_repo(session, workspace_path)
        entry = next((item for item in git["entries"] if item.get("path") == normalized_file_path), None)
        if entry and clean_text(entry.get("code")) == "??":
            result = run_remote_exec(session, f"git clean -f -- {shlex.quote(normalized_file_path)}", cwd=workspace_path, timeout_sec=60)
            return _remote_git_response(session, workspace_path, action="discard", result=result, file_path=normalized_file_path)

        result = run_remote_exec(
            session,
            f"git restore --staged --worktree -- {shlex.quote(normalized_file_path)}",
            cwd=workspace_path,
            timeout_sec=60,
        )
        if result["exit_code"] != 0:
            stderr = clean_text(result.get("stderr")).lower()
            if "could not resolve head" in stderr or "unknown revision" in stderr:
                unstaged = _remote_git_unstage(session, workspace_path, normalized_file_path)
                if unstaged["exit_code"] == 0:
                    cleaned = run_remote_exec(session, f"git clean -f -- {shlex.quote(normalized_file_path)}", cwd=workspace_path, timeout_sec=60)
                    if cleaned["exit_code"] == 0:
                        result = cleaned
                    else:
                        result = unstaged
            if result["exit_code"] != 0:
                fallback = run_remote_exec(session, f"git checkout -- {shlex.quote(normalized_file_path)}", cwd=workspace_path, timeout_sec=60)
                if fallback["exit_code"] == 0:
                    result = fallback
        return _remote_git_response(session, workspace_path, action="discard", result=result, file_path=normalized_file_path)


def remote_git_commit(server_entry: dict, *, path: str, message: str) -> dict:
    commit_message = clean_text(message)
    if not commit_message:
        raise WorkspaceAccessError("提交说明不能为空")
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        _ensure_remote_git_repo(session, workspace_path)
        result = run_remote_exec(session, f"git commit -m {shlex.quote(commit_message)}", cwd=workspace_path, timeout_sec=90)
        return _remote_git_response(session, workspace_path, action="commit", result=result)


def remote_git_sync(server_entry: dict, *, path: str, action: str) -> dict:
    normalized = clean_text(action).lower()
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
        git = _ensure_remote_git_repo(session, workspace_path)
        if normalized == "fetch":
            result = run_remote_exec(session, "git fetch --all --prune", cwd=workspace_path, timeout_sec=120)
        elif normalized == "pull":
            result = run_remote_exec(session, "git pull --ff-only", cwd=workspace_path, timeout_sec=120)
        elif normalized == "push":
            result = run_remote_exec(session, "git push", cwd=workspace_path, timeout_sec=120)
            combined = "\n".join(clean_text(result.get(key)) for key in ("stdout", "stderr")).lower()
            if (
                result["exit_code"] != 0
                and git.get("branch")
                and git.get("remotes")
                and ("set the remote as upstream" in combined or "no upstream branch" in combined)
            ):
                result = run_remote_exec(
                    session,
                    f"git push -u {shlex.quote(str(git['remotes'][0]))} {shlex.quote(str(git['branch']))}",
                    cwd=workspace_path,
                    timeout_sec=120,
                )
        else:
            raise WorkspaceAccessError("不支持的 Git 同步动作")
        return _remote_git_response(session, workspace_path, action=normalized, result=result)


def remote_reveal(server_entry: dict, *, path: str) -> dict:
    with open_ssh_session(server_entry) as session:
        workspace_path = resolve_remote_workspace_path(server_entry, path, session)
    return {
        "path": workspace_path,
        "opened": False,
        "message": "SSH 远程工作区无法在本地直接打开资源管理器，请使用终端或 SFTP 访问。",
    }


def probe_ssh(payload: dict) -> dict:
    entry = {**payload, "enabled": True}
    try:
        with open_ssh_session(entry) as session:
            home_dir = session.home_dir
            workspace_root = None
            workspace_exists = None
            if clean_text(payload.get("workspace_root")):
                workspace_root = resolve_remote_workspace_path(entry, clean_text(payload.get("workspace_root")), session)
                workspace_exists = remote_stat(session.sftp, workspace_root) is not None
            return {"success": True, "message": "SSH 连接成功", "home_dir": home_dir, "workspace_root": workspace_root, "workspace_exists": workspace_exists}
    except Exception as error:
        return {"success": False, "message": str(error)}

