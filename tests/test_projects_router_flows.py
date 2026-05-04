from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.pool import StaticPool

from apps.api.routers import projects as projects_router
from packages.agent.session.session_runtime import append_session_message, ensure_session_record
from packages.domain.enums import ProjectRunActionType, ProjectRunStatus, ProjectWorkflowType
from packages.domain.schemas import PaperCreate
from packages.domain.task_tracker import global_tracker
from packages.integrations.llm_client import LLMResult
from packages.storage import db
from packages.storage.db import Base
from packages.storage.models import GeneratedContent
from packages.storage.repositories import GeneratedContentRepository, PaperRepository


def _configure_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(db, "SessionLocal", session_local)


def _bind_projects_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "project-roots"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(projects_router, "default_projects_root", lambda: root)
    return root


def _create_project(name: str = "Project Flow Test") -> dict:
    return projects_router.create_project(
        projects_router.ProjectCreateRequest(
            name=name,
            description="integration test",
        )
    )["item"]


def _seed_paper(arxiv_id: str = "2603.00001") -> str:
    with db.session_scope() as session:
        repo = PaperRepository(session)
        paper = repo.upsert_paper(
            PaperCreate(
                arxiv_id=arxiv_id,
                title=f"Paper {arxiv_id}",
                abstract="Test abstract for project flows.",
                metadata={},
            )
        )
        return paper.id


def _init_local_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    (path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ResearchOS Tester",
            "-c",
            "user.email=tester@example.com",
            "commit",
            "-m",
            "init commit",
        ],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


def test_project_crud_touch_and_default_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    root = _bind_projects_root(monkeypatch, tmp_path)

    item = _create_project("Graph RL Workspace")
    project_id = item["id"]
    assert item["name"] == "Graph RL Workspace"
    assert item["workdir"].startswith(str(root))
    assert Path(item["workdir"]).exists()

    listed = projects_router.list_projects()["items"]
    assert len(listed) == 1
    assert listed[0]["id"] == project_id

    fetched = projects_router.get_project(project_id)["item"]
    assert fetched["id"] == project_id

    updated = projects_router.update_project(
        project_id,
        projects_router.ProjectUpdateRequest(
            name="Graph RL Workspace v2",
            description="updated",
        ),
    )["item"]
    assert updated["name"] == "Graph RL Workspace v2"
    assert updated["description"] == "updated"

    targets = projects_router.list_project_targets(project_id)["items"]
    assert len(targets) >= 1
    assert any(target["is_primary"] for target in targets)

    touched = projects_router.touch_project(project_id)
    assert touched["ok"] is True
    assert touched["project_id"] == project_id
    assert touched["last_accessed_at"]

    deleted = projects_router.delete_project(project_id)
    assert deleted["deleted"] == project_id

    with pytest.raises(HTTPException) as exc:
        projects_router.get_project(project_id)
    assert exc.value.status_code == 404


def test_project_create_respects_explicit_local_workdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    explicit_workdir = tmp_path / "custom-project-root" / "anchorcot"

    item = projects_router.create_project(
        projects_router.ProjectCreateRequest(
            name="AnchorCoT Custom Root",
            description="custom workdir test",
            workdir=str(explicit_workdir),
        )
    )["item"]

    assert item["workdir"] == str(explicit_workdir)
    assert item["workspace_path"] == str(explicit_workdir)
    assert Path(item["workdir"]).exists()


