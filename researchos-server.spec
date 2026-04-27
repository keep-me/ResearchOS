# -*- mode: python ; coding: utf-8 -*-
"""
ResearchOS Desktop — PyInstaller spec
打包 Python 后端为独立二进制，供 Tauri sidecar 调用。
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

block_cipher = None
ROOT = Path(SPECPATH)
frontend_dist = ROOT / "frontend" / "dist"
frontend_datas = [(str(frontend_dist), "frontend/dist")] if frontend_dist.exists() else []
passlib_hiddenimports = collect_submodules("passlib.handlers")
pillow_hiddenimports = collect_submodules("PIL")
pillow_datas = collect_data_files("PIL")
try:
    mineru_hiddenimports = collect_submodules("mineru")
    mineru_datas = collect_data_files("mineru")
except Exception:
    mineru_hiddenimports = []
    mineru_datas = []
try:
    magika_hiddenimports = collect_submodules("magika")
    magika_datas = collect_data_files("magika")
except Exception:
    magika_hiddenimports = []
    magika_datas = []
try:
    fast_langdetect_hiddenimports = collect_submodules("fast_langdetect")
    fast_langdetect_datas = collect_data_files("fast_langdetect")
except Exception:
    fast_langdetect_hiddenimports = []
    fast_langdetect_datas = []
mcp_hiddenimports = [
    "mcp",
    "mcp.types",
    *collect_submodules("mcp.server"),
    *collect_submodules("mcp.shared"),
]
agent_hiddenimports = collect_submodules("packages.agent")

try:
    winpty_hiddenimports = collect_submodules("winpty")
    winpty_datas = collect_data_files("winpty")
    winpty_binaries = collect_dynamic_libs("winpty")
except Exception:
    winpty_hiddenimports = []
    winpty_datas = []
    winpty_binaries = []

a = Analysis(
    [str(ROOT / "apps" / "desktop" / "server.py")],
    pathex=[str(ROOT)],
    binaries=[
        *winpty_binaries,
    ],
    datas=[
        (str(ROOT / "infra" / "migrations"), "infra/migrations"),
        (str(ROOT / "alembic.ini"), "."),
        *pillow_datas,
        *mineru_datas,
        *magika_datas,
        *fast_langdetect_datas,
        *winpty_datas,
        *frontend_datas,
    ],
    hiddenimports=[
        "apps.api.main",
        "apps.worker.main",
        "packages.config",
        "packages.ai",
        "packages.ai.agent_service",
        "packages.ai.agent_tools",
        "packages.ai.brief_service",
        "packages.ai.daily_runner",
        "packages.ai.graph_service",
        "packages.ai.pipelines",
        "packages.ai.rag_service",
        "packages.ai.task_manager",
        "packages.ai.keyword_service",
        "packages.ai.recommendation_service",
        "packages.ai.reasoning_service",
        "packages.ai.figure_service",
        "packages.ai.mineru_runtime",
        "packages.ai.writing_service",
        "packages.ai.cost_guard",
        "packages.ai.cli_agent_service",
        "packages.ai.terminal_service",
        "packages.ai.researchos_mcp",
        "packages.domain",
        "packages.domain.enums",
        "packages.domain.schemas",
        "packages.integrations",
        "packages.integrations.llm_client",
        "packages.storage",
        "packages.storage.db",
        "packages.storage.models",
        "packages.storage.repositories",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "fastapi",
        "starlette",
        "sqlalchemy",
        "sqlalchemy.dialects.sqlite",
        "alembic",
        "apscheduler",
        "apscheduler.schedulers.blocking",
        "apscheduler.triggers.cron",
        "pydantic",
        "pydantic_settings",
        "httpx",
        "dotenv",
        "bcrypt",
        *agent_hiddenimports,
        *mcp_hiddenimports,
        *mineru_hiddenimports,
        *magika_hiddenimports,
        *fast_langdetect_hiddenimports,
        *passlib_hiddenimports,
        *pillow_hiddenimports,
        *winpty_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    module_collection_mode={
        "mineru": "py+pyz",
        "transformers": "py+pyz",
        "torchvision": "py+pyz",
    },
    excludes=["tkinter", "matplotlib", "test", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="researchos-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
