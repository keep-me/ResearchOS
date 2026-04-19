from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run_python(code: str, *, tmp_path: Path, db_name: str) -> dict:
    env = os.environ.copy()
    db_path = tmp_path / db_name
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["RESEARCHOS_ENV_FILE"] = str(tmp_path / "empty.env")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stdout
    return json.loads(lines[-1])


def test_importing_storage_db_does_not_create_schema(tmp_path: Path) -> None:
    payload = _run_python(
        """
import json
import os
import sqlite3
from pathlib import Path

db_path = Path(os.environ["DATABASE_URL"].replace("sqlite:///", ""))
sqlite3.connect(db_path).close()
import packages.storage.db  # noqa: F401

with sqlite3.connect(db_path) as conn:
    tables = sorted(
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
print(json.dumps({"tables": tables}, ensure_ascii=False))
        """,
        tmp_path=tmp_path,
        db_name="import_only.db",
    )

    assert payload["tables"] == []


def test_explicit_bootstrap_initializes_schema(tmp_path: Path) -> None:
    payload = _run_python(
        """
import json
import os
import sqlite3
from pathlib import Path

db_path = Path(os.environ["DATABASE_URL"].replace("sqlite:///", ""))
sqlite3.connect(db_path).close()

from packages.storage.bootstrap import bootstrap_local_runtime

bootstrap_local_runtime()

with sqlite3.connect(db_path) as conn:
    tables = sorted(
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
    revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
print(json.dumps({"tables": tables, "revision": revision}, ensure_ascii=False))
        """,
        tmp_path=tmp_path,
        db_name="explicit_bootstrap.db",
    )

    assert "alembic_version" in payload["tables"]
    assert "papers" in payload["tables"]
    assert "topic_subscriptions" in payload["tables"]
    assert "analysis_reports" in payload["tables"]
    assert "projects" in payload["tables"]
    assert payload["revision"] == "20260414_0012_schema_reconciliation"


def test_explicit_bootstrap_stamps_legacy_runtime_schema(tmp_path: Path) -> None:
    payload = _run_python(
        """
import json
import os
import sqlite3
from pathlib import Path

from packages.storage import models  # noqa: F401
from packages.storage.db import Base, engine

db_path = Path(os.environ["DATABASE_URL"].replace("sqlite:///", ""))
sqlite3.connect(db_path).close()
Base.metadata.create_all(bind=engine)

from packages.storage.bootstrap import bootstrap_local_runtime

bootstrap_local_runtime()

with sqlite3.connect(db_path) as conn:
    revision = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    tables = sorted(
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )
print(json.dumps({"tables": tables, "revision": revision}, ensure_ascii=False))
        """,
        tmp_path=tmp_path,
        db_name="legacy_runtime_schema.db",
    )

    assert "alembic_version" in payload["tables"]
    assert "project_runs" in payload["tables"]
    assert payload["revision"] == "20260414_0012_schema_reconciliation"