def test_touch_project_returns_404_when_row_was_deleted_during_flush(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Touch Race Project")

    def _raise_stale(self, project_id: str):
        raise StaleDataError("stale row")

    monkeypatch.setattr(projects_router.ProjectRepository, "touch_last_accessed", _raise_stale)

    with pytest.raises(HTTPException) as exc:
        projects_router.touch_project(project["id"])

    assert exc.value.status_code == 404


def test_project_paper_link_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Paper Link Project")
    project_id = project["id"]
    paper_id = _seed_paper("2603.10001")

    added = projects_router.add_project_paper(
        project_id,
        projects_router.ProjectPaperRequest(paper_id=paper_id, note="important"),
    )["item"]
    assert added["id"] == paper_id
    assert added["note"] == "important"
    assert added["project_paper_id"]

    listed = projects_router.list_project_papers(project_id)["items"]
    assert len(listed) == 1
    assert listed[0]["id"] == paper_id

    removed = projects_router.remove_project_paper(project_id, paper_id)
    assert removed["deleted"] == paper_id
    assert projects_router.list_project_papers(project_id)["items"] == []

    with pytest.raises(HTTPException) as exc:
        projects_router.remove_project_paper(project_id, paper_id)
    assert exc.value.status_code == 404


def test_project_target_flow_and_remote_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Target Flow Project")
    project_id = project["id"]

    with pytest.raises(HTTPException) as exc:
        projects_router.create_project_target(
            project_id,
            projects_router.ProjectTargetCreateRequest(
                label="remote-a",
                workspace_server_id="ssh-01",
                remote_workdir=None,
            ),
        )
    assert exc.value.status_code == 400
    assert "remote_workdir" in str(exc.value.detail)

    created = projects_router.create_project_target(
        project_id,
        projects_router.ProjectTargetCreateRequest(
            label="remote-a",
            workspace_server_id="ssh-01",
            remote_workdir="/data/work-a",
            enabled=True,
            is_primary=False,
        ),
    )["item"]
    target_id = created["id"]
    assert created["workspace_server_id"] == "ssh-01"
    assert created["remote_workdir"] == "/data/work-a"

    updated = projects_router.update_project_target(
        project_id,
        target_id,
        projects_router.ProjectTargetUpdateRequest(
            label="remote-a-v2",
            remote_workdir="/data/work-b",
            enabled=False,
        ),
    )["item"]
    assert updated["label"] == "remote-a-v2"
    assert updated["remote_workdir"] == "/data/work-b"
    assert updated["enabled"] is False

    deleted = projects_router.delete_project_target(project_id, target_id)
    assert deleted["deleted"] == target_id
    # list 会自动确保至少有一个默认目标
    assert len(projects_router.list_project_targets(project_id)["items"]) >= 1


def test_project_workspace_context_aggregate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Workspace Context Project")
    project_id = project["id"]

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)
    created_target = projects_router.create_project_target(
        project_id,
        projects_router.ProjectTargetCreateRequest(
            label="remote-main",
            workspace_server_id="ssh-main",
            remote_workdir="/srv/research/workspace-context",
            enabled=True,
            is_primary=True,
        ),
    )["item"]
    created_run = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            target_id=created_target["id"],
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="Summarize this workspace.",
        ),
    )["item"]

    payload = projects_router.get_project_workspace_context(project_id)["item"]
    assert payload["project"]["id"] == project_id
    assert payload["project"]["workspace_path"] == "/srv/research/workspace-context"
    assert payload["project"]["target_count"] >= 1
    assert payload["project"]["run_count"] == 1
    assert payload["default_selections"]["target_id"] == created_target["id"]
    assert payload["default_selections"]["run_id"] == created_run["id"]
    assert payload["default_selections"]["workflow_type"]
    assert any(item["id"] == created_target["id"] for item in payload["targets"])
    assert any(item["id"] == created_run["id"] for item in payload["runs"])
    assert payload["workflow_presets"]
    assert payload["planned_workflow_presets"] == []
    assert payload["action_items"]
    assert payload["agent_templates"]
    assert "workspace_health" in payload["targets"][0]


def test_workflow_presets_hide_legacy_items(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)

    payload = projects_router.list_workflow_presets()
    workflow_types = [item["workflow_type"] for item in payload["items"]]

    assert workflow_types == [
        ProjectWorkflowType.idea_discovery.value,
        ProjectWorkflowType.run_experiment.value,
        ProjectWorkflowType.auto_review_loop.value,
        ProjectWorkflowType.paper_writing.value,
        ProjectWorkflowType.rebuttal.value,
        ProjectWorkflowType.full_pipeline.value,
    ]
    assert [item["label"] for item in payload["items"]] == [
        "Workflow 1 · Idea Discovery",
        "Workflow 1.5 · Experiment Bridge",
        "Workflow 2 · Auto Review Loop",
        "Workflow 3 · Paper Writing",
        "Workflow 4 · Rebuttal",
        "One-Click · Research Pipeline",
    ]
    assert all(str(item.get("intro") or "").strip() for item in payload["items"])
    assert all(
        isinstance(item.get("usage_steps"), list) and item["usage_steps"]
        for item in payload["items"]
    )
    assert all(str(item.get("sample_prompt") or "").strip() for item in payload["items"])
    assert payload["items"][1]["sample_execution_command"]
    assert payload["items"][4]["sample_rebuttal_review_bundle"]
    assert payload["items"][5]["sample_execution_command"]
    assert ProjectWorkflowType.literature_review.value not in workflow_types
    assert ProjectWorkflowType.init_repo.value not in workflow_types
    assert ProjectWorkflowType.autoresearch_claude_code.value not in workflow_types
    assert ProjectWorkflowType.custom_run.value not in workflow_types
    assert payload["planned_items"] == []


