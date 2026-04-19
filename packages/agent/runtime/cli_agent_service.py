from __future__ import annotations

import json
import os
import posixpath
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

try:  # pragma: no cover - Python 3.11+ uses tomllib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from packages.agent.workspace.workspace_remote import (
    clean_text,
    mask_secret,
    open_ssh_session,
    remote_read_file,
    remote_stat,
    remote_terminal_result,
    remote_write_file,
    resolve_remote_workspace_path,
)
from packages.agent.runtime.acp_service import get_acp_registry_service
from packages.agent.mcp.researchos_mcp_registry import (
    RESEARCHOS_CONTEXT_MODE_ENV,
    RESEARCHOS_CONTEXT_SESSION_ID_ENV,
    RESEARCHOS_CONTEXT_WORKSPACE_PATH_ENV,
    RESEARCHOS_CONTEXT_WORKSPACE_SERVER_ID_ENV,
    RESEARCHOS_MCP_SERVER_NAME,
    bridge_qualified_tool_names,
)
from packages.agent.mcp.mcp_service import get_mcp_registry_service
from packages.ai.project.workflow_catalog import list_project_agent_templates
from packages.config import get_settings
from packages.integrations.llm_provider_schema import resolve_provider_protocol

_SUPPORTED_EXECUTION_AGENT_TYPES = {"codex", "claude_code", "claw"}
_AGENT_BINARY_CANDIDATES: dict[str, list[str]] = {
    "codex": ["codex"],
    "claude_code": ["claude"],
    "claw": ["claw"],
    "gemini": ["gemini", "gemini-cli"],
    "qwen": ["qwen"],
    "goose": ["goose"],
    "custom_acp": [],
}
_CONFIG_FILE_NAME = "agent_cli_configs.json"
_DEFAULT_EXECUTION_MODE = "auto"
_MAX_OUTPUT_CHARS = 200_000
_DEFAULT_CLAW_RUNTIME_DIRNAME = "claw-code-main"
_DEFAULT_CLAW_MCP_SERVER_NAME = RESEARCHOS_MCP_SERVER_NAME
_DEFAULT_CLAW_BINARY_ENV = "RESEARCHOS_CLAW_BINARY"
_DEFAULT_CLAW_MCP_MODE_FLAG = "--researchos-mcp-stdio"
_DEFAULT_CLAW_BRIDGE_DIRNAME = "claw-bridge-workspaces"
_DEFAULT_CLAW_CONFIG_DIRNAME = "claw-config"
_CLAW_MANAGED_MCP_NAMES_KEY = "researchosManagedMcpServers"


class RemoteCliCommandMissingError(RuntimeError):
    """Raised when required CLI command does not exist on SSH target."""


def _is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_repo_root(path: Path) -> bool:
    return path.joinpath("pyproject.toml").exists() and path.joinpath("apps", "desktop", "server.py").exists()


def _candidate_repo_roots() -> list[Path]:
    candidates: list[Path] = [_repo_root()]
    current_dir = Path.cwd().resolve()
    candidates.extend(current_dir.parents)
    executable = clean_text(getattr(sys, "executable", None))
    if executable:
        try:
            exe_path = Path(executable).resolve()
        except OSError:
            exe_path = Path(executable)
        candidates.extend(exe_path.parents)
    return _dedupe_paths([path for path in candidates if _is_repo_root(path)])


def _claw_runtime_root() -> Path:
    override = clean_text(os.environ.get("RESEARCHOS_CLAW_ROOT"))
    if override:
        return Path(override).expanduser()
    if _is_frozen_runtime():
        executable_root = Path(sys.executable).resolve().parent
        bundled_runtime_root = executable_root / _DEFAULT_CLAW_RUNTIME_DIRNAME
        if bundled_runtime_root.exists():
            return bundled_runtime_root
        meipass = clean_text(getattr(sys, "_MEIPASS", None))
        if meipass:
            meipass_runtime_root = Path(meipass).resolve() / _DEFAULT_CLAW_RUNTIME_DIRNAME
            if meipass_runtime_root.exists():
                return meipass_runtime_root
    return _repo_root() / _DEFAULT_CLAW_RUNTIME_DIRNAME


def _claw_rust_root() -> Path:
    return _claw_runtime_root() / "rust"


def _claw_binary_name() -> str:
    return "claw.exe" if os.name == "nt" else "claw"


