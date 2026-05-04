from enum import StrEnum


class ReadStatus(StrEnum):
    unread = "unread"
    skimmed = "skimmed"
    deep_read = "deep_read"


class PipelineStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ActionType(StrEnum):
    """论文入库行动类型"""

    initial_import = "initial_import"
    manual_collect = "manual_collect"
    auto_collect = "auto_collect"
    agent_collect = "agent_collect"
    subscription_ingest = "subscription_ingest"
    reference_import = "reference_import"


class ProjectWorkflowType(StrEnum):
    init_repo = "init_repo"
    autoresearch_claude_code = "autoresearch_claude_code"
    literature_review = "literature_review"
    idea_discovery = "idea_discovery"
    novelty_check = "novelty_check"
    research_review = "research_review"
    run_experiment = "run_experiment"
    experiment_audit = "experiment_audit"
    auto_review_loop = "auto_review_loop"
    paper_plan = "paper_plan"
    paper_figure = "paper_figure"
    paper_write = "paper_write"
    paper_compile = "paper_compile"
    paper_writing = "paper_writing"
    rebuttal = "rebuttal"
    paper_improvement = "paper_improvement"
    full_pipeline = "full_pipeline"
    monitor_experiment = "monitor_experiment"
    sync_workspace = "sync_workspace"
    custom_run = "custom_run"


class ProjectRunStatus(StrEnum):
    draft = "draft"
    queued = "queued"
    paused = "paused"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class ProjectRunActionType(StrEnum):
    continue_run = "continue"
    run_experiment = "run_experiment"
    monitor = "monitor"
    review = "review"
    retry = "retry"
    sync_workspace = "sync_workspace"
    custom = "custom"