def test_project_companion_overview_includes_latest_run_tasks_and_acp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Companion Overview Project")
    project_id = project["id"]

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)
    created_run = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="Companion overview run.",
        ),
    )["item"]

    monkeypatch.setattr(
        projects_router.global_tracker,
        "list_tasks",
        lambda limit=100, task_type=None: [
            {
                "task_id": "task-project-overview",
                "title": "Overview task",
                "project_id": project_id,
                "run_id": created_run["id"],
                "finished": False,
                "updated_at": 10,
                "status": "running",
            },
            {
                "task_id": "task-other-project",
                "title": "Other task",
                "project_id": "other-project",
                "finished": False,
                "updated_at": 5,
                "status": "running",
            },
        ],
    )

    class _FakeAcpService:
        @staticmethod
        def get_backend_summary():
            return {
                "chat_ready": True,
                "default_server": "mock-acp",
                "chat_status_label": "ACP 已连接 · Mock ACP",
            }

    monkeypatch.setattr(projects_router, "get_acp_registry_service", lambda: _FakeAcpService())

    payload = projects_router.get_projects_companion_overview(project_limit=10, task_limit=10)

    assert payload["items"]
    item = next(entry for entry in payload["items"] if entry["id"] == project_id)
    assert item["latest_run"]["id"] == created_run["id"]
    assert item["active_task_count"] == 1
    assert payload["acp"]["chat_ready"] is True
    assert payload["tasks"][0]["task_id"] == "task-project-overview"


def test_project_companion_snapshot_includes_tasks_sessions_and_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Companion Snapshot Project")
    project_id = project["id"]

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)
    created_run = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="Companion snapshot run.",
        ),
    )["item"]

    session_record = ensure_session_record(
        session_id="companion-session",
        directory=project["workspace_path"],
        workspace_path=project["workspace_path"],
        workspace_server_id=project["workspace_server_id"],
        mode="research",
    )
    append_session_message(
        session_id=session_record["id"],
        role="user",
        content="Please analyze this project.",
    )
    append_session_message(
        session_id=session_record["id"],
        role="assistant",
        content="Latest assistant reply for snapshot preview.",
    )

    monkeypatch.setattr(
        projects_router.global_tracker,
        "list_tasks",
        lambda limit=100, task_type=None: [
            {
                "task_id": "task-project-snapshot",
                "title": "Snapshot task",
                "project_id": project_id,
                "run_id": created_run["id"],
                "finished": False,
                "updated_at": 20,
                "status": "running",
            },
        ],
    )

    class _FakeAcpService:
        @staticmethod
        def get_backend_summary():
            return {
                "chat_ready": False,
                "default_server": None,
                "chat_status_label": "未绑定 ACP",
            }

    monkeypatch.setattr(projects_router, "get_acp_registry_service", lambda: _FakeAcpService())

    payload = projects_router.get_project_companion_snapshot(
        project_id,
        task_limit=10,
        session_limit=10,
        include_latest_session_messages=True,
        latest_session_message_limit=20,
    )["item"]

    assert payload["project"]["id"] == project_id
    assert payload["workspace_context"]["project"]["id"] == project_id
    assert payload["tasks"][0]["task_id"] == "task-project-snapshot"
    assert payload["sessions"]
    assert payload["sessions"][0]["id"] == session_record["id"]
    assert payload["sessions"][0]["latest_message"]["text"].startswith("Latest assistant reply")
    assert payload["latest_session_messages"]
    assert payload["latest_session_messages"][-1]["info"]["role"] == "assistant"
    assert payload["acp"]["chat_ready"] is False


