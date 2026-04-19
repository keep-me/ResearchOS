from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.routers import projects as projects_router
from packages.integrations.llm_client import LLMClient
from packages.integrations.llm_engine_profiles import build_llm_engine_profile_id
from packages.storage import db
from packages.storage.db import Base
from packages.storage.repositories import LLMConfigRepository


def _configure_test_db(monkeypatch) -> None:
    import packages.storage.models  # noqa: F401

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _bind_projects_root(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "project-roots"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(projects_router, "default_projects_root", lambda: root)


def _seed_llm_profiles() -> dict[str, str]:
    with db.session_scope() as session:
        repo = LLMConfigRepository(session)
        active = repo.create(
            name="Primary OpenAI",
            provider="openai",
            api_key="sk-primary",
            api_base_url="https://primary.example.com",
            model_skim="primary-mini",
            model_deep="primary-deep",
            model_vision="primary-vision",
            embedding_provider="openai",
            embedding_api_key="sk-embed",
            embedding_api_base_url="https://embed.example.com",
            model_embedding="text-embedding-3-small",
            model_fallback="primary-fallback",
        )
        alternate = repo.create(
            name="Secondary Anthropic",
            provider="anthropic",
            api_key="sk-anthropic",
            api_base_url="https://api.anthropic.com",
            model_skim="claude-haiku-test",
            model_deep="claude-sonnet-test",
            model_vision="claude-vision-test",
            embedding_provider="openai",
            embedding_api_key="sk-embed-2",
            embedding_api_base_url="https://embed-2.example.com",
            model_embedding="text-embedding-3-large",
            model_fallback="claude-fallback-test",
        )
        repo.activate(active.id)
        return {
            "active_deep": build_llm_engine_profile_id(active.id, "deep"),
            "active_fallback": build_llm_engine_profile_id(active.id, "fallback"),
            "alternate_deep": build_llm_engine_profile_id(alternate.id, "deep"),
        }


def test_llm_client_resolves_engine_profile_to_selected_config(monkeypatch):
    _configure_test_db(monkeypatch)
    profile_ids = _seed_llm_profiles()

    target = LLMClient()._resolve_model_target(
        "project_literature_review",
        profile_ids["alternate_deep"],
    )

    assert target.provider == "anthropic"
    assert target.model == "claude-sonnet-test"
    assert target.base_url == "https://api.anthropic.com"
    assert target.variant == "medium"


def test_project_workspace_context_and_run_expose_engine_bindings(monkeypatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    profile_ids = _seed_llm_profiles()
    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)

    project = projects_router.create_project(
        projects_router.ProjectCreateRequest(
            name="Engine Binding Project",
            description="engine binding integration",
        )
    )["item"]

    workspace_context = projects_router.get_project_workspace_context(project["id"])["item"]
    assert workspace_context["engine_profiles"]
    assert workspace_context["default_selections"]["executor_engine_id"]
    assert workspace_context["default_selections"]["reviewer_engine_id"]

    run = projects_router.create_project_run(
        project["id"],
        projects_router.ProjectRunCreateRequest(
            workflow_type="literature_review",
            prompt="Summarize the project using explicit engine bindings.",
            executor_engine_id=profile_ids["alternate_deep"],
            reviewer_engine_id=profile_ids["active_fallback"],
        ),
    )["item"]

    assert run["executor_engine_id"] == profile_ids["alternate_deep"]
    assert run["reviewer_engine_id"] == profile_ids["active_fallback"]
    assert "Secondary Anthropic" in str(run["executor_engine_label"])
    assert "Primary OpenAI" in str(run["reviewer_engine_label"])
    assert (run["metadata"] or {}).get("engine_bindings")
