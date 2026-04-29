"""
应用配置 - Pydantic Settings
支持桌面模式通过 RESEARCHOS_ENV_FILE / RESEARCHOS_DATA_DIR 环境变量注入路径。
"""

from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.path_utils import is_foreign_windows_path, sqlite_url_for_path


def _resolve_env_file() -> str:
    """优先使用 RESEARCHOS_ENV_FILE 环境变量指定的路径"""
    return os.environ.get("RESEARCHOS_ENV_FILE", ".env")


def _default_data_dir() -> Path:
    configured = os.environ.get("RESEARCHOS_DATA_DIR", "").strip()
    if configured:
        if is_foreign_windows_path(configured):
            raise RuntimeError(
                "RESEARCHOS_DATA_DIR is a Windows path on a non-Windows host; "
                "use a host-mounted POSIX path or run the API on Windows."
            )
        return Path(configured).expanduser().resolve()
    container_dir = Path("/app/data")
    if container_dir.exists():
        return container_dir
    return Path("./data").resolve()


def _sqlite_url(path: Path) -> str:
    return sqlite_url_for_path(path)


def default_database_file(data_dir: Path) -> Path:
    return data_dir / "researchos.db"


def _default_database_url() -> str:
    data_dir = _default_data_dir()
    database_file = default_database_file(data_dir)
    return _sqlite_url(database_file)


def _default_pdf_storage_root() -> Path:
    return _default_data_dir() / "papers"


def _default_brief_output_root() -> Path:
    return _default_data_dir() / "briefs"


class Settings(BaseSettings):
    app_env: str = "dev"
    app_name: str = "ResearchOS API"
    api_host: str = "0.0.0.0"
    api_port: int = 8002

    # 站点配置
    site_url: str = "http://localhost:3002"  # 默认本地，生产环境设为 https://pm.vibingu.cn

    # 认证配置
    auth_password: str = ""  # 站点密码，为空则禁用认证
    auth_password_hash: str = ""  # 推荐使用 bcrypt 哈希
    auth_secret_key: str = ""  # JWT 密钥，开启认证时必须显式配置
    allow_unauthenticated: bool = False  # 非 dev 环境必须显式允许无认证
    expose_api_docs: bool = True  # 生产环境可关闭 /docs 和 /openapi.json

    database_url: str = Field(default_factory=_default_database_url)
    pdf_storage_root: Path = Field(default_factory=_default_pdf_storage_root)
    brief_output_root: Path = Field(default_factory=_default_brief_output_root)
    automation_allowed_roots: str = "/app,/app/data"
    # 图表提取模式: arxiv_source / mineru
    figure_extract_mode: str = "arxiv_source"
    mineru_backend: str = "api"
    mineru_api_base_url: str = "https://mineru.net"
    mineru_api_token: str | None = None
    mineru_api_model_version: str = "pipeline"
    mineru_api_poll_interval_seconds: float = 3.0
    mineru_api_timeout_seconds: int = 300
    mineru_api_upload_timeout_seconds: int = 600
    agent_max_tool_steps: int = 20
    agent_compaction_auto: bool = True
    agent_compaction_reserved_tokens: int = 20000
    agent_compaction_fallback_context_window: int = 128000
    agent_retry_max_attempts: int = 2
    skim_score_threshold: float = 0.65
    dashboard_trend_cron: str = "0 16 * * *"
    daily_cron: str = "0 21 * * *"
    weekly_cron: str = "0 22 * * 0"
    cors_allow_origins: str = (
        "http://localhost:3002,http://127.0.0.1:3002"  # 本地前端
    )

    # LLM Provider: openai / anthropic / zhipu / gemini
    llm_provider: str = "zhipu"
    llm_model_skim: str = "glm-4.7"
    llm_model_deep: str = "glm-4.7"
    llm_model_vision: str = "glm-4.6v"
    llm_model_fallback: str = "glm-4.7"
    reasoning_max_pages: int = 10
    reasoning_max_tokens: int = 4096
    reasoning_llm_timeout_seconds: int = 150
    embedding_model: str = "embedding-3"
    embedding_provider: str | None = None
    embedding_api_key: str | None = None
    embedding_api_base_url: str | None = None

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    opencode_provider: str | None = None
    opencode_api_key: str | None = None
    opencode_base_url: str | None = None
    opencode_model: str | None = None
    opencode_small_model: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    zhipu_api_key: str | None = None
    image_provider: str | None = None
    image_api_key: str | None = None
    image_api_base_url: str | None = None
    image_model: str = "gemini-2.5-flash-image"
    semantic_scholar_api_key: str | None = None
    openalex_email: str | None = None

    # Worker 调度
    worker_retry_max: int = 2
    worker_retry_base_delay: float = 5.0

    # 并发与缓存
    paper_concurrency: int = 5
    brief_cache_ttl: int = 300

    cost_guard_enabled: bool = True
    per_call_budget_usd: float = 0.05
    daily_budget_usd: float = 2.0

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    notify_default_to: str | None = None
    # 用户时区（影响"今天"判定、日报日期、按日分组等面向用户的日期逻辑）
    user_timezone: str = "Asia/Shanghai"

    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )


def _sqlite_database_path(database_url: str) -> Path | None:
    raw = str(database_url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme and parsed.scheme != "sqlite":
        return None
    if raw.startswith("sqlite:///"):
        path_text = unquote(raw[len("sqlite:///") :])
        if not path_text:
            return None
        if os.name == "nt" and path_text.startswith("/") and len(path_text) >= 3 and path_text[2] == ":":
            path_text = path_text[1:]
        return Path(path_text).expanduser()
    if raw.startswith("sqlite://"):
        return None
    if raw.startswith("sqlite:"):
        path_text = unquote(raw[len("sqlite:") :])
        return Path(path_text).expanduser() if path_text else None
    return Path(raw).expanduser()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    configured_data_dir = os.environ.get("RESEARCHOS_DATA_DIR", "").strip()
    if configured_data_dir:
        if is_foreign_windows_path(configured_data_dir):
            raise RuntimeError(
                "RESEARCHOS_DATA_DIR is a Windows path on a non-Windows host; "
                "use a host-mounted POSIX path or run the API on Windows."
            )
        data_dir = Path(configured_data_dir).expanduser().resolve()
        if not os.environ.get("DATABASE_URL", "").strip():
            settings.database_url = _sqlite_url(default_database_file(data_dir))
        if not os.environ.get("PDF_STORAGE_ROOT", "").strip():
            settings.pdf_storage_root = data_dir / "papers"
        if not os.environ.get("BRIEF_OUTPUT_ROOT", "").strip():
            settings.brief_output_root = data_dir / "briefs"
    settings.pdf_storage_root.mkdir(parents=True, exist_ok=True)
    settings.brief_output_root.mkdir(parents=True, exist_ok=True)
    db_path = _sqlite_database_path(settings.database_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings


def reload_settings() -> Settings:
    """Clear the cached settings object and reload it from the current environment."""
    get_settings.cache_clear()
    return get_settings()