def test_project_repo_flow_and_commit_listing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    if not shutil.which("git"):
        pytest.skip("git is required for commit listing test")

    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Repo Flow Project")
    project_id = project["id"]

    git_repo = tmp_path / "git-workspace"
    _init_local_git_repo(git_repo)

    created = projects_router.create_project_repo(
        project_id,
        projects_router.ProjectRepoRequest(
            repo_url="https://github.com/example/repo",
            local_path=str(git_repo),
            cloned_at="2026-03-17T10:20:30",
            is_workdir_repo=False,
        ),
    )["item"]
    repo_id = created["id"]
    assert created["local_path"] == str(git_repo)

    listed = projects_router.list_project_repos(project_id)["items"]
    assert len(listed) == 1
    assert listed[0]["id"] == repo_id

    updated = projects_router.update_project_repo(
        project_id,
        repo_id,
        projects_router.ProjectRepoUpdateRequest(
            is_workdir_repo=True,
        ),
    )["item"]
    assert updated["is_workdir_repo"] is True

    commits = projects_router.list_repo_commits(project_id, repo_id, limit=10)["items"]
    assert commits
    assert commits[0]["message"] == "init commit"

    deleted = projects_router.delete_project_repo(project_id, repo_id)
    assert deleted["deleted"] == repo_id
    assert projects_router.list_project_repos(project_id)["items"] == []


def test_project_idea_manual_sync_and_async_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Idea Flow Project")
    project_id = project["id"]
    paper_id = _seed_paper("2603.20001")
    projects_router.add_project_paper(
        project_id,
        projects_router.ProjectPaperRequest(paper_id=paper_id, note=None),
    )

    manual = projects_router.create_project_idea(
        project_id,
        projects_router.ProjectIdeaRequest(
            title="Manual idea",
            content="Initial markdown content",
            paper_ids=[paper_id],
        ),
    )["item"]
    assert manual["title"] == "Manual idea"

    def _fake_complete_json(self, prompt, stage, max_tokens=None, max_retries=1):
        assert stage == "project_idea_generate"
        assert "Idea Flow Project" in prompt
        assert "paper_id:" in prompt
        assert "Test abstract for project flows" not in prompt
        return LLMResult(
            content='{"title":"Auto Idea","content":"- step 1\\n- step 2"}',
            parsed_json={"title": "Auto Idea", "content": "- step 1\n- step 2"},
        )

    monkeypatch.setattr(
        "packages.integrations.llm_client.LLMClient.complete_json",
        _fake_complete_json,
    )
    generated = projects_router.generate_project_idea(
        project_id,
        projects_router.ProjectIdeaGenerateRequest(
            paper_ids=[paper_id],
            focus="robustness",
        ),
    )["item"]
    assert generated["title"] == "Auto Idea"
    assert "step 1" in generated["content"]

    listed = projects_router.list_project_ideas(project_id)["items"]
    assert len(listed) == 2

    updated = projects_router.update_project_idea(
        project_id,
        manual["id"],
        projects_router.ProjectIdeaUpdateRequest(
            title="Manual idea v2",
            content="updated",
            paper_ids=[paper_id],
        ),
    )["item"]
    assert updated["title"] == "Manual idea v2"
    assert updated["content"] == "updated"

    captured: dict[str, object] = {}

    def _fake_submit(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "task-idea-123"

    monkeypatch.setattr(projects_router.global_tracker, "submit", _fake_submit)
    async_result = projects_router.generate_project_idea_async(
        project_id,
        projects_router.ProjectIdeaGenerateRequest(
            paper_ids=[paper_id],
            focus="efficiency",
        ),
    )
    assert async_result["task_id"] == "task-idea-123"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("task_type") == "project_idea_generate"
    assert kwargs.get("project_id") == project_id

    deleted = projects_router.delete_project_idea(project_id, manual["id"])
    assert deleted["deleted"] == manual["id"]


def test_project_run_flow_retry_and_actions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Run Flow Project")
    project_id = project["id"]

    submitted_run_ids: list[str] = []
    submitted_action_ids: list[str] = []
    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: True)

    def _fake_submit_project_run(run_id: str):
        submitted_run_ids.append(run_id)
        return "task-run-1"

    def _fake_submit_project_run_action(action_id: str):
        submitted_action_ids.append(action_id)
        with db.session_scope() as session:
            repo = projects_router.ProjectRepository(session)
            action = repo.get_run_action(action_id)
            run = repo.get_run(action.run_id) if action is not None else None
            if action is not None:
                repo.update_run_action(
                    action_id,
                    task_id="task-action-1",
                    log_path=(run.log_path + ".followup")
                    if run and run.log_path
                    else "/tmp/followup.log",
                    result_path=(run.run_directory + f"/actions/{action_id}.md")
                    if run and run.run_directory
                    else f"/tmp/{action_id}.md",
                )
        return "task-action-1"

    monkeypatch.setattr(projects_router, "submit_project_run", _fake_submit_project_run)
    monkeypatch.setattr(
        projects_router, "submit_project_run_action", _fake_submit_project_run_action
    )

    created_run = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="Summarize core findings.",
            max_iterations=3,
            reviewer_model="mock-model",
        ),
    )["item"]
    run_id = created_run["id"]
    assert run_id in submitted_run_ids
    assert created_run["workflow_type"] == ProjectWorkflowType.literature_review.value
    assert (
        created_run["orchestration"]["workflow_type"] == ProjectWorkflowType.literature_review.value
    )
    assert len(created_run["stage_trace"]) == len(created_run["orchestration"]["stages"])
    assert created_run["run_directory"]
    assert created_run["log_path"]
    assert "同步方案：" in created_run["summary"]

    action = projects_router.create_project_run_action(
        run_id,
        projects_router.ProjectRunActionRequest(
            action_type=ProjectRunActionType.review.value,
            prompt="Please tighten structure.",
        ),
    )["item"]
    assert action["run_id"] == run_id
    assert action["action_type"] == ProjectRunActionType.review.value
    assert action["id"] in submitted_action_ids
    assert action["task_id"] == "task-action-1"
    assert action["log_path"]
    assert action["result_path"]

    detail = projects_router.get_project_run(run_id)["item"]
    assert any(item["id"] == action["id"] for item in detail["actions"])
    assert isinstance(detail["recent_logs"], list)

    retried = projects_router.retry_project_run(run_id)["item"]
    assert retried["retry_of_run_id"] == run_id
    assert retried["id"] in submitted_run_ids

    listed = projects_router.list_project_runs(project_id, limit=10)["items"]
    assert len(listed) == 2


