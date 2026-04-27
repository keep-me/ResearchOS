"""
ResearchOS Desktop Server — PyInstaller 入口
Tauri sidecar 调用此二进制，自动选端口 + 内嵌 scheduler。
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
from pathlib import Path

from packages.config import default_database_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("researchos.desktop")
_MCP_STDIO_MODE_FLAG = "--researchos-mcp-stdio"


def _find_free_port() -> int:
    """获取 OS 分配的空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _is_repo_root(path: Path) -> bool:
    return (
        path.joinpath("pyproject.toml").exists()
        and path.joinpath("apps", "desktop", "server.py").exists()
    )


def _resolve_repo_root() -> Path | None:
    for candidate in Path(__file__).resolve().parents:
        if _is_repo_root(candidate):
            return candidate
    current_dir = Path.cwd().resolve()
    for candidate in (current_dir, *current_dir.parents):
        if _is_repo_root(candidate):
            return candidate
    return None


def _platform_default_data_dir() -> Path:
    if sys.platform.startswith("win"):
        appdata = Path(os.environ.get("APPDATA", "")).expanduser()
        if str(appdata).strip():
            return appdata / "ResearchOS" / "data"
        return Path.home() / "AppData" / "Roaming" / "ResearchOS" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ResearchOS" / "data"
    return Path.home() / ".local" / "share" / "ResearchOS" / "data"


def _default_data_dir() -> Path:
    repo_root = _resolve_repo_root()
    if repo_root is not None:
        return repo_root / "data"
    return _platform_default_data_dir()


def _setup_data_dir(data_dir: Path) -> None:
    """确保数据目录结构完整"""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "papers").mkdir(exist_ok=True)
    (data_dir / "briefs").mkdir(exist_ok=True)
def _select_database_file(data_dir: Path) -> Path:
    return default_database_file(data_dir)


def _apply_env_overrides(data_dir: Path, env_file: Path | None) -> None:
    """
    根据用户配置的路径注入环境变量，
    让 Pydantic Settings 和 SQLAlchemy 读到正确的值。
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_select_database_file(data_dir).as_posix()}"
    os.environ["PDF_STORAGE_ROOT"] = str(data_dir / "papers")
    os.environ["BRIEF_OUTPUT_ROOT"] = str(data_dir / "briefs")

    if env_file and env_file.is_file():
        os.environ["RESEARCHOS_ENV_FILE"] = str(env_file)
        from dotenv import load_dotenv
        load_dotenv(env_file, override=True)
        logger.info("Loaded .env from %s", env_file)


def _desktop_cors_origins(port: int) -> str:
    origins = [
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "http://localhost",
        "https://localhost",
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        f"https://127.0.0.1:{port}",
        f"https://localhost:{port}",
    ]
    deduped: list[str] = []
    for origin in origins:
        if origin not in deduped:
            deduped.append(origin)
    return ",".join(deduped)


def _run_researchos_mcp_stdio() -> None:
    from packages.agent.mcp.researchos_mcp import main as mcp_main

    logger.info("ResearchOS MCP stdio mode starting")
    mcp_main()


def main() -> None:
    data_dir = Path(os.environ.get("RESEARCHOS_DATA_DIR", "")).expanduser()
    env_file_str = os.environ.get("RESEARCHOS_ENV_FILE", "")
    env_file = Path(env_file_str).expanduser() if env_file_str else None

    if not data_dir or not data_dir.is_absolute():
        data_dir = _default_data_dir()

    _setup_data_dir(data_dir)
    _apply_env_overrides(data_dir, env_file)

    if _MCP_STDIO_MODE_FLAG in sys.argv[1:]:
        _run_researchos_mcp_stdio()
        return

    port = _find_free_port()

    os.environ["API_HOST"] = "127.0.0.1"
    os.environ["API_PORT"] = str(port)
    os.environ["CORS_ALLOW_ORIGINS"] = _desktop_cors_origins(port)
    os.environ.setdefault("RESEARCHOS_SERVE_FRONTEND", "1")
    os.environ.setdefault("RESEARCHOS_EMBED_WORKER", "1")
    os.environ.setdefault("RESEARCHOS_DASHBOARD_TREND_ON_DEMAND", "1")

    # Tauri 通过 stdout 读取端口号（协议：首行 JSON）
    sys.stdout.write(json.dumps({"port": port}) + "\n")
    sys.stdout.flush()

    logger.info("ResearchOS Desktop starting on 127.0.0.1:%d", port)
    logger.info("Web UI: http://127.0.0.1:%d", port)
    logger.info("Data dir: %s", data_dir)

    import uvicorn
    from apps.api.main import app

    def _handle_signal(sig, _frame):
        logger.info("Received signal %s, shutting down...", sig)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
