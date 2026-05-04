"""Manage a local opencode sidecar runtime for the ResearchOS assistant."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from packages.config import get_settings
from packages.integrations.llm_provider_schema import (
    normalize_provider_name as _normalize_provider_name,
)
from packages.storage.db import session_scope
from packages.storage.repositories import LLMConfigRepository

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_REPO_PATH = _ROOT / "opencode-dev"
_LOG_DIR = _ROOT / "logs"
_RUNTIME_LOG = _LOG_DIR / "opencode-runtime.log"
_INSTALL_LOG = _LOG_DIR / "opencode-install.log"
_INSTALL_SENTINEL = _REPO_PATH / ".researchos-opencode-install.json"
_BUN_CACHE_DIR = _REPO_PATH / ".researchos-bun-cache"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 4097
_OFFICIAL_NPM_REGISTRY = "https://registry.npmjs.org"


def _runtime_support_status() -> tuple[bool, str | None]:
    if not _REPO_PATH.exists():
        return False, f"当前环境未包含 opencode-dev：{_REPO_PATH}，MCP / opencode 运行时不可用"
    if shutil.which("bun") is None:
        return False, "当前环境未安装 bun，MCP / opencode 运行时不可用"
    return True, None


def _ensure_v1_suffix(url: str | None) -> str | None:
    if not url:
        return url
    stripped = url.strip().rstrip("/")
    if not stripped:
        return None
    if stripped.lower().endswith("/v1"):
        return stripped
    return f"{stripped}/v1"


def _infer_reasoning(model_name: str | None) -> bool:
    model = (model_name or "").lower()
    hints = (
        "gpt-5",
        "o1",
        "o3",
        "o4",
        "reason",
        "thinking",
        "r1",
        "qwq",
        "kimi",
        "deepseek",
    )
    return any(token in model for token in hints)


def _requires_custom_tls_compat(base_url: str | None) -> bool:
    if not base_url:
        return False
    try:
        parsed = urlparse(base_url)
        if parsed.scheme.lower() != "https":
            return False
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    trusted_hosts = {
        "api.openai.com",
        "api.anthropic.com",
        "open.bigmodel.cn",
    }
    return host not in trusted_hosts


def _opencode_provider_proxy_url() -> str:
    settings = get_settings()
    return f"http://127.0.0.1:{settings.api_port}/opencode/provider"


def _model_limits(model_name: str | None) -> dict[str, int]:
    model = (model_name or "").strip()
    explicit: dict[str, dict[str, int]] = {
        "gpt-5-codex": {"context": 400000, "output": 128000},
        "gpt-5.1-codex": {"context": 400000, "output": 128000},
        "gpt-5.1-codex-max": {"context": 400000, "output": 128000},
        "gpt-5.1-codex-mini": {"context": 400000, "output": 128000},
        "gpt-5.2": {"context": 400000, "output": 128000},
        "gpt-5.4": {"context": 1050000, "output": 128000},
        "gpt-5.3-codex-spark": {"context": 128000, "output": 32000},
        "gpt-5.3-codex": {"context": 400000, "output": 128000},
        "gpt-5.2-codex": {"context": 400000, "output": 128000},
        "codex-mini-latest": {"context": 200000, "output": 100000},
    }
    return explicit.get(model, {"context": 200000, "output": 32000})


def _tail_file(path: Path, limit: int = 40) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-limit:])


def _policy_to_opencode_permission(policy: dict | None) -> str | dict[str, object]:
    from packages.agent.workspace.workspace_executor import (
        DEFAULT_ASSISTANT_EXEC_POLICY,
        get_assistant_exec_policy,
    )

    current = policy or get_assistant_exec_policy()
    workspace_access = str(
        current.get("workspace_access") or DEFAULT_ASSISTANT_EXEC_POLICY["workspace_access"]
    )
    command_execution = str(
        current.get("command_execution") or DEFAULT_ASSISTANT_EXEC_POLICY["command_execution"]
    )
    approval_mode = str(
        current.get("approval_mode") or DEFAULT_ASSISTANT_EXEC_POLICY["approval_mode"]
    )
    allowed_prefixes = list(current.get("allowed_command_prefixes") or [])

    if workspace_access == "read_write" and command_execution == "full" and approval_mode == "off":
        return "allow"

    read_action = "allow" if workspace_access != "none" else "deny"
    edit_action = "deny"
    if workspace_access == "read_write":
        edit_action = "allow" if approval_mode == "off" else "ask"

    if command_execution == "deny":
        bash_permission: str | dict[str, str] = "deny"
    elif command_execution == "full":
        bash_permission = "allow" if approval_mode == "off" else "ask"
    else:
        bash_permission = {"*": "deny"}
        for prefix in allowed_prefixes:
            cleaned = " ".join(str(prefix or "").strip().split())
            if not cleaned:
                continue
            action = "allow" if approval_mode == "off" else "ask"
            bash_permission[cleaned] = action
            bash_permission[f"{cleaned} *"] = action

    external_directory_action = "deny"
    if workspace_access == "read_write":
        external_directory_action = "allow" if approval_mode == "off" else "ask"

    return {
        "read": read_action,
        "list": read_action,
        "grep": read_action,
        "glob": read_action,
        "codesearch": read_action,
        "lsp": read_action,
        "edit": edit_action,
        "bash": bash_permission,
        "external_directory": {"*": external_directory_action},
        "task": "allow",
        "skill": "allow",
        "webfetch": "allow",
        "websearch": "allow",
        "todoread": "allow",
        "todowrite": (
            "allow"
            if approval_mode == "off" and workspace_access == "read_write"
            else "ask"
            if workspace_access == "read_write"
            else "deny"
        ),
        "question": "allow",
    }


@dataclass
class OpenCodeRuntimeState:
    available: bool = False
    phase: str = "idle"
    message: str = "opencode 运行时未启动"
    url: str | None = None
    pid: int | None = None
    host: str = _DEFAULT_HOST
    port: int = _DEFAULT_PORT
    repo_path: str = str(_REPO_PATH)
    default_directory: str = str(_ROOT)
    active_provider: str | None = None
    active_model: str | None = None
    skills_paths: list[str] = field(default_factory=list)
    log_path: str = str(_RUNTIME_LOG)
    install_log_path: str = str(_INSTALL_LOG)
    last_error: str | None = None
    updated_at: float = field(default_factory=time.time)


class OpenCodeRuntimeManager:
    """Spin up and track a local opencode sidecar service."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        available, reason = _runtime_support_status()
        self._state = OpenCodeRuntimeState(
            available=available,
            phase="idle" if available else "unavailable",
            message=reason or "opencode 运行时未启动",
            default_directory=str(_ROOT),
        )

    def snapshot(self) -> dict:
        with self._lock:
            self._refresh_process_state_locked()
            return asdict(self._state)

    def start(self, *, force_restart: bool = False) -> dict:
        with self._lock:
            self._refresh_process_state_locked()
            if not self._state.available:
                return asdict(self._state)
            if force_restart:
                self._stop_process_locked()
            if self._process and self._process.poll() is None:
                self._set_state_locked(
                    phase="ready",
                    message="opencode 运行时已就绪",
                    url=self._build_url(),
                    pid=self._process.pid,
                )
                return asdict(self._state)
            if self._worker and self._worker.is_alive():
                return asdict(self._state)
            self._worker = threading.Thread(target=self._bootstrap_and_start, daemon=True)
            self._worker.start()
            return asdict(self._state)

    def stop(self) -> dict:
        with self._lock:
            self._stop_process_locked()
            self._set_state_locked(
                phase="stopped",
                message="opencode 运行时已停止",
                url=None,
                pid=None,
            )
            return asdict(self._state)

    def _build_url(self) -> str:
        return f"http://{self._state.host}:{self._state.port}"

    def _set_state_locked(self, **updates) -> None:
        for key, value in updates.items():
            setattr(self._state, key, value)
        self._state.updated_at = time.time()

    def _refresh_process_state_locked(self) -> None:
        available, reason = _runtime_support_status()
        self._state.available = available
        if not available and not self._process:
            self._set_state_locked(
                phase="unavailable",
                message=reason or "opencode 运行时当前不可用",
                url=None,
                pid=None,
            )
            return
        runtime_pid = self._discover_runtime_pid()
        if self._runtime_reachable():
            self._set_state_locked(
                phase="ready",
                message="opencode 运行时已就绪",
                url=self._build_url(),
                pid=runtime_pid,
                last_error=None,
            )
            if self._process and self._process.poll() is not None:
                self._process = None
            return
        if not self._process:
            return
        if self._process.poll() is None:
            return
        code = self._process.returncode
        if self._state.phase in {"error", "stopped"}:
            self._process = None
            return
        tail = _tail_file(_RUNTIME_LOG)
        message = f"opencode 运行时已退出（code={code}）"
        if tail:
            message = f"{message}\n{tail}"
        self._process = None
        self._set_state_locked(
            phase="error",
            message="opencode 运行时意外退出",
            url=None,
            pid=None,
            last_error=message,
        )

    def _stop_process_locked(self) -> None:
        proc = self._process
        self._process = None
        if not proc:
            external_pid = self._discover_runtime_pid()
            if external_pid:
                self._kill_pid(external_pid)
            return
        if proc.poll() is None:
            self._terminate_process(proc)

    def _terminate_process(self, proc: subprocess.Popen[str]) -> None:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        except OSError:
            logger.debug("failed to terminate opencode runtime", exc_info=True)

    def _kill_pid(self, pid: int) -> None:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            logger.debug("failed to kill external opencode runtime pid=%s", pid, exc_info=True)

    def _runtime_reachable(self) -> bool:
        try:
            with urlopen(f"{self._build_url()}/doc", timeout=1.5) as response:
                return response.status == 200
        except OSError:
            return False
        except URLError:
            return False

    def _discover_runtime_pid(self) -> int | None:
        try:
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-Command",
                    f"Get-NetTCPConnection -LocalPort {self._state.port} -State Listen -ErrorAction SilentlyContinue | "
                    "Select-Object -First 1 -ExpandProperty OwningProcess",
                ],
                cwd=_ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        text = (result.stdout or "").strip()
        if not text.isdigit():
            return None
        return int(text)

    def _bootstrap_and_start(self) -> None:
        try:
            if not _REPO_PATH.exists():
                raise RuntimeError(f"未找到 opencode-dev 目录: {_REPO_PATH}")

            self._set_runtime_metadata()
            self._ensure_dependencies()
            config = self._build_runtime_config()
            self._start_process(config)
            self._wait_until_ready()
            with self._lock:
                if self._process is None or self._process.poll() is not None:
                    raise RuntimeError("opencode 运行时未能成功启动")
                self._set_state_locked(
                    phase="ready",
                    message="opencode 原生工作台已就绪",
                    url=self._build_url(),
                    pid=self._process.pid,
                    last_error=None,
                )
        except Exception as exc:  # pragma: no cover - defensive runtime path
            logger.exception("failed to bootstrap opencode runtime: %s", exc)
            tail = _tail_file(_RUNTIME_LOG) or _tail_file(_INSTALL_LOG)
            details = str(exc)
            if tail and tail not in details:
                details = f"{details}\n{tail}"
            with self._lock:
                self._stop_process_locked()
                self._set_state_locked(
                    phase="error",
                    message="opencode 启动失败",
                    url=None,
                    pid=None,
                    last_error=details,
                )

    def _set_runtime_metadata(self) -> None:
        provider, model = self._active_model_summary()
        skills = self._skill_paths()
        with self._lock:
            self._set_state_locked(
                phase="bootstrapping",
                message="正在准备 opencode 运行环境",
                active_provider=provider,
                active_model=model,
                skills_paths=skills,
            )

    def _ensure_dependencies(self) -> None:
        node_modules = _REPO_PATH / "node_modules"
        if self._dependencies_ready(node_modules):
            return

        if node_modules.exists():
            self._safe_remove_path(node_modules)
        if _BUN_CACHE_DIR.exists():
            self._safe_remove_path(_BUN_CACHE_DIR)
        if _INSTALL_SENTINEL.exists():
            self._safe_remove_path(_INSTALL_SENTINEL)

        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _BUN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._set_state_locked(
                phase="bootstrapping",
                message="正在安装 opencode 依赖（官方源，首次可能需要几分钟）",
            )
        with _INSTALL_LOG.open("a", encoding="utf-8") as install_log:
            install_log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} bun install ===\n")
            install_log.flush()
            env = os.environ.copy()
            env["npm_config_registry"] = _OFFICIAL_NPM_REGISTRY
            env["NPM_CONFIG_REGISTRY"] = _OFFICIAL_NPM_REGISTRY
            env["BUN_CONFIG_REGISTRY"] = _OFFICIAL_NPM_REGISTRY
            result = subprocess.run(
                [
                    "bun",
                    "install",
                    "--registry",
                    _OFFICIAL_NPM_REGISTRY,
                    "--backend",
                    "copyfile",
                    "--cache-dir",
                    str(_BUN_CACHE_DIR),
                    "--force",
                    "--no-cache",
                    "--no-progress",
                ],
                cwd=_REPO_PATH,
                stdout=install_log,
                stderr=install_log,
                text=True,
                check=False,
                env=env,
            )
        if result.returncode != 0:
            raise RuntimeError(f"bun install 失败，详情见 {_INSTALL_LOG}")
        _INSTALL_SENTINEL.write_text(
            json.dumps(
                {
                    "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "registry": _OFFICIAL_NPM_REGISTRY,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _dependencies_ready(self, node_modules: Path) -> bool:
        if not node_modules.exists() or not _INSTALL_SENTINEL.exists():
            return False
        required_paths = [
            _REPO_PATH / "node_modules" / "@opencode-ai",
            _REPO_PATH / "node_modules" / "typescript",
            _REPO_PATH / "packages" / "opencode" / "src" / "index.ts",
            _REPO_PATH / "packages" / "app" / "package.json",
        ]
        return all(path.exists() for path in required_paths)

    def _safe_remove_path(self, target: Path) -> None:
        if not target.exists():
            return
        try:
            if target.is_file():
                target.unlink(missing_ok=True)
                return
            shutil.rmtree(target, ignore_errors=False)
        except OSError:
            logger.warning(
                "failed to remove path during opencode bootstrap: %s", target, exc_info=True
            )

    def _active_model_summary(self) -> tuple[str | None, str | None]:
        with session_scope() as session:
            active = LLMConfigRepository(session).get_active()
            if active:
                return active.provider, active.model_deep or active.model_fallback
        fallback = self._settings_llm_record()
        if not fallback:
            return None, None
        return fallback.provider, fallback.model_deep or fallback.model_fallback

    def _load_active_llm_record(self):
        with session_scope() as session:
            active = LLMConfigRepository(session).get_active()
            if active:
                return SimpleNamespace(
                    id=active.id,
                    name=active.name,
                    provider=active.provider,
                    api_key=active.api_key,
                    api_base_url=active.api_base_url,
                    model_skim=active.model_skim,
                    model_deep=active.model_deep,
                    model_fallback=active.model_fallback,
                )
        fallback = self._settings_llm_record()
        if fallback:
            return fallback
        raise RuntimeError(
            "当前没有激活的 LLM 配置，且 .env 中也没有可用默认模型，无法启动 opencode"
        )

    def _settings_llm_record(self):
        settings = get_settings()

        opencode_provider = _normalize_provider_name(
            settings.opencode_provider or settings.llm_provider
        )
        opencode_model = settings.opencode_model or None
        opencode_small_model = settings.opencode_small_model or None
        opencode_api_key = settings.opencode_api_key or None
        opencode_base_url = settings.opencode_base_url or None
        if opencode_provider and opencode_model and opencode_api_key:
            return SimpleNamespace(
                id="opencode-env",
                name="OpenCode 专用配置",
                provider=opencode_provider,
                api_key=opencode_api_key,
                api_base_url=opencode_base_url or settings.openai_base_url,
                model_skim=opencode_small_model or opencode_model,
                model_deep=opencode_model,
                model_fallback=opencode_small_model or opencode_model,
            )

        provider = _normalize_provider_name(settings.llm_provider)
        model_deep = (
            settings.llm_model_deep or settings.llm_model_fallback or settings.llm_model_skim
        )
        if not provider or not model_deep:
            return None

        api_key = None
        api_base_url = None
        if provider == "anthropic":
            api_key = settings.anthropic_api_key
        elif provider == "zhipu":
            api_key = settings.zhipu_api_key
        else:
            provider = "openai"
            api_key = settings.openai_api_key
            api_base_url = settings.openai_base_url

        if not api_key:
            return None

        return SimpleNamespace(
            id="env-default",
            name="环境变量默认配置",
            provider=provider,
            api_key=api_key,
            api_base_url=api_base_url,
            model_skim=settings.llm_model_skim,
            model_deep=settings.llm_model_deep,
            model_fallback=settings.llm_model_fallback,
        )

    def _skill_paths(self) -> list[str]:
        home = Path.home()
        candidates = [
            home / ".codex" / "skills",
            home / ".agents" / "skills",
        ]
        result: list[str] = []
        for path in candidates:
            if path.exists():
                result.append(str(path))
        return result

    def _build_runtime_config(self) -> dict:
        active = self._load_active_llm_record()
        provider = _normalize_provider_name(active.provider)
        model_name = active.model_deep or active.model_fallback or active.model_skim
        small_model_name = active.model_fallback or active.model_skim or model_name
        if not model_name:
            raise RuntimeError("激活的 LLM 配置缺少可用模型，无法启动 opencode")

        base_url = (active.api_base_url or "").strip() or None
        npm = "@ai-sdk/openai-compatible"
        provider_api = _ensure_v1_suffix(base_url)
        provider_options: dict[str, object] = {
            "apiKey": active.api_key,
            "timeout": False,
            "chunkTimeout": 300000,
        }

        if provider == "anthropic":
            npm = "@ai-sdk/anthropic"
            provider_api = base_url
            if base_url:
                provider_options["baseURL"] = base_url.rstrip("/")
        elif provider == "openai" and provider_api and "api.openai.com" in provider_api.lower():
            npm = "@ai-sdk/openai"
            provider_options["baseURL"] = provider_api
        else:
            if provider_api:
                provider_options["baseURL"] = provider_api

        if npm == "@ai-sdk/openai-compatible" and provider_api:
            provider_api = _opencode_provider_proxy_url()
            provider_options["apiKey"] = "researchos-local-proxy"
            provider_options["baseURL"] = provider_api

        def build_model_config(name: str) -> dict[str, object]:
            limits = _model_limits(name)
            config: dict[str, object] = {
                "name": name,
                "tool_call": True,
                "reasoning": _infer_reasoning(name),
                "attachment": True,
                "modalities": {
                    "input": ["text", "image", "pdf"],
                    "output": ["text"],
                },
                "limit": limits,
            }
            if "codex" in name.lower():
                config["options"] = {
                    "store": False,
                }
            return config

        provider_models = {
            model_name: build_model_config(model_name),
        }
        if small_model_name and small_model_name not in provider_models:
            provider_models[small_model_name] = build_model_config(small_model_name)

        settings = get_settings()
        cors_origins = [
            origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()
        ]
        permission = _policy_to_opencode_permission(None)

        return {
            "$schema": "https://opencode.ai/config.json",
            "server": {
                "port": self._state.port,
                "hostname": self._state.host,
                "cors": cors_origins,
            },
            "default_agent": "build",
            "permission": permission,
            "model": f"researchos/{model_name}",
            "small_model": f"researchos/{small_model_name}",
            "skills": {
                "paths": self._skill_paths(),
            },
            "provider": {
                "researchos": {
                    "name": f"ResearchOS ({active.name})",
                    "npm": npm,
                    "api": provider_api,
                    "options": provider_options,
                    "models": provider_models,
                },
            },
            "mcp": {
                "researchos": {
                    "type": "local",
                    "command": [
                        sys.executable,
                        str(_ROOT / "scripts" / "researchos_mcp_server.py"),
                    ],
                    "environment": {
                        "PYTHONPATH": str(_ROOT),
                        "PYTHONUTF8": "1",
                    },
                    "enabled": True,
                    "timeout": 30000,
                },
            },
        }

    def _start_process(self, config: dict) -> None:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._set_state_locked(
                phase="starting",
                message="正在启动 opencode 服务",
                url=None,
                pid=None,
                last_error=None,
            )

        runtime_log = _RUNTIME_LOG.open("a", encoding="utf-8")
        runtime_log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} opencode start ===\n")
        runtime_log.flush()

        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, ensure_ascii=False)
        provider_api = config.get("provider", {}).get("researchos", {}).get("api")
        if isinstance(provider_api, str) and _requires_custom_tls_compat(provider_api):
            # Custom OpenAI-compatible endpoints on Windows/Bun often fail due to
            # incomplete certificate chains. Keep this scoped to the sidecar only.
            env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"

        process = subprocess.Popen(
            [
                "bun",
                "run",
                "--cwd",
                "packages/opencode",
                "--conditions=browser",
                "src/index.ts",
                "serve",
                f"--hostname={self._state.host}",
                f"--port={self._state.port}",
            ],
            cwd=_REPO_PATH,
            stdout=runtime_log,
            stderr=runtime_log,
            text=True,
            env=env,
        )

        with self._lock:
            self._process = process
            self._set_state_locked(pid=process.pid)

    def _wait_until_ready(self, timeout_seconds: float = 90.0) -> None:
        deadline = time.time() + timeout_seconds
        url = f"{self._build_url()}/doc"
        while time.time() < deadline:
            with self._lock:
                proc = self._process
                if proc is None:
                    raise RuntimeError("opencode 进程不存在")
                code = proc.poll()
            if code is not None:
                raise RuntimeError(f"opencode 进程提前退出（code={code}）")
            try:
                with urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except URLError:
                time.sleep(1)
                continue
            except OSError:
                time.sleep(1)
                continue
            time.sleep(0.5)
        raise RuntimeError(f"等待 opencode 启动超时（>{int(timeout_seconds)}s）")


_manager = OpenCodeRuntimeManager()


def get_opencode_runtime_manager() -> OpenCodeRuntimeManager:
    return _manager


def get_opencode_llm_record():
    return _manager._load_active_llm_record()