def test_project_run_paper_ids_build_full_index_and_link_to_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Run Paper Index Project")
    project_id = project["id"]
    paper_ids = [_seed_paper(f"2603.{index:05d}") for index in range(12)]

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)

    created = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="Use every linked paper as index context.",
            paper_ids=paper_ids,
        ),
    )["item"]

    assert created["paper_ids"] == paper_ids
    assert len(created["paper_index"]) == 12
    assert [item["paper_id"] for item in created["paper_index"]] == paper_ids
    assert created["paper_index"][0]["ref_id"] == "P1"
    assert created["paper_index"][-1]["ref_id"] == "P12"
    assert all("abstract" not in item for item in created["paper_index"])

    project_detail = projects_router.get_project(project_id)["item"]
    assert {item["id"] for item in project_detail["papers"]} == set(paper_ids)


def test_project_run_external_literature_candidate_import_links_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Candidate Import Project")
    project_id = project["id"]
    existing_paper_id = _seed_paper("2604.00001")

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)
    created = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="Find more papers.",
        ),
    )["item"]
    run_id = created["id"]

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        run = repo.get_run(run_id)
        metadata = dict(run.metadata_json or {})
        metadata["literature_candidates"] = [
            {
                "ref_id": "E1",
                "source": "arxiv_search",
                "status": "candidate",
                "title": "External Candidate",
                "abstract": "Candidate abstract should stay metadata-only until imported.",
                "arxiv_id": "2604.00001",
                "importable": True,
                "linkable": False,
            }
        ]
        repo.update_run(run_id, metadata=metadata)

    captured_entries: list[dict] = []

    def _fake_ingest_external_entries(*, entries, action_type, query):
        captured_entries.extend(entries)
        return {
            "requested": len(entries),
            "found": len(entries),
            "ingested": 0,
            "duplicates": 1,
            "missing_ids": [],
            "papers": [],
        }

    monkeypatch.setattr(
        projects_router.pipelines, "ingest_external_entries", _fake_ingest_external_entries
    )

    imported = projects_router.import_project_run_literature_candidates(
        run_id,
        projects_router.ProjectRunLiteratureCandidateImportRequest(candidate_ref_ids=["E1"]),
    )

    assert captured_entries and captured_entries[0]["title"] == "External Candidate"
    assert imported["imported_paper_ids"] == [existing_paper_id]
    assert imported["linked_paper_ids"] == [existing_paper_id]
    candidate = imported["item"]["literature_candidates"][0]
    assert candidate["status"] == "imported"
    assert candidate["paper_id"] == existing_paper_id
    assert candidate["project_linked"] is True
    assert any(item["paper_id"] == existing_paper_id for item in imported["item"]["paper_index"])

    listed = projects_router.list_project_run_literature_candidates(run_id)
    assert listed["items"][0]["paper_id"] == existing_paper_id
    project_detail = projects_router.get_project(project_id)["item"]
    assert {item["id"] for item in project_detail["papers"]} == {existing_paper_id}