def _looks_like_packaged_claw_runtime() -> bool:
    if _is_frozen_runtime():
        return True
    runtime_root = os.path.normcase(str(_claw_runtime_root()))
    executable = os.path.normcase(clean_text(getattr(sys, "executable", None)) or "")
    argv0 = os.path.normcase(clean_text(sys.argv[0] if sys.argv else None) or "")
    return any("_mei" in value for value in (runtime_root, executable, argv0) if value)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        normalized = str(path.expanduser())
        key = os.path.normcase(normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(Path(normalized))
    return deduped


def _resolved_home_dir(env: dict[str, str] | None = None) -> str | None:
    source = env or os.environ
    candidates: list[str] = []
    for key in ("HOME", "USERPROFILE"):
        value = clean_text(source.get(key))
        if value:
            candidates.append(value)
    drive = clean_text(source.get("HOMEDRIVE"))
    path = clean_text(source.get("HOMEPATH"))
    if drive and path:
        candidates.append(f"{drive}{path}")
    try:
        candidates.append(str(Path.home()))
    except Exception:
        pass
    return next((item for item in candidates if clean_text(item)), None)


def _ensure_local_cli_env(env: dict[str, str]) -> dict[str, str]:
    home_dir = _resolved_home_dir(env)
    if home_dir:
        env.setdefault("HOME", home_dir)
        if os.name == "nt":
            env.setdefault("USERPROFILE", home_dir)
    return env


def _claw_binary_parent_dirs() -> list[Path]:
    candidates: list[Path] = []
    current_dir = Path.cwd().resolve()
    candidates.append(current_dir)
    candidates.extend(current_dir.parents[:2])
    executable = clean_text(getattr(sys, "executable", None))
    if executable:
        try:
            executable_path = Path(executable).resolve()
        except OSError:
            executable_path = Path(executable)
        candidates.append(executable_path.parent)
    argv0 = clean_text(sys.argv[0] if sys.argv else None)
    if argv0:
        try:
            argv0_path = Path(argv0).resolve()
        except OSError:
            argv0_path = Path(argv0)
        candidates.append(argv0_path.parent)
    meipass = clean_text(getattr(sys, "_MEIPASS", None))
    if meipass:
        try:
            meipass_path = Path(meipass).resolve()
        except OSError:
            meipass_path = Path(meipass)
        candidates.append(meipass_path.parent)
    return _dedupe_paths(candidates)


def _append_claw_binary_names(candidates: list[Path], directory: Path) -> None:
    binary_name = _claw_binary_name()
    candidates.append(directory / binary_name)
    if not directory.exists():
        return
    if os.name == "nt":
        candidates.extend(
            path
            for path in sorted(directory.glob("claw*.exe"))
            if path.is_file()
        )
        return
    candidates.extend(
        path
        for path in sorted(directory.glob("claw*"))
        if path.is_file()
    )


def _claw_binary_candidates() -> list[Path]:
    rust_root = _claw_rust_root()
    candidates: list[Path] = []
    explicit_binary = clean_text(os.environ.get(_DEFAULT_CLAW_BINARY_ENV))
    if explicit_binary:
        candidates.append(Path(explicit_binary).expanduser())
    if _is_frozen_runtime():
        executable_root = Path(sys.executable).resolve().parent
        _append_claw_binary_names(candidates, executable_root)
        meipass = clean_text(getattr(sys, "_MEIPASS", None))
        if meipass:
            _append_claw_binary_names(candidates, Path(meipass).resolve())
        for repo_root in _candidate_repo_roots():
            binaries_dir = repo_root / "src-tauri" / "binaries"
            _append_claw_binary_names(candidates, binaries_dir)
    candidates.extend(
        [
            rust_root / "target" / "release" / _claw_binary_name(),
            rust_root / "target" / "debug" / _claw_binary_name(),
        ]
    )
    for parent in _claw_binary_parent_dirs():
        _append_claw_binary_names(candidates, parent)
    return _dedupe_paths(candidates)


def _preferred_claw_binary_candidate() -> Path:
    candidates = _claw_binary_candidates()
    return next((item for item in candidates if item.exists()), candidates[0])


def _claw_bootstrap_available() -> bool:
    return _claw_rust_root().exists() and shutil.which("cargo") is not None


def _ensure_claw_binary(timeout_sec: int = 1800) -> Path:
    existing = next((item for item in _claw_binary_candidates() if item.exists()), None)
    if existing is not None:
        return existing

    rust_root = _claw_rust_root()
    if not rust_root.exists():
        raise RuntimeError(f"未找到 claw Rust 工作区：{rust_root}")
    if shutil.which("cargo") is None:
        raise RuntimeError("未检测到 cargo，无法自动构建 claw 后端")

    result = subprocess.run(
        ["cargo", "build", "--workspace"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        cwd=str(rust_root),
        timeout=max(30, timeout_sec),
        check=False,
    )
    if result.returncode != 0:
        detail = _trim_text(result.stderr or result.stdout)
        raise RuntimeError(detail or "构建 claw 后端失败")

    built = next((item for item in _claw_binary_candidates() if item.exists()), None)
    if built is None:
        raise RuntimeError("claw 构建完成，但没有找到可执行文件")
    return built


def _infer_claw_provider(
    model: str | None,
    protocol_hint: str | None = None,
    provider_hint: str | None = None,
    base_url: str | None = None,
) -> str:
    protocol_value = (clean_text(protocol_hint) or "").lower()
    if protocol_value in {"openai", "anthropic"}:
        return protocol_value

    provider_value = (clean_text(provider_hint) or "").lower()
    model_value = (clean_text(model) or "").lower()
    base_url_value = (clean_text(base_url) or "").lower()
    combined_hint = " ".join(
        part for part in (provider_value, model_value, base_url_value) if part
    )

    if provider_value in {"openai", "openai-compatible", "openai_compatible"}:
        return "openai"
    if provider_value in {"xai"}:
        return "xai"
    if provider_value in {"anthropic"}:
        return "anthropic"

    if (
        model_value.startswith("claude")
        or "anthropic" in provider_value
        or "claude-" in model_value
        or "anthropic" in base_url_value
    ):
        return "anthropic"
    if (
        model_value.startswith(("grok", "xai"))
        or "x.ai" in combined_hint
        or "xai" in provider_value
    ):
        return "xai"
    if (
        model_value.startswith(("gpt", "o1", "o3", "o4", "gemini", "glm", "qwen", "kimi", "minimax"))
        or any(
            token in combined_hint
            for token in (
                "openai",
                "openai-compatible",
                "openai_compatible",
                "google",
                "googleapis",
                "generativelanguage",
                "zhipu",
                "bigmodel",
                "dashscope",
                "aliyuncs",
                "aliyun",
                "bailian",
                "moonshot",
                "minimax",
                "siliconflow",
                "openrouter",
                "deepseek",
                "venice",
            )
        )
    ):
        return "openai"
    return "anthropic"


def _normalize_claw_base_url(base_url: str | None, provider_kind: str) -> str | None:
    value = clean_text(base_url)
    if not value:
        return None
    candidate = value.rstrip("/")
    lowered = candidate.lower()
    suffixes = (
        ("/chat/completions", "/responses")
        if provider_kind in {"openai", "xai"}
        else ("/v1/messages/count_tokens", "/v1/messages", "/messages/count_tokens", "/messages", "/v1")
    )
    for suffix in suffixes:
        if lowered.endswith(suffix):
            trimmed = candidate[: len(candidate) - len(suffix)].rstrip("/")
            return trimmed or candidate
    return candidate


def _load_active_llm_defaults() -> dict[str, str | None]:
    try:
        from packages.integrations.llm_client import build_llm_config_from_record
        from packages.storage.db import session_scope
        from packages.storage.repositories import LLMConfigRepository
    except Exception:
        return {}

    try:
        with session_scope() as session:
            active = LLMConfigRepository(session).get_active()
            if active is None:
                return {}
            cfg = build_llm_config_from_record(active)
    except Exception:
        return {}

    default_model = (
        clean_text(cfg.model_deep)
        or clean_text(cfg.model_fallback)
        or clean_text(cfg.model_skim)
        or None
    )
    return {
        "provider": clean_text(cfg.provider) or None,
        "protocol": resolve_provider_protocol(
            clean_text(cfg.provider) or None,
            clean_text(cfg.api_base_url) or None,
        )
        or None,
        "base_url": clean_text(cfg.api_base_url) or None,
        "api_key": clean_text(cfg.api_key) or None,
        "default_model": default_model,
        "config_source": "ResearchOS active LLM config",
    }


def _merge_pythonpath(repo_root: Path, existing: str | None = None) -> str:
    parts = [str(repo_root)]
    if existing:
        parts.extend(part for part in str(existing).split(os.pathsep) if part)
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = part.strip()
        if not normalized:
            continue
        key = os.path.normcase(normalized)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return os.pathsep.join(deduped)


def _merge_remote_pythonpath(repo_root: str, existing: str | None = None) -> str:
    parts = [clean_text(repo_root)]
    if existing:
        parts.extend(part.strip() for part in str(existing).split(":") if part.strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        normalized = part.strip()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return ":".join(deduped)


def _claw_bridge_root() -> Path:
    data_dir = clean_text(os.environ.get("RESEARCHOS_DATA_DIR"))
    if data_dir:
        return Path(data_dir).expanduser() / _DEFAULT_CLAW_BRIDGE_DIRNAME
    return Path(tempfile.gettempdir()) / "ResearchOS" / _DEFAULT_CLAW_BRIDGE_DIRNAME


def _ensure_claw_bridge_workspace(
    workspace_path: str | None,
    workspace_server_id: str | None,
) -> Path:
    server_id = clean_text(workspace_server_id) or "local"
    workspace_value = clean_text(workspace_path).replace("\\", "/")
    label_source = workspace_value.rstrip("/").split("/")[-1] if workspace_value else server_id
    label = _slugify(label_source) or "workspace"
    digest = hashlib.sha1(f"{server_id}::{workspace_value}".encode("utf-8")).hexdigest()[:16]
    workspace_dir = _claw_bridge_root() / f"{_slugify(server_id)}-{label}-{digest}"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def _session_mode_for_claw(session_id: str | None) -> str:
    normalized_session_id = clean_text(session_id)
    if not normalized_session_id:
        return "build"
    try:
        from packages.agent.session.session_runtime import get_session_record

        session_record = get_session_record(normalized_session_id) or {}
    except Exception:
        return "build"
    return clean_text(session_record.get("mode")) or "build"


def _chunk_items(items: list[str], size: int) -> list[list[str]]:
    normalized_size = max(1, int(size or 1))
    return [items[index : index + normalized_size] for index in range(0, len(items), normalized_size)]


def _researchos_data_dir() -> Path | None:
    explicit_data_dir = clean_text(os.environ.get("RESEARCHOS_DATA_DIR"))
    if explicit_data_dir:
        return Path(explicit_data_dir).expanduser().resolve()
    try:
        settings = get_settings()
    except Exception:
        return None
    try:
        return settings.pdf_storage_root.parent.resolve()
    except Exception:
        return None


def _researchos_runtime_env_overrides() -> dict[str, str]:
    inherited_env = {
        key: value
        for key in (
            "RESEARCHOS_DATA_DIR",
            "RESEARCHOS_ENV_FILE",
            "DATABASE_URL",
            "PDF_STORAGE_ROOT",
            "BRIEF_OUTPUT_ROOT",
        )
        for value in [clean_text(os.environ.get(key))]
        if value
    }
    try:
        settings = get_settings()
    except Exception:
        return inherited_env

    data_dir = _researchos_data_dir()
    if data_dir is None:
        return inherited_env
    configured_database_url = clean_text(os.environ.get("DATABASE_URL")) or str(settings.database_url)
    if configured_database_url.lower().startswith("sqlite:///"):
        database_url = f"sqlite:///{(data_dir / 'researchos.db').resolve().as_posix()}"
    else:
        database_url = configured_database_url
    resolved_env = {
        "RESEARCHOS_DATA_DIR": str(data_dir),
        "DATABASE_URL": database_url,
        "PDF_STORAGE_ROOT": clean_text(os.environ.get("PDF_STORAGE_ROOT")) or str((data_dir / "papers").resolve()),
        "BRIEF_OUTPUT_ROOT": clean_text(os.environ.get("BRIEF_OUTPUT_ROOT")) or str((data_dir / "briefs").resolve()),
    }
    env_file = clean_text(os.environ.get("RESEARCHOS_ENV_FILE"))
    if env_file:
        resolved_env["RESEARCHOS_ENV_FILE"] = env_file
    return {
        **inherited_env,
        **{key: value for key, value in resolved_env.items() if clean_text(value)},
    }


def _claw_config_home_dir() -> Path:
    data_dir = _researchos_data_dir()
    base_dir = data_dir if data_dir is not None else Path(tempfile.gettempdir()) / "ResearchOS"
    config_dir = base_dir / _DEFAULT_CLAW_CONFIG_DIRNAME
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def _configured_claw_custom_mcp_servers() -> dict[str, dict[str, Any]]:
    try:
        config = get_mcp_registry_service().get_config()
    except Exception:
        return {}

    raw_servers = config.get("servers") if isinstance(config, dict) else {}
    if not isinstance(raw_servers, dict):
        return {}

    configured: dict[str, dict[str, Any]] = {}
    for name, raw in raw_servers.items():
        server_name = clean_text(name)
        if not server_name or not isinstance(raw, dict):
            continue
        if bool(raw.get("builtin")):
            continue
        if server_name in {_DEFAULT_CLAW_MCP_SERVER_NAME, "researchos"}:
            continue
        if not bool(raw.get("enabled", True)):
            continue
        transport = clean_text(raw.get("transport")).lower() or "stdio"
        timeout_ms = max(5, min(int(raw.get("timeout_sec") or 30), 300)) * 1000
        if transport == "http":
            url = clean_text(raw.get("url"))
            if not url:
                continue
            configured[server_name] = {
                "url": url,
                "headers": {
                    str(key): str(value)
                    for key, value in (raw.get("headers") or {}).items()
                    if clean_text(key)
                },
                "toolCallTimeoutMs": timeout_ms,
            }
            continue

        command = clean_text(raw.get("command"))
        if not command:
            continue
        configured[server_name] = {
            "command": command,
            "args": [str(item) for item in (raw.get("args") or []) if clean_text(item)],
            "cwd": clean_text(raw.get("cwd")) or None,
            "env": {
                str(key): str(value)
                for key, value in (raw.get("env") or {}).items()
                if clean_text(key)
            },
            "toolCallTimeoutMs": timeout_ms,
        }
    return configured


def _sync_managed_claw_mcp_servers(
    payload: dict[str, Any],
    mcp_servers: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    changed = False
    managed_names = {
        clean_text(item)
        for item in (payload.get(_CLAW_MANAGED_MCP_NAMES_KEY) or [])
        if clean_text(item)
    }
    for name in list(managed_names):
        if name in mcp_servers:
            del mcp_servers[name]
            changed = True

    configured = _configured_claw_custom_mcp_servers()
    for name, server in configured.items():
        if mcp_servers.get(name) != server:
            mcp_servers[name] = server
            changed = True

    next_managed_names = sorted(configured.keys())
    if next_managed_names:
        if payload.get(_CLAW_MANAGED_MCP_NAMES_KEY) != next_managed_names:
            payload[_CLAW_MANAGED_MCP_NAMES_KEY] = next_managed_names
            changed = True
    elif _CLAW_MANAGED_MCP_NAMES_KEY in payload:
        payload.pop(_CLAW_MANAGED_MCP_NAMES_KEY, None)
        changed = True
    return mcp_servers, changed


def _ensure_claw_workspace_settings(workspace_dir: Path) -> Path:
    settings_dir = workspace_dir / ".claw"
    settings_dir.mkdir(parents=True, exist_ok=True)
    settings_path = settings_dir / "settings.local.json"

    payload = _load_json_file(settings_path)
    if not isinstance(payload, dict):
        payload = {}

    mcp_servers = payload.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        mcp_servers = {}

    existing_server = mcp_servers.get(_DEFAULT_CLAW_MCP_SERVER_NAME)
    existing_env = existing_server.get("env") if isinstance(existing_server, dict) else {}
    inherited_env = {
        **(existing_env if isinstance(existing_env, dict) else {}),
        **_researchos_runtime_env_overrides(),
    }
    if _is_frozen_runtime():
        command = sys.executable
        args = [_DEFAULT_CLAW_MCP_MODE_FLAG]
        merged_env = inherited_env
    else:
        repo_root = _repo_root()
        command = sys.executable
        args = ["-m", "packages.agent.mcp.researchos_mcp"]
        merged_env = {
            **inherited_env,
            "PYTHONPATH": _merge_pythonpath(
                repo_root,
                str(inherited_env.get("PYTHONPATH") or ""),
            ),
        }
    desired_server = {
        **(existing_server if isinstance(existing_server, dict) else {}),
        "command": command,
        "args": args,
        "env": merged_env,
        "toolCallTimeoutMs": 600_000,
    }

    changed = False
    if existing_server != desired_server:
        mcp_servers[_DEFAULT_CLAW_MCP_SERVER_NAME] = desired_server
        changed = True

    mcp_servers, sync_changed = _sync_managed_claw_mcp_servers(payload, mcp_servers)
    changed = changed or sync_changed

    if not changed and payload.get("mcpServers") == mcp_servers:
        return settings_path

    payload["mcpServers"] = mcp_servers
    settings_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return settings_path


def _load_remote_json_file(
    server_entry: dict[str, Any],
    *,
    workspace_path: str,
    relative_path: str,
) -> Any:
    try:
        payload = remote_read_file(
            server_entry,
            workspace_path,
            relative_path,
            max_chars=_MAX_OUTPUT_CHARS,
        )
    except Exception as exc:
        if "文件不存在" in str(exc):
            return None
        raise
    try:
        return json.loads(str(payload.get("content") or ""))
    except (TypeError, json.JSONDecodeError):
        return None


def _resolve_remote_claw_repo_root(
    server_entry: dict[str, Any],
    *,
    session,
    remote_workspace_path: str,
) -> str | None:
    candidates: list[str] = []
    configured_root = clean_text(server_entry.get("workspace_root"))
    if configured_root:
        candidates.append(resolve_remote_workspace_path(server_entry, configured_root, session))
    if remote_workspace_path:
        candidates.append(remote_workspace_path)

    seen: set[str] = set()
    for candidate in candidates:
        normalized = clean_text(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        mcp_module_path = posixpath.join(normalized, "packages", "ai", "researchos_mcp.py")
        if remote_stat(session.sftp, mcp_module_path) is not None:
            return normalized
    return None


def _ensure_remote_claw_workspace_settings(
    server_entry: dict[str, Any],
    *,
    workspace_path: str,
    remote_workspace_path: str,
    researchos_repo_root: str | None,
) -> str | None:
    settings_relative_path = ".claw/settings.local.json"
    payload = _load_remote_json_file(
        server_entry,
        workspace_path=workspace_path,
        relative_path=settings_relative_path,
    )
    if not isinstance(payload, dict):
        payload = {}

    existing_servers = payload.get("mcpServers")
    mcp_servers = dict(existing_servers) if isinstance(existing_servers, dict) else {}
    changed = False

    if researchos_repo_root:
        existing_server = mcp_servers.get(_DEFAULT_CLAW_MCP_SERVER_NAME)
        existing_env = existing_server.get("env") if isinstance(existing_server, dict) else {}
        desired_server = {
            **(existing_server if isinstance(existing_server, dict) else {}),
            "command": "bash",
            "args": [
                "-lc",
                (
                    "if command -v python3 >/dev/null 2>&1; then "
                    "exec python3 -m packages.agent.mcp.researchos_mcp; "
                    "else exec python -m packages.agent.mcp.researchos_mcp; fi"
                ),
            ],
            "env": {
                **(existing_env if isinstance(existing_env, dict) else {}),
                "PYTHONPATH": _merge_remote_pythonpath(
                    researchos_repo_root,
                    str((existing_env or {}).get("PYTHONPATH") or ""),
                ),
            },
            "toolCallTimeoutMs": 600_000,
        }
        if existing_server != desired_server:
            mcp_servers[_DEFAULT_CLAW_MCP_SERVER_NAME] = desired_server
            changed = True
    elif _DEFAULT_CLAW_MCP_SERVER_NAME in mcp_servers:
        del mcp_servers[_DEFAULT_CLAW_MCP_SERVER_NAME]
        changed = True

    mcp_servers, sync_changed = _sync_managed_claw_mcp_servers(payload, mcp_servers)
    changed = changed or sync_changed

    if mcp_servers:
        if payload.get("mcpServers") != mcp_servers:
            payload["mcpServers"] = mcp_servers
            changed = True
    elif "mcpServers" in payload:
        payload.pop("mcpServers", None)
        changed = True

    if changed:
        remote_write_file(
            server_entry,
            path=workspace_path,
            relative_path=settings_relative_path,
            content=json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            create_dirs=True,
            overwrite=True,
        )

    if changed or researchos_repo_root or isinstance(existing_servers, dict):
        return posixpath.join(remote_workspace_path, ".claw", "settings.local.json")
    return None


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _slugify(value: str) -> str:
    chars = []
    for char in str(value or "").strip().lower():
        if char.isalnum() or char in {"_", "-", "."}:
            chars.append(char)
        else:
            chars.append("-")
    return "".join(chars).strip("-") or "agent"


def _trim_text(value: str | None, limit: int = 12_000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


def _load_json_file(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = (text or "").strip()
    if not candidate:
        return None

    fenced = candidate
    if fenced.startswith("```"):
        parts = fenced.split("```")
        if len(parts) >= 3:
            fenced = parts[1]
            if "\n" in fenced:
                fenced = fenced.split("\n", 1)[1]
        candidate = fenced.strip()

    for raw in (candidate, candidate.splitlines()[-1].strip()):
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        snippet = candidate[start : end + 1]
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


class CliAgentService:
    def __init__(self) -> None:
        templates = list_project_agent_templates()
        self._template_map = {
            str(item.get("id")): item
            for item in templates
            if str(item.get("id") or "").strip() and str(item.get("id")) != "researchos_native"
        }

    def list_configs(self) -> list[dict[str, Any]]:
        return [self._serialize_config(item) for item in self._load_configs()]

    def get_config(self, config_id: str) -> dict[str, Any]:
        config = self._find_config(config_id)
        if config is None:
            raise ValueError(f"未找到智能体配置：{config_id}")
        return self._serialize_config(config)

    def get_runtime_config(self, config_id: str) -> dict[str, Any]:
        agent_type = clean_text(config_id)
        if agent_type not in self._template_map:
            raise ValueError(f"未找到智能体配置：{config_id}")
        config = self._find_config(agent_type) or self._normalize_config({"agent_type": agent_type})
        return self._resolve_config(config)

    def upsert_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_type = clean_text(payload.get("agent_type"))
        if agent_type not in self._template_map:
            raise ValueError("不支持的智能体类型")

        current_items = self._load_configs()
        now = _iso_now()
        incoming = {
            "id": agent_type,
            "agent_type": agent_type,
            "label": clean_text(payload.get("label"))
            or self._template_map[agent_type].get("label")
            or agent_type,
            "enabled": bool(payload.get("enabled", True)),
            "command": clean_text(payload.get("command"))
            or (_AGENT_BINARY_CANDIDATES.get(agent_type) or [""])[0],
            "args": [str(item).strip() for item in (payload.get("args") or []) if str(item).strip()],
            "provider": clean_text(payload.get("provider")) or None,
            "base_url": clean_text(payload.get("base_url")) or None,
            "api_key": payload.get("api_key"),
            "default_model": clean_text(payload.get("default_model")) or None,
            "workspace_server_id": clean_text(payload.get("workspace_server_id")) or None,
            "execution_mode": self._normalize_execution_mode(payload.get("execution_mode")),
            "metadata": dict(payload.get("metadata") or {}),
            "created_at": now,
            "updated_at": now,
        }

        existing = next((item for item in current_items if item["id"] == agent_type), None)
        if existing is not None:
            incoming["created_at"] = existing.get("created_at") or now
            incoming["api_key"] = self._merge_secret(payload.get("api_key"), existing.get("api_key"))
            current_items = [item for item in current_items if item["id"] != agent_type]
        else:
            incoming["api_key"] = clean_text(payload.get("api_key")) or None

        current_items.append(self._normalize_config(incoming))
        self._save_configs(current_items)
        return self.get_config(agent_type)

    def delete_config(self, config_id: str) -> bool:
        config_id = _slugify(config_id)
        current_items = self._load_configs()
        remaining = [item for item in current_items if item["id"] != config_id]
        if len(remaining) == len(current_items):
            return False
        self._save_configs(remaining)
        return True

    def detect_agents(self) -> list[dict[str, Any]]:
        configs = {item["agent_type"]: item for item in self._load_configs()}
        items: list[dict[str, Any]] = []
        for agent_type, template in self._template_map.items():
            config = configs.get(agent_type) or self._normalize_config({"agent_type": agent_type})
            detected = self._resolve_config(config)
            capability = self._build_chat_capability(detected)
            acp_summary = get_acp_registry_service().get_backend_summary() if agent_type == "custom_acp" else {}
            items.append(
                {
                    "agent_type": agent_type,
                    "label": template.get("label") or agent_type,
                    "kind": template.get("kind") or "cli",
                    "description": template.get("description") or "",
                    "installed": detected["installed"],
                    "supported": capability["chat_supported"],
                    "chat_supported": capability["chat_supported"],
                    "chat_ready": capability["chat_ready"],
                    "chat_status": capability["chat_status"],
                    "chat_status_label": capability["chat_status_label"],
                    "chat_blocked_reason": capability["chat_blocked_reason"],
                    "acp_server_name": acp_summary.get("default_server"),
                    "acp_server_label": acp_summary.get("default_server_label"),
                    "acp_transport": acp_summary.get("default_transport"),
                    "acp_connected": acp_summary.get("default_connected"),
                    "binary_path": detected.get("command_path"),
            "command": detected.get("command"),
            "provider": detected.get("provider"),
            "protocol": detected.get("protocol"),
            "base_url": detected.get("base_url"),
            "default_model": detected.get("default_model"),
            "config_source": detected.get("config_source"),
                    "has_api_key": bool(detected.get("api_key")),
                    "api_key_masked": mask_secret(detected.get("api_key")),
                    "message": self._detect_message(agent_type, detected),
                }
            )
        return items

    def test_config(
        self,
        config_id: str,
        *,
        prompt: str,
        workspace_path: str | None = None,
        workspace_server_id: str | None = None,
        timeout_sec: int = 180,
    ) -> dict[str, Any]:
        config = self._find_config(config_id)
        if config is None:
            raise ValueError(f"未找到智能体配置：{config_id}")
        return self.execute_prompt(
            config["agent_type"],
            prompt=prompt,
            workspace_path=workspace_path,
            workspace_server_id=workspace_server_id or config.get("workspace_server_id"),
            timeout_sec=timeout_sec,
        )

    def execute_prompt(
        self,
        agent_type: str,
        *,
        prompt: str,
        workspace_path: str | None,
        workspace_server_id: str | None = None,
        timeout_sec: int = 600,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        agent_type = clean_text(agent_type)
        if agent_type not in self._template_map:
            raise ValueError(f"不支持的智能体类型：{agent_type}")

        config = self._find_config(agent_type) or self._normalize_config({"agent_type": agent_type})
        resolved = self._resolve_config(config)
        capability = self._build_chat_capability(resolved)
        if not capability["chat_ready"]:
            raise ValueError(capability["chat_blocked_reason"] or f"{resolved['label']} 当前不可用于聊天")
        if not clean_text(prompt):
            raise ValueError("prompt 不能为空")

        if agent_type == "custom_acp":
            return get_acp_registry_service().execute_prompt(
                prompt=prompt,
                workspace_path=workspace_path,
                workspace_server_id=workspace_server_id or resolved.get("workspace_server_id"),
                timeout_sec=timeout_sec,
                session_id=session_id,
            )

        configured_mode = self._normalize_execution_mode(resolved.get("execution_mode"))
        execution_mode = self._resolve_execution_mode(resolved, workspace_server_id=workspace_server_id)
        target_workspace_path = clean_text(workspace_path) or None

        def _auto_fallback_to_local(reason: str) -> dict[str, Any]:
            fallback_workspace = self._resolve_local_fallback_workspace(target_workspace_path)
            try:
                result = self._execute_local(
                    resolved,
                    prompt=prompt,
                    workspace_path=fallback_workspace,
                    timeout_sec=timeout_sec,
                    session_id=session_id,
                    requested_workspace_path=fallback_workspace,
                    requested_workspace_server_id=None,
                )
            except Exception as local_exc:
                raise RuntimeError(f"{reason}；自动回退本地执行失败：{local_exc}") from local_exc
            result["fallback_reason"] = reason
            return result

        if execution_mode == "ssh":
            if not target_workspace_path:
                if configured_mode != "auto":
                    raise ValueError("远程执行需要提供 workspace_path")
                return _auto_fallback_to_local("远程执行需要提供 workspace_path，已自动回退本地执行。")
            try:
                return self._execute_remote(
                    resolved,
                    prompt=prompt,
                    workspace_path=target_workspace_path,
                    workspace_server_id=workspace_server_id,
                    timeout_sec=timeout_sec,
                )
            except RemoteCliCommandMissingError as exc:
                if configured_mode != "auto":
                    raise
                return _auto_fallback_to_local(str(exc))
            except Exception as exc:
                if configured_mode != "auto" or not self._looks_like_remote_transport_error(exc):
                    raise
                return _auto_fallback_to_local(f"SSH 远程执行异常：{exc}")
        try:
            return self._execute_local(
                resolved,
                prompt=prompt,
                workspace_path=target_workspace_path,
                timeout_sec=timeout_sec,
                session_id=session_id,
                requested_workspace_path=target_workspace_path,
                requested_workspace_server_id=workspace_server_id or resolved.get("workspace_server_id"),
            )
        except ValueError as exc:
            if configured_mode != "auto" or not self._looks_like_local_workspace_error(exc):
                raise
            return _auto_fallback_to_local(f"{exc}；已自动回退到可用本地工作目录。")

    def _resolve_execution_mode(self, config: dict[str, Any], *, workspace_server_id: str | None) -> str:
        if clean_text(config.get("agent_type")) == "claw":
            return "local"
        mode = self._normalize_execution_mode(config.get("execution_mode"))
        remote_server_id = clean_text(workspace_server_id) or clean_text(config.get("workspace_server_id"))
        if mode == "ssh":
            if not remote_server_id:
                raise ValueError("当前配置未绑定 SSH 服务器")
            return "ssh"
        if mode == "local":
            return "local"
        return "ssh" if remote_server_id else "local"

    def _execute_local(
        self,
        config: dict[str, Any],
        *,
        prompt: str,
        workspace_path: str | None,
        timeout_sec: int,
        session_id: str | None,
        requested_workspace_path: str | None = None,
        requested_workspace_server_id: str | None = None,
    ) -> dict[str, Any]:
        if config["agent_type"] == "codex":
            workspace_dir = Path(workspace_path or Path.cwd()).expanduser()
            if not workspace_dir.exists():
                raise ValueError(f"本地工作区不存在：{workspace_dir}")
            if not workspace_dir.is_dir():
                raise ValueError(f"本地工作区不是目录：{workspace_dir}")
            return self._run_local_codex(
                config,
                prompt=prompt,
                workspace_dir=workspace_dir,
                timeout_sec=timeout_sec,
            )
        if config["agent_type"] == "claude_code":
            workspace_dir = Path(workspace_path or Path.cwd()).expanduser()
            if not workspace_dir.exists():
                raise ValueError(f"本地工作区不存在：{workspace_dir}")
            if not workspace_dir.is_dir():
                raise ValueError(f"本地工作区不是目录：{workspace_dir}")
            return self._run_local_claude(
                config,
                prompt=prompt,
                workspace_dir=workspace_dir,
                timeout_sec=timeout_sec,
            )
        if config["agent_type"] == "claw":
            bound_server_id = clean_text(requested_workspace_server_id)
            target_workspace_path = clean_text(requested_workspace_path) or workspace_path or None
            if bound_server_id and bound_server_id.lower() != "local":
                workspace_dir = _ensure_claw_bridge_workspace(target_workspace_path, bound_server_id)
            else:
                workspace_dir = Path(workspace_path or Path.cwd()).expanduser()
                if not workspace_dir.exists():
                    raise ValueError(f"本地工作区不存在：{workspace_dir}")
                if not workspace_dir.is_dir():
                    raise ValueError(f"本地工作区不是目录：{workspace_dir}")
            return self._run_local_claw(
                config,
                prompt=prompt,
                workspace_dir=workspace_dir,
                timeout_sec=timeout_sec,
                session_id=session_id,
                requested_workspace_path=target_workspace_path or str(workspace_dir),
                requested_workspace_server_id=bound_server_id or None,
            )
        raise ValueError(f"当前智能体暂不支持本地执行：{config['agent_type']}")

    def _execute_remote(
        self,
        config: dict[str, Any],
        *,
        prompt: str,
        workspace_path: str,
        workspace_server_id: str | None = None,
        timeout_sec: int,
    ) -> dict[str, Any]:
        bound_server_id = clean_text(workspace_server_id) or clean_text(config.get("workspace_server_id"))
        server_entry = self._find_workspace_server_entry(bound_server_id)
        if server_entry is None:
            raise ValueError("未找到绑定的 SSH 工作区服务器")

        remote_claw_repo_root: str | None = None
        with open_ssh_session(server_entry) as session:
            remote_workspace_path = resolve_remote_workspace_path(server_entry, workspace_path, session)
            remote_attr = remote_stat(session.sftp, remote_workspace_path)
            if remote_attr is None:
                raise ValueError(f"远程工作区不存在：{remote_workspace_path}")
            if config["agent_type"] == "claw":
                remote_claw_repo_root = _resolve_remote_claw_repo_root(
                    server_entry,
                    session=session,
                    remote_workspace_path=remote_workspace_path,
                )

        run_dir = f".researchos/agent_runs/{config['agent_type']}-{int(time.time())}"
        prompt_rel = f"{run_dir}/prompt.txt"
        output_rel = f"{run_dir}/output.txt"
        remote_write_file(
            server_entry,
            path=workspace_path,
            relative_path=prompt_rel,
            content=prompt,
            create_dirs=True,
            overwrite=True,
        )

        started_at = time.perf_counter()
        if config["agent_type"] == "codex":
            command = self._build_remote_codex_command(config, prompt_rel=prompt_rel, output_rel=output_rel)
        elif config["agent_type"] == "claude_code":
            command = self._build_remote_claude_command(config, prompt_rel=prompt_rel, output_rel=output_rel)
        elif config["agent_type"] == "claw":
            bridge_session_ref = _slugify(f"researchos-remote-{bound_server_id}-{int(time.time())}")
            remote_settings_path = _ensure_remote_claw_workspace_settings(
                server_entry,
                workspace_path=workspace_path,
                remote_workspace_path=remote_workspace_path,
                researchos_repo_root=remote_claw_repo_root,
            )
            command = self._build_remote_claw_command(
                config,
                prompt_rel=prompt_rel,
                output_rel=output_rel,
                session_ref=bridge_session_ref,
            )
        else:
            raise ValueError(f"当前智能体暂不支持远程执行：{config['agent_type']}")

        result = remote_terminal_result(
            server_entry,
            path=workspace_path,
            command=command,
            timeout_sec=max(10, timeout_sec),
        )
        if (
            result.get("exit_code") != 0
            and self._looks_like_remote_command_missing(result, str(config.get("command") or ""))
        ):
            login_script = f"cd {shlex.quote(remote_workspace_path)} && {command}"
            login_command = f"bash -lc {shlex.quote(login_script)}"
            retry_result = remote_terminal_result(
                server_entry,
                path=workspace_path,
                command=login_command,
                timeout_sec=max(10, timeout_sec),
            )
            if retry_result.get("exit_code") == 0:
                result = retry_result
            elif self._looks_like_remote_command_missing(retry_result, str(config.get("command") or "")):
                missing_command = clean_text(config.get("command")) or clean_text(config.get("label")) or "CLI"
                server_name = clean_text(server_entry.get("label")) or clean_text(server_entry.get("id")) or "远程服务器"
                raise RemoteCliCommandMissingError(
                    f"{server_name} 上未找到命令 `{missing_command}`。"
                    "请先在该 SSH 服务器安装并配置该 CLI，或将该智能体执行模式改为 local。"
                )
            else:
                result = retry_result
        duration_ms = int((time.perf_counter() - started_at) * 1000)

        output_payload = None
        try:
            output_payload = remote_read_file(
                server_entry,
                workspace_path,
                output_rel,
                max_chars=_MAX_OUTPUT_CHARS,
            )
        except Exception:
            output_payload = None

        content = ""
        parsed = None
        if config["agent_type"] == "codex":
            content = str((output_payload or {}).get("content") or "").strip()
        else:
            parsed = _parse_json_object(str((output_payload or {}).get("content") or ""))
            if config["agent_type"] == "claw":
                content = str((parsed or {}).get("message") or (parsed or {}).get("result") or "").strip()
            else:
                content = str((parsed or {}).get("result") or "").strip()

        if result.get("exit_code") != 0:
            detail = _trim_text(result.get("stderr") or result.get("stdout") or "")
            raise RuntimeError(detail or "远程 CLI 执行失败")
        if not content:
            detail = _trim_text((output_payload or {}).get("content") or result.get("stdout") or "")
            raise RuntimeError(detail or "CLI 没有返回有效结果")

        return {
            "config_id": config["id"],
            "agent_type": config["agent_type"],
            "label": config["label"],
            "command": config["command"],
            "command_path": config.get("command_path"),
            "provider": config.get("provider"),
            "base_url": config.get("base_url"),
            "default_model": config.get("default_model"),
            "workspace_path": remote_workspace_path,
            "workspace_server_id": server_entry.get("id") or bound_server_id,
            "execution_mode": "ssh",
            "duration_ms": duration_ms,
            "exit_code": int(result.get("exit_code") or 0),
            "success": True,
            "content": content,
            "stdout": _trim_text(result.get("stdout")),
            "stderr": _trim_text(result.get("stderr")),
            "parsed": parsed,
            "session_ref": bridge_session_ref if config["agent_type"] == "claw" else None,
            "session_path": (
                str((parsed or {}).get("session_path") or "").strip() or None
                if config["agent_type"] == "claw"
                else None
            ),
            "claw_settings_path": remote_settings_path if config["agent_type"] == "claw" else None,
        }

    def _resolve_local_fallback_workspace(self, workspace_path: str | None) -> str:
        candidate_raw = clean_text(workspace_path)
        if candidate_raw:
            candidate = Path(candidate_raw).expanduser()
            if candidate.exists() and candidate.is_dir():
                return str(candidate)
        return str(Path.cwd().resolve())

    def _looks_like_remote_transport_error(self, exc: Exception) -> bool:
        detail = str(exc or "").strip().lower()
        if not detail:
            return False
        markers = (
            "ssh 连接",
            "ssh connection",
            "ssh 认证",
            "ssh authentication",
            "ssh protocol",
            "protocol banner",
            "error reading ssh protocol banner",
            "workspaceaccesserror",
            "no valid connections",
            "unable to connect",
            "connection reset",
            "connection refused",
            "connection aborted",
            "connection timed out",
            "connection timeout",
            "timed out",
            "远程命令执行超时",
            "远程命令执行失败",
            "无法解析 ssh 主机",
        )
        return any(marker in detail for marker in markers)

    def _looks_like_local_workspace_error(self, exc: Exception) -> bool:
        detail = str(exc or "").strip()
        if not detail:
            return False
        return "本地工作区不存在" in detail or "本地工作区不是目录" in detail

    def _looks_like_remote_command_missing(self, result: dict[str, Any], command: str) -> bool:
        detail = f"{result.get('stderr') or ''}\n{result.get('stdout') or ''}".strip().lower()
        if "command not found" not in detail:
            return False
        normalized_command = clean_text(command).lower()
        if not normalized_command:
            return True
        return normalized_command in detail

    def _run_local_codex(
        self,
        config: dict[str, Any],
        *,
        prompt: str,
        workspace_dir: Path,
        timeout_sec: int,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="researchos-codex-") as temp_dir:
            output_path = Path(temp_dir) / "last-message.txt"
            command = self._build_local_command_prefix(config) + [
                "exec",
                "--skip-git-repo-check",
                "-C",
                str(workspace_dir),
                "--sandbox",
                "workspace-write",
                "--full-auto",
            ]
            if clean_text(config.get("default_model")):
                command.extend(["-m", str(config["default_model"])])
            if clean_text(config.get("provider")):
                command.extend(["-c", f'model_provider="{config["provider"]}"'])
            if clean_text(config.get("provider")) and clean_text(config.get("base_url")):
                provider = str(config["provider"])
                base_url = str(config["base_url"]).replace("\\", "\\\\").replace('"', '\\"')
                command.extend(["-c", f'model_providers.{provider}.base_url="{base_url}"'])
            command.extend(["-o", str(output_path), "-"])

            env = _ensure_local_cli_env(os.environ.copy())
            if clean_text(config.get("api_key")):
                env["OPENAI_API_KEY"] = str(config["api_key"])
            if clean_text(config.get("base_url")):
                env.setdefault("OPENAI_BASE_URL", str(config["base_url"]))

            started_at = time.perf_counter()
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                cwd=str(workspace_dir),
                env=env,
                timeout=max(10, timeout_sec),
                check=False,
            )
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            content = (
                output_path.read_text(encoding="utf-8", errors="replace").strip()
                if output_path.exists()
                else ""
            )

        if result.returncode != 0:
            detail = _trim_text(result.stderr or result.stdout)
            raise RuntimeError(detail or "Codex 执行失败")
        if not content:
            detail = _trim_text(result.stdout or result.stderr)
            raise RuntimeError(detail or "Codex 没有返回有效结果")

        return {
            "config_id": config["id"],
            "agent_type": config["agent_type"],
            "label": config["label"],
            "command": config["command"],
            "command_path": config.get("command_path"),
            "provider": config.get("provider"),
            "base_url": config.get("base_url"),
            "default_model": config.get("default_model"),
            "workspace_path": str(workspace_dir),
            "execution_mode": "local",
            "duration_ms": duration_ms,
            "exit_code": int(result.returncode),
            "success": True,
            "content": content,
            "stdout": _trim_text(result.stdout),
            "stderr": _trim_text(result.stderr),
            "parsed": None,
        }

    def _run_local_claude(
        self,
        config: dict[str, Any],
        *,
        prompt: str,
        workspace_dir: Path,
        timeout_sec: int,
    ) -> dict[str, Any]:
        command = self._build_local_command_prefix(config) + [
            "-p",
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
        ]
        if clean_text(config.get("default_model")):
            command.extend(["--model", str(config["default_model"])])

        env = _ensure_local_cli_env(os.environ.copy())
        if clean_text(config.get("api_key")):
            env["ANTHROPIC_AUTH_TOKEN"] = str(config["api_key"])
            env.setdefault("ANTHROPIC_API_KEY", str(config["api_key"]))
        if clean_text(config.get("base_url")):
            env["ANTHROPIC_BASE_URL"] = str(config["base_url"])
        if clean_text(config.get("default_model")):
            env["ANTHROPIC_MODEL"] = str(config["default_model"])

        started_at = time.perf_counter()
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(workspace_dir),
            env=env,
            timeout=max(10, timeout_sec),
            check=False,
        )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        parsed = _parse_json_object(result.stdout)
        content = str((parsed or {}).get("result") or "").strip()

        if result.returncode != 0:
            detail = _trim_text(result.stderr or result.stdout)
            raise RuntimeError(detail or "Claude Code 执行失败")
        if not content:
            detail = _trim_text(result.stdout or result.stderr)
            raise RuntimeError(detail or "Claude Code 没有返回有效结果")

        return {
            "config_id": config["id"],
            "agent_type": config["agent_type"],
            "label": config["label"],
            "command": config["command"],
            "command_path": config.get("command_path"),
            "provider": config.get("provider"),
            "base_url": config.get("base_url"),
            "default_model": config.get("default_model"),
            "workspace_path": str(workspace_dir),
            "execution_mode": "local",
            "duration_ms": duration_ms,
            "exit_code": int(result.returncode),
            "success": True,
            "content": content,
            "stdout": _trim_text(result.stdout),
            "stderr": _trim_text(result.stderr),
            "parsed": parsed,
        }

    def _run_local_claw(
        self,
        config: dict[str, Any],
        *,
        prompt: str,
        workspace_dir: Path,
        timeout_sec: int,
        session_id: str | None,
        requested_workspace_path: str,
        requested_workspace_server_id: str | None = None,
    ) -> dict[str, Any]:
        executable = clean_text(config.get("command_path")) or clean_text(config.get("command"))
        binary_path = Path(executable).expanduser() if executable else None
        if binary_path is None or not binary_path.exists():
            binary_path = _ensure_claw_binary(timeout_sec=max(timeout_sec, 1800))

        settings_path = _ensure_claw_workspace_settings(workspace_dir)
        bridge_session_ref = _slugify(f"researchos-{session_id or workspace_dir.name}")
        command = [
            str(binary_path),
            "--output-format",
            "json",
        ]
        for chunk in _chunk_items(bridge_qualified_tool_names(), 24):
            command.extend(["--allowedTools", ",".join(chunk)])
        if clean_text(config.get("default_model")):
            command.extend(["--model", str(config["default_model"])])
        command.extend(["bridge-turn", bridge_session_ref])

        env = _ensure_local_cli_env(os.environ.copy())
        for key in (
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "XAI_API_KEY",
            "XAI_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL",
        ):
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
        env[RESEARCHOS_CONTEXT_SESSION_ID_ENV] = clean_text(session_id) or bridge_session_ref
        env[RESEARCHOS_CONTEXT_MODE_ENV] = _session_mode_for_claw(session_id)
        env[RESEARCHOS_CONTEXT_WORKSPACE_PATH_ENV] = clean_text(requested_workspace_path) or str(workspace_dir)
        if clean_text(requested_workspace_server_id):
            env[RESEARCHOS_CONTEXT_WORKSPACE_SERVER_ID_ENV] = str(requested_workspace_server_id)
        else:
            env.pop(RESEARCHOS_CONTEXT_WORKSPACE_SERVER_ID_ENV, None)

        started_at = time.perf_counter()
        result = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(workspace_dir),
            env=env,
            timeout=max(10, timeout_sec),
            check=False,
        )
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        parsed = _parse_json_object(result.stdout)
        content = str((parsed or {}).get("message") or (parsed or {}).get("result") or "").strip()

        if result.returncode != 0:
            detail = _trim_text(result.stderr or result.stdout)
            raise RuntimeError(detail or "Claw 执行失败")
        if not content:
            detail = _trim_text(result.stdout or result.stderr)
            raise RuntimeError(detail or "Claw 没有返回有效结果")

        return {
            "config_id": config["id"],
            "agent_type": config["agent_type"],
            "label": config["label"],
            "command": config["command"],
            "command_path": str(binary_path),
            "provider": config.get("provider"),
            "base_url": config.get("base_url"),
            "default_model": config.get("default_model"),
            "workspace_path": clean_text(requested_workspace_path) or str(workspace_dir),
            "workspace_server_id": clean_text(requested_workspace_server_id) or None,
            "execution_mode": (
                "ssh"
                if clean_text(requested_workspace_server_id)
                and clean_text(requested_workspace_server_id).lower() != "local"
                else "local"
            ),
            "duration_ms": duration_ms,
            "exit_code": int(result.returncode),
            "success": True,
            "content": content,
            "stdout": _trim_text(result.stdout),
            "stderr": _trim_text(result.stderr),
            "parsed": parsed,
            "session_ref": bridge_session_ref,
            "session_path": str((parsed or {}).get("session_path") or "").strip() or None,
            "claw_settings_path": str(settings_path),
        }

    def _build_remote_codex_command(self, config: dict[str, Any], *, prompt_rel: str, output_rel: str) -> str:
        env_parts: list[str] = []
        if clean_text(config.get("api_key")):
            env_parts.append(f"OPENAI_API_KEY={shlex.quote(str(config['api_key']))}")
        if clean_text(config.get("base_url")):
            env_parts.append(f"OPENAI_BASE_URL={shlex.quote(str(config['base_url']))}")

        command_parts = self._build_remote_command_prefix(config) + [
            "exec",
            "--skip-git-repo-check",
            "-C",
            ".",
            "--sandbox",
            "workspace-write",
            "--full-auto",
        ]
        if clean_text(config.get("default_model")):
            command_parts.extend(["-m", shlex.quote(str(config["default_model"]))])
        if clean_text(config.get("provider")):
            provider = str(config["provider"])
            command_parts.extend(["-c", shlex.quote(f'model_provider="{provider}"')])
        if clean_text(config.get("provider")) and clean_text(config.get("base_url")):
            provider = str(config["provider"])
            base_url = str(config["base_url"]).replace("\\", "\\\\").replace('"', '\\"')
            command_parts.extend(
                ["-c", shlex.quote(f'model_providers.{provider}.base_url="{base_url}"')]
            )
        command_parts.extend(["-o", shlex.quote(output_rel), "-"])
        return f"{' '.join(env_parts + command_parts)} < {shlex.quote(prompt_rel)}"

    def _build_remote_claude_command(self, config: dict[str, Any], *, prompt_rel: str, output_rel: str) -> str:
        env_parts: list[str] = []
        if clean_text(config.get("api_key")):
            env_parts.append(f"ANTHROPIC_AUTH_TOKEN={shlex.quote(str(config['api_key']))}")
            env_parts.append(f"ANTHROPIC_API_KEY={shlex.quote(str(config['api_key']))}")
        if clean_text(config.get("base_url")):
            env_parts.append(f"ANTHROPIC_BASE_URL={shlex.quote(str(config['base_url']))}")
        if clean_text(config.get("default_model")):
            env_parts.append(f"ANTHROPIC_MODEL={shlex.quote(str(config['default_model']))}")

        command_parts = self._build_remote_command_prefix(config) + [
            "-p",
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
        ]
        if clean_text(config.get("default_model")):
            command_parts.extend(["--model", shlex.quote(str(config["default_model"]))])
        return f"{' '.join(env_parts + command_parts)} < {shlex.quote(prompt_rel)} > {shlex.quote(output_rel)}"

    def _build_remote_claw_command(
        self,
        config: dict[str, Any],
        *,
        prompt_rel: str,
        output_rel: str,
        session_ref: str,
    ) -> str:
        env_parts: list[str] = []
        provider_kind = _infer_claw_provider(
            clean_text(config.get("default_model")) or None,
            clean_text(config.get("protocol")) or None,
            clean_text(config.get("provider")) or None,
            clean_text(config.get("base_url")) or None,
        )
        normalized_base_url = _normalize_claw_base_url(config.get("base_url"), provider_kind)
        if clean_text(config.get("api_key")):
            if provider_kind == "openai":
                env_parts.append(f"OPENAI_API_KEY={shlex.quote(str(config['api_key']))}")
            elif provider_kind == "xai":
                env_parts.append(f"XAI_API_KEY={shlex.quote(str(config['api_key']))}")
            else:
                env_parts.append(f"ANTHROPIC_AUTH_TOKEN={shlex.quote(str(config['api_key']))}")
                env_parts.append(f"ANTHROPIC_API_KEY={shlex.quote(str(config['api_key']))}")
        if normalized_base_url:
            if provider_kind == "openai":
                env_parts.append(f"OPENAI_BASE_URL={shlex.quote(normalized_base_url)}")
            elif provider_kind == "xai":
                env_parts.append(f"XAI_BASE_URL={shlex.quote(normalized_base_url)}")
            else:
                env_parts.append(f"ANTHROPIC_BASE_URL={shlex.quote(normalized_base_url)}")

        command_parts = self._build_remote_command_prefix(config) + [
            "--output-format",
            "json",
        ]
        if clean_text(config.get("default_model")):
            command_parts.extend(["--model", shlex.quote(str(config["default_model"]))])
        command_parts.extend(["bridge-turn", shlex.quote(session_ref)])
        return f"{' '.join(env_parts + command_parts)} < {shlex.quote(prompt_rel)} > {shlex.quote(output_rel)}"

    def _build_local_command_prefix(self, config: dict[str, Any]) -> list[str]:
        executable = clean_text(config.get("command_path")) or clean_text(config.get("command"))
        if not executable:
            raise ValueError(f"{config.get('label') or config.get('agent_type') or 'CLI'} 未配置可执行命令")
        return [executable, *[str(item) for item in (config.get("args") or []) if str(item).strip()]]

    def _build_remote_command_prefix(self, config: dict[str, Any]) -> list[str]:
        executable = clean_text(config.get("command")) or clean_text(config.get("command_path"))
        if not executable:
            raise ValueError(f"{config.get('label') or config.get('agent_type') or 'CLI'} 未配置可执行命令")
        parts = [shlex.quote(executable)]
        parts.extend(
            shlex.quote(str(item))
            for item in (config.get("args") or [])
            if str(item).strip()
        )
        return parts

    def _build_chat_capability(self, config: dict[str, Any]) -> dict[str, Any]:
        agent_type = clean_text(config.get("agent_type")) or "agent"
        label = clean_text(config.get("label")) or agent_type
        kind = clean_text(config.get("kind")) or "cli"
        installed = bool(config.get("installed"))

        if agent_type in _SUPPORTED_EXECUTION_AGENT_TYPES:
            if not installed:
                if agent_type == "claw":
                    if _looks_like_packaged_claw_runtime():
                        blocked_reason = (
                            f"未检测到 {label} 的可执行文件。"
                            "当前桌面包没有携带可用的 claw 二进制，"
                            f"请确认 `{_DEFAULT_CLAW_BINARY_ENV}` 已指向可执行文件，"
                            "或重新安装包含 claw 的桌面构建。"
                        )
                    else:
                        blocked_reason = (
                            f"未检测到 {label} 的可执行文件。"
                            f"请先在 `{_claw_rust_root()}` 下执行 `cargo build --workspace`，"
                            "或确认 claw 副本路径配置正确。"
                        )
                    return {
                        "chat_supported": True,
                        "chat_ready": False,
                        "chat_status": "missing_command",
                        "chat_status_label": "需构建",
                        "chat_blocked_reason": blocked_reason,
                    }
                return {
                    "chat_supported": True,
                    "chat_ready": False,
                    "chat_status": "missing_command",
                    "chat_status_label": "命令缺失",
                    "chat_blocked_reason": f"未检测到 {label} 的本地可执行命令，暂时无法作为聊天后端。",
                }
            return {
                "chat_supported": True,
                "chat_ready": True,
                "chat_status": "ready",
                "chat_status_label": "可聊天",
                "chat_blocked_reason": None,
            }

        if kind == "acp":
            summary = get_acp_registry_service().get_backend_summary()
            return {
                "chat_supported": bool(summary.get("chat_supported", True)),
                "chat_ready": bool(summary.get("chat_ready")),
                "chat_status": summary.get("chat_status") or "requires_service",
                "chat_status_label": summary.get("chat_status_label") or "需 ACP 服务",
                "chat_blocked_reason": summary.get("chat_blocked_reason"),
            }

        if installed:
            return {
                "chat_supported": False,
                "chat_ready": False,
                "chat_status": "detection_only",
                "chat_status_label": "仅检测",
                "chat_blocked_reason": (
                    f"{label} 已检测到本地命令，但当前版本还没有接通真实聊天执行链路。"
                ),
            }

        return {
            "chat_supported": False,
            "chat_ready": False,
            "chat_status": "detection_only",
            "chat_status_label": "仅检测",
            "chat_blocked_reason": (
                f"{label} 当前只支持探测与配置管理，而且还没有检测到可用命令。"
            ),
        }

    def _detect_message(self, agent_type: str, config: dict[str, Any]) -> str | None:
        if agent_type == "custom_acp":
            return get_acp_registry_service().get_backend_summary().get("chat_blocked_reason")
        capability = self._build_chat_capability(config)
        if not capability["chat_ready"]:
            return capability["chat_blocked_reason"]
        if agent_type == "codex" and not config.get("config_source"):
            return "已检测到 Codex 命令，但未读取到本地 config.toml。"
        return None

    def _serialize_config(self, config: dict[str, Any]) -> dict[str, Any]:
        resolved = self._resolve_config(config)
        capability = self._build_chat_capability(resolved)
        acp_summary = get_acp_registry_service().get_backend_summary() if resolved["agent_type"] == "custom_acp" else {}
        return {
            "id": resolved["id"],
            "agent_type": resolved["agent_type"],
            "label": resolved["label"],
            "kind": resolved.get("kind") or "cli",
            "description": resolved.get("description") or "",
            "enabled": bool(resolved.get("enabled", True)),
            "command": resolved.get("command"),
            "args": list(resolved.get("args") or []),
            "provider": resolved.get("provider"),
            "protocol": resolved.get("protocol"),
            "base_url": resolved.get("base_url"),
            "default_model": resolved.get("default_model"),
            "workspace_server_id": resolved.get("workspace_server_id"),
            "execution_mode": resolved.get("execution_mode") or _DEFAULT_EXECUTION_MODE,
            "metadata": dict(resolved.get("metadata") or {}),
            "installed": bool(resolved.get("installed")),
            "chat_supported": capability["chat_supported"],
            "chat_ready": capability["chat_ready"],
            "chat_status": capability["chat_status"],
            "chat_status_label": capability["chat_status_label"],
            "chat_blocked_reason": capability["chat_blocked_reason"],
            "acp_server_name": acp_summary.get("default_server"),
            "acp_server_label": acp_summary.get("default_server_label"),
            "acp_transport": acp_summary.get("default_transport"),
            "acp_connected": acp_summary.get("default_connected"),
            "command_path": resolved.get("command_path"),
            "config_source": resolved.get("config_source"),
            "has_api_key": bool(resolved.get("api_key")),
            "api_key_masked": mask_secret(resolved.get("api_key")),
            "created_at": resolved.get("created_at"),
            "updated_at": resolved.get("updated_at"),
        }

    def _resolve_config(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_config(config)
        detected = self._detect_local_defaults(normalized["agent_type"])
        active_llm_defaults = (
            _load_active_llm_defaults() if normalized["agent_type"] == "claw" else {}
        )
        command_name = (
            clean_text(normalized.get("command"))
            or clean_text(detected.get("command"))
            or (_AGENT_BINARY_CANDIDATES.get(normalized["agent_type"]) or [""])[0]
        )
        command_path = self._resolve_command_path(normalized["agent_type"], command_name)
        claw_bootstrap_available = normalized["agent_type"] == "claw" and _claw_bootstrap_available()
        template = self._template_map.get(normalized["agent_type"], {})
        provider = (
            clean_text(normalized.get("provider"))
            or clean_text(active_llm_defaults.get("provider"))
            or clean_text(detected.get("provider"))
            or None
        )
        protocol = (
            clean_text(normalized.get("protocol"))
            or clean_text(active_llm_defaults.get("protocol"))
            or clean_text(detected.get("protocol"))
            or resolve_provider_protocol(
                provider,
                clean_text(normalized.get("base_url"))
                or clean_text(active_llm_defaults.get("base_url"))
                or clean_text(detected.get("base_url"))
                or None,
            )
            or None
        )
        base_url = (
            clean_text(normalized.get("base_url"))
            or clean_text(active_llm_defaults.get("base_url"))
            or clean_text(detected.get("base_url"))
            or None
        )
        api_key = (
            clean_text(normalized.get("api_key"))
            or clean_text(active_llm_defaults.get("api_key"))
            or clean_text(detected.get("api_key"))
            or None
        )
        default_model = (
            clean_text(normalized.get("default_model"))
            or clean_text(active_llm_defaults.get("default_model"))
            or clean_text(detected.get("default_model"))
            or None
        )
        config_source = (
            clean_text(active_llm_defaults.get("config_source"))
            if normalized["agent_type"] == "claw" and not clean_text(normalized.get("provider"))
            else None
        ) or clean_text(detected.get("config_source")) or None
        return {
            **normalized,
            "label": normalized.get("label") or template.get("label") or normalized["agent_type"],
            "kind": template.get("kind") or "cli",
            "description": template.get("description") or "",
            "command": command_name,
            "command_path": command_path or (str(_preferred_claw_binary_candidate()) if claw_bootstrap_available else None),
            "installed": bool(command_path) or claw_bootstrap_available,
            "provider": provider,
            "protocol": protocol,
            "base_url": base_url,
            "api_key": api_key,
            "default_model": default_model,
            "config_source": config_source,
        }

    def _detect_local_defaults(self, agent_type: str) -> dict[str, Any]:
        if agent_type == "codex":
            return self._detect_codex_defaults()
        if agent_type == "claude_code":
            return self._detect_claude_defaults()
        if agent_type == "claw":
            return self._detect_claw_defaults()
        return {}

    def _detect_codex_defaults(self) -> dict[str, Any]:
        path = Path.home() / ".codex" / "config.toml"
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        provider = clean_text(data.get("model_provider")) or None
        provider_details = data.get("model_providers") if isinstance(data.get("model_providers"), dict) else {}
        provider_config = provider_details.get(provider) if isinstance(provider_details, dict) and provider else {}
        return {
            "command": "codex",
            "provider": provider,
            "base_url": clean_text((provider_config or {}).get("base_url")) or None,
            "default_model": clean_text(data.get("model")) or None,
            "config_source": str(path) if path.exists() else None,
        }

    def _detect_claude_defaults(self) -> dict[str, Any]:
        path = Path.home() / ".claude" / "settings.json"
        payload = _load_json_file(path)
        env = payload.get("env") if isinstance(payload, dict) else {}
        if not isinstance(env, dict):
            env = {}
        api_key = clean_text(env.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN")) or None
        return {
            "command": "claude",
            "base_url": clean_text(env.get("ANTHROPIC_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")) or None,
            "default_model": clean_text(env.get("ANTHROPIC_MODEL") or os.environ.get("ANTHROPIC_MODEL")) or None,
            "api_key": api_key,
            "config_source": str(path) if path.exists() else None,
        }

    def _detect_claw_defaults(self) -> dict[str, Any]:
        claude_defaults = self._detect_claude_defaults()
        runtime_root = _claw_runtime_root()
        default_model = clean_text(claude_defaults.get("default_model")) or "sonnet"
        base_url = claude_defaults.get("base_url")
        return {
            "command": str(_preferred_claw_binary_candidate()),
            "provider": _infer_claw_provider(default_model, None, None, clean_text(base_url) or None),
            "protocol": resolve_provider_protocol(None, clean_text(base_url) or None) or "anthropic",
            "base_url": base_url,
            "default_model": default_model,
            "api_key": claude_defaults.get("api_key"),
            "config_source": str(runtime_root) if runtime_root.exists() else claude_defaults.get("config_source"),
        }

    def _resolve_command_path(self, agent_type: str, command_name: str) -> str | None:
        explicit = clean_text(command_name)
        if agent_type == "claw":
            if explicit:
                candidate_path = Path(explicit).expanduser()
                if candidate_path.exists():
                    return str(candidate_path.resolve())
            bundled = next((item for item in _claw_binary_candidates() if item.exists()), None)
            if bundled is not None:
                return str(bundled.resolve())
            if explicit:
                path = shutil.which(explicit)
                if path:
                    return str(Path(path).resolve())
            return None
        if explicit:
            path = shutil.which(explicit)
            if path:
                return str(Path(path).resolve())
            candidate_path = Path(explicit).expanduser()
            if candidate_path.exists():
                return str(candidate_path.resolve())
        for candidate in _AGENT_BINARY_CANDIDATES.get(agent_type, []):
            path = shutil.which(candidate)
            if path:
                return str(Path(path).resolve())
        return None

    def _normalize_execution_mode(self, value: object) -> str:
        mode = clean_text(value or _DEFAULT_EXECUTION_MODE).lower()
        if mode not in {"auto", "local", "ssh"}:
            return _DEFAULT_EXECUTION_MODE
        return mode

    def _merge_secret(self, incoming: object, existing: object) -> str | None:
        if incoming is None:
            return clean_text(existing) or None
        value = clean_text(incoming)
        if not value:
            return clean_text(existing) or None
        return value

    def _normalize_config(self, raw: dict[str, Any]) -> dict[str, Any]:
        agent_type = clean_text(raw.get("agent_type") or raw.get("id"))
        if agent_type not in self._template_map:
            raise ValueError("不支持的智能体类型")
        template = self._template_map[agent_type]
        now = _iso_now()
        return {
            "id": agent_type,
            "agent_type": agent_type,
            "label": clean_text(raw.get("label")) or template.get("label") or agent_type,
            "enabled": bool(raw.get("enabled", True)),
            "command": clean_text(raw.get("command")) or (_AGENT_BINARY_CANDIDATES.get(agent_type) or [""])[0],
            "args": [str(item).strip() for item in (raw.get("args") or []) if str(item).strip()],
            "provider": clean_text(raw.get("provider")) or None,
            "base_url": clean_text(raw.get("base_url")) or None,
            "api_key": clean_text(raw.get("api_key")) or None,
            "default_model": clean_text(raw.get("default_model")) or None,
            "workspace_server_id": clean_text(raw.get("workspace_server_id")) or None,
            "execution_mode": (
                _DEFAULT_EXECUTION_MODE
                if agent_type == "claw"
                else self._normalize_execution_mode(raw.get("execution_mode"))
            ),
            "metadata": dict(raw.get("metadata") or {}),
            "created_at": clean_text(raw.get("created_at")) or now,
            "updated_at": clean_text(raw.get("updated_at")) or now,
        }

    def _config_store_path(self) -> Path:
        settings = get_settings()
        base_dir = settings.pdf_storage_root.parent.resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / _CONFIG_FILE_NAME

    def _load_configs(self) -> list[dict[str, Any]]:
        store = self._config_store_path()
        payload = _load_json_file(store)
        items = payload if isinstance(payload, list) else []
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                normalized_item = self._normalize_config(item)
            except ValueError:
                continue
            if normalized_item["id"] in seen:
                continue
            seen.add(normalized_item["id"])
            normalized.append(normalized_item)

        changed = False
        for agent_type in self._template_map:
            if agent_type in seen:
                continue
            normalized.append(self._normalize_config({"agent_type": agent_type}))
            changed = True

        normalized.sort(key=lambda item: list(self._template_map).index(item["agent_type"]))
        if changed or payload is None:
            self._save_configs(normalized)
        return normalized

    def _save_configs(self, configs: list[dict[str, Any]]) -> None:
        store = self._config_store_path()
        store.write_text(
            json.dumps(configs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _find_config(self, config_id: str) -> dict[str, Any] | None:
        config_id = _slugify(config_id)
        return next((item for item in self._load_configs() if item["id"] == config_id), None)

    def _workspace_server_store_path(self) -> Path:
        settings = get_settings()
        base_dir = settings.pdf_storage_root.parent.resolve()
        return base_dir / "assistant_workspace_servers.json"

    def _find_workspace_server_entry(self, server_id: str | None) -> dict[str, Any] | None:
        normalized_id = _slugify(server_id or "")
        if not normalized_id:
            return None
        payload = _load_json_file(self._workspace_server_store_path())
        items = payload if isinstance(payload, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            current_id = _slugify(str(item.get("id") or item.get("label") or item.get("host") or ""))
            if current_id != normalized_id:
                continue
            return item
        return None


@lru_cache(maxsize=1)
def get_cli_agent_service() -> CliAgentService:
    return CliAgentService()