def test_project_run_detail_falls_back_to_persisted_artifact_refs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Persisted Artifact Project")
    run_directory = tmp_path / "missing-run-directory"
    report_path = run_directory / "reports" / "literature-review.md"

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        target = repo.get_primary_target(project["id"])
        run = repo.create_run(
            project_id=project["id"],
            target_id=target.id if target else None,
            workflow_type=ProjectWorkflowType.literature_review,
            title="persisted artifact run",
            prompt="Summarize literature review",
            status=ProjectRunStatus.succeeded,
            active_phase="completed",
            summary="已完成",
            workdir=project["workdir"],
            run_directory=str(run_directory),
            log_path=str(run_directory / "run.log"),
            metadata={
                "workflow_output_markdown": "# Persisted Report\n\n- output",
                "artifact_refs": [
                    {
                        "path": str(report_path),
                        "relative_path": "reports/literature-review.md",
                        "kind": "report",
                    }
                ],
            },
        )
        run_id = run.id

    detail = projects_router.get_project_run(run_id)["item"]

    assert detail["artifact_refs"]
    assert detail["artifact_refs"][0]["path"] == str(report_path)
    assert detail["result_path"] == str(report_path)
    assert detail["metadata"]["artifact_refs"][0]["relative_path"] == "reports/literature-review.md"


def test_delete_project_run_removes_records_tasks_and_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Delete Run Project")
    run_task_id = "delete-run-task"
    action_task_id = "delete-run-action-task"
    run_id = ""
    action_id = ""
    generated_id = ""

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        generated_repo = GeneratedContentRepository(session)
        target = repo.get_primary_target(project["id"])
        run = repo.create_run(
            project_id=project["id"],
            target_id=target.id if target else None,
            workflow_type=ProjectWorkflowType.literature_review,
            title="delete me",
            prompt="cleanup",
            status=ProjectRunStatus.succeeded,
            active_phase="completed",
            summary="已完成",
            task_id=run_task_id,
            workdir=project["workdir"],
        )
        run_directory = Path(project["workdir"]) / ".auto-researcher" / "aris-runs" / run.id
        log_path = run_directory / "run.log"
        result_path = run_directory / "reports" / "literature-review.md"
        action_log_path = run_directory / "actions" / "followup.log"
        action_result_path = run_directory / "actions" / "followup.md"
        generated = generated_repo.create(
            content_type="project_literature_review",
            title="Delete Run Report",
            markdown="# Delete Run Report",
            keyword="delete",
            metadata_json={"project_id": project["id"], "run_id": run.id},
        )
        repo.update_run(
            run.id,
            run_directory=str(run_directory),
            log_path=str(log_path),
            result_path=str(result_path),
            metadata={
                "generated_content_id": generated.id,
                "artifact_refs": [
                    {
                        "path": str(result_path),
                        "relative_path": "reports/literature-review.md",
                        "kind": "report",
                    }
                ],
            },
        )
        action = repo.create_run_action(
            run_id=run.id,
            action_type=ProjectRunActionType.review,
            prompt="follow up",
            status=ProjectRunStatus.succeeded,
            active_phase="completed",
            summary="done",
            task_id=action_task_id,
            log_path=str(action_log_path),
            result_path=str(action_result_path),
        )
        run_id = run.id
        action_id = action.id
        generated_id = generated.id

    run_directory.mkdir(parents=True, exist_ok=True)
    log_path.write_text("run log\n", encoding="utf-8")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text("# report\n", encoding="utf-8")
    action_log_path.parent.mkdir(parents=True, exist_ok=True)
    action_log_path.write_text("action log\n", encoding="utf-8")
    action_result_path.write_text("# action\n", encoding="utf-8")

    global_tracker.start(
        run_task_id,
        "project_run",
        "delete run",
        total=1,
        metadata={"project_id": project["id"], "run_id": run_id},
    )
    global_tracker.finish(run_task_id)
    global_tracker.start(
        action_task_id,
        "project_action",
        "delete run action",
        total=1,
        metadata={"project_id": project["id"], "run_id": run_id, "action_id": action_id},
    )
    global_tracker.finish(action_task_id)

    payload = projects_router.delete_project_run(run_id, delete_artifacts=True)

    assert payload["deleted"] == run_id
    assert payload["artifacts_deleted"] is True
    assert str(run_directory) in payload["deleted_paths"]
    assert not run_directory.exists()
    assert global_tracker.get_task(run_task_id) is None
    assert global_tracker.get_task(action_task_id) is None

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        assert repo.get_run(run_id) is None
        assert repo.get_run_action(action_id) is None
        assert session.get(GeneratedContent, generated_id) is None


def test_delete_project_run_can_keep_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Keep Artifact Run Project")
    run_id = ""

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        target = repo.get_primary_target(project["id"])
        run = repo.create_run(
            project_id=project["id"],
            target_id=target.id if target else None,
            workflow_type=ProjectWorkflowType.literature_review,
            title="keep files",
            prompt="cleanup",
            status=ProjectRunStatus.failed,
            active_phase="completed",
            summary="failed",
            workdir=project["workdir"],
        )
        run_directory = Path(project["workdir"]) / ".auto-researcher" / "aris-runs" / run.id
        repo.update_run(
            run.id,
            run_directory=str(run_directory),
            log_path=str(run_directory / "run.log"),
            result_path=str(run_directory / "reports" / "literature-review.md"),
        )
        run_id = run.id

    run_directory.mkdir(parents=True, exist_ok=True)
    (run_directory / "run.log").write_text("run log\n", encoding="utf-8")

    payload = projects_router.delete_project_run(run_id, delete_artifacts=False)

    assert payload["deleted"] == run_id
    assert payload["artifacts_deleted"] is False
    assert run_directory.exists()


def test_delete_project_run_rejects_active_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Active Delete Run Project")
    run_id = ""

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        target = repo.get_primary_target(project["id"])
        run = repo.create_run(
            project_id=project["id"],
            target_id=target.id if target else None,
            workflow_type=ProjectWorkflowType.literature_review,
            title="active run",
            prompt="cleanup",
            status=ProjectRunStatus.running,
            active_phase="synthesize_evidence",
            summary="running",
            workdir=project["workdir"],
        )
        run_id = run.id

    with pytest.raises(HTTPException) as exc:
        projects_router.delete_project_run(run_id, delete_artifacts=True)

    assert exc.value.status_code == 400
    assert "先停止" in str(exc.value.detail)


def test_project_run_create_accepts_auto_proceed_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Auto Proceed Project")
    project_id = project["id"]

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)

    created_run = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.paper_plan.value,
            prompt="Draft an outline.",
            auto_proceed=False,
        ),
    )["item"]

    assert created_run["auto_proceed"] is False
    assert created_run["human_checkpoint_enabled"] is True
    assert created_run["metadata"]["auto_proceed"] is False
    assert created_run["metadata"]["human_checkpoint_enabled"] is True


def test_retry_legacy_project_run_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Legacy Retry Project")
    project_id = project["id"]

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        target = repo.get_primary_target(project_id)
        run = repo.create_run(
            project_id=project_id,
            target_id=target.id if target else None,
            workflow_type=ProjectWorkflowType.init_repo,
            title="legacy init repo run",
            prompt="bootstrap workspace",
            status=ProjectRunStatus.succeeded,
            active_phase="completed",
            summary="legacy workflow result",
            workdir=project["workdir"],
            metadata={},
        )
        run_id = run.id

    with pytest.raises(HTTPException) as exc:
        projects_router.retry_project_run(run_id)
    assert exc.value.status_code == 400
    assert "退役" in str(exc.value.detail)


def test_project_run_checkpoint_response_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Checkpoint Flow Project")
    project_id = project["id"]

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        target = repo.get_primary_target(project_id)
        target_id = target.id if target else None
        run = repo.create_run(
            project_id=project_id,
            target_id=target_id,
            workflow_type=ProjectWorkflowType.literature_review,
            title="checkpoint run",
            prompt="Summarize core findings.",
            status=ProjectRunStatus.paused,
            active_phase="awaiting_checkpoint",
            summary="已创建运行，等待人工确认后开始执行。",
            task_id="project-run-checkpoint",
            workdir=project["workdir"],
            metadata={
                "human_checkpoint_enabled": True,
                "checkpoint_state": "pending",
                "notification_recipients": ["reviewer@example.com"],
                "pending_checkpoint": {
                    "type": "preflight",
                    "label": "运行前确认",
                    "status": "pending",
                    "message": "已创建运行，等待人工确认后开始执行。",
                    "requested_at": "2026-03-18T10:00:00+00:00",
                },
            },
        )
        run_id = run.id

    submitted_run_ids: list[str] = []

    def _fake_submit_project_run(run_id: str):
        submitted_run_ids.append(run_id)
        with db.session_scope() as session:
            repo = projects_router.ProjectRepository(session)
            repo.update_run(
                run_id,
                status=ProjectRunStatus.running,
                active_phase="initializing",
                summary="工作流已启动，正在准备项目上下文。",
            )
        return "task-run-approved"

    monkeypatch.setattr(
        "packages.ai.project.execution_service.submit_project_run", _fake_submit_project_run
    )

    approved = projects_router.respond_project_run_checkpoint(
        run_id,
        projects_router.ProjectRunCheckpointResponseRequest(
            action="approve",
            comment="可以开始执行",
        ),
    )["item"]

    assert run_id in submitted_run_ids
    assert approved["status"] == ProjectRunStatus.running.value
    assert approved["checkpoint_state"] == "approved"
    assert approved["pending_checkpoint"] is None

    with db.session_scope() as session:
        repo = projects_router.ProjectRepository(session)
        retry_run = repo.create_run(
            project_id=project_id,
            target_id=target_id,
            workflow_type=ProjectWorkflowType.literature_review,
            title="checkpoint run reject",
            prompt="Summarize core findings.",
            status=ProjectRunStatus.paused,
            active_phase="awaiting_checkpoint",
            summary="已创建运行，等待人工确认后开始执行。",
            task_id="project-run-checkpoint-reject",
            workdir=project["workdir"],
            metadata={
                "human_checkpoint_enabled": True,
                "checkpoint_state": "pending",
                "notification_recipients": ["reviewer@example.com"],
                "pending_checkpoint": {
                    "type": "preflight",
                    "label": "运行前确认",
                    "status": "pending",
                    "message": "已创建运行，等待人工确认后开始执行。",
                    "requested_at": "2026-03-18T10:00:00+00:00",
                },
            },
        )
        reject_run_id = retry_run.id

    rejected = projects_router.respond_project_run_checkpoint(
        reject_run_id,
        projects_router.ProjectRunCheckpointResponseRequest(
            action="reject",
            comment="先补充数据说明",
        ),
    )["item"]

    assert rejected["status"] == ProjectRunStatus.cancelled.value
    assert rejected["checkpoint_state"] == "rejected"
    assert rejected["pending_checkpoint"] is None


def test_project_run_submit_failure_marks_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Run Fail Project")
    project_id = project["id"]

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: True)

    def _raise_submit_error(_run_id: str):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(projects_router, "submit_project_run", _raise_submit_error)

    created = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="test failure path",
        ),
    )["item"]
    assert created["status"] == ProjectRunStatus.failed.value
    assert "执行器启动失败" in created["summary"]


def test_project_run_and_action_validation_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _configure_test_db(monkeypatch)
    _bind_projects_root(monkeypatch, tmp_path)
    project = _create_project("Validation Project")
    project_id = project["id"]

    with pytest.raises(HTTPException) as exc:
        projects_router.create_project_run(
            project_id,
            projects_router.ProjectRunCreateRequest(
                workflow_type="not_a_valid_workflow",
                prompt="x",
            ),
        )
    assert exc.value.status_code == 400
    assert "workflow_type" in str(exc.value.detail)

    monkeypatch.setattr(projects_router, "supports_project_run", lambda workflow_type: False)
    created = projects_router.create_project_run(
        project_id,
        projects_router.ProjectRunCreateRequest(
            workflow_type=ProjectWorkflowType.literature_review.value,
            prompt="valid run",
        ),
    )["item"]

    with pytest.raises(HTTPException) as exc_action:
        projects_router.create_project_run_action(
            created["id"],
            projects_router.ProjectRunActionRequest(
                action_type="invalid_action",
                prompt="x",
            ),
        )
    assert exc_action.value.status_code == 400
    assert "action_type" in str(exc_action.value.detail)
