"""reconcile runtime-created schema with Alembic head

Revision ID: 20260414_0012_schema_reconciliation
Revises: 20260412_0011_add_project_research_wiki
Create Date: 2026-04-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "20260414_0012_schema_reconciliation"
down_revision: str | None = "20260412_0011_add_project_research_wiki"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    inspector = inspect(op.get_bind())
    if not _has_table(inspector, table_name):
        return
    if _has_column(inspector, table_name, column.name):
        return
    op.add_column(table_name, column)


def _create_index_if_missing(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    inspector = inspect(op.get_bind())
    if not _has_table(inspector, table_name):
        return
    if _has_index(inspector, table_name, index_name):
        return
    op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    from packages.storage import models  # noqa: F401
    from packages.storage.db import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)

    _add_column_if_missing(
        "papers",
        sa.Column("favorited", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )

    for column in (
        sa.Column("kind", sa.String(length=20), nullable=False, server_default=sa.text("'subscription'")),
        sa.Column("sort_by", sa.String(length=32), nullable=False, server_default=sa.text("'submittedDate'")),
        sa.Column("source", sa.String(length=32), nullable=False, server_default=sa.text("'arxiv'")),
        sa.Column("search_field", sa.String(length=32), nullable=False, server_default=sa.text("'all'")),
        sa.Column("priority_mode", sa.String(length=32), nullable=False, server_default=sa.text("'time'")),
        sa.Column("venue_tier", sa.String(length=32), nullable=False, server_default=sa.text("'all'")),
        sa.Column("venue_type", sa.String(length=32), nullable=False, server_default=sa.text("'all'")),
        sa.Column("venue_names", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("from_year", sa.Integer(), nullable=True),
        sa.Column("default_folder_id", sa.String(length=36), nullable=True),
        sa.Column("schedule_frequency", sa.String(length=20), nullable=False, server_default=sa.text("'daily'")),
        sa.Column("schedule_time_utc", sa.Integer(), nullable=False, server_default=sa.text("21")),
        sa.Column("enable_date_filter", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("date_filter_days", sa.Integer(), nullable=False, server_default=sa.text("7")),
        sa.Column("date_filter_start", sa.Date(), nullable=True),
        sa.Column("date_filter_end", sa.Date(), nullable=True),
        sa.Column("max_results_per_run", sa.Integer(), nullable=False, server_default=sa.text("20")),
        sa.Column("retry_limit", sa.Integer(), nullable=False, server_default=sa.text("2")),
    ):
        _add_column_if_missing("topic_subscriptions", column)

    for column in (
        sa.Column("input_cost_usd", sa.Float(), nullable=True),
        sa.Column("output_cost_usd", sa.Float(), nullable=True),
        sa.Column("total_cost_usd", sa.Float(), nullable=True),
    ):
        _add_column_if_missing("prompt_traces", column)

    _add_column_if_missing("pipeline_runs", sa.Column("decision_note", sa.Text(), nullable=True))

    for column in (
        sa.Column("embedding_provider", sa.String(length=32), nullable=False, server_default=sa.text("''")),
        sa.Column("embedding_api_key", sa.String(length=512), nullable=False, server_default=sa.text("''")),
        sa.Column("embedding_api_base_url", sa.String(length=512), nullable=False, server_default=sa.text("''")),
        sa.Column("image_provider", sa.String(length=32), nullable=False, server_default=sa.text("''")),
        sa.Column("image_api_key", sa.String(length=512), nullable=False, server_default=sa.text("''")),
        sa.Column("image_api_base_url", sa.String(length=512), nullable=False, server_default=sa.text("''")),
        sa.Column("model_image", sa.String(length=128), nullable=False, server_default=sa.text("''")),
    ):
        _add_column_if_missing("llm_provider_configs", column)

    _add_column_if_missing(
        "feishu_configs",
        sa.Column("timeout_action", sa.String(length=20), nullable=False, server_default=sa.text("'approve'")),
    )
    _add_column_if_missing(
        "agent_sessions",
        sa.Column("backend_id", sa.String(length=64), nullable=False, server_default=sa.text("'native'")),
    )
    _add_column_if_missing("image_analyses", sa.Column("image_path", sa.String(length=512), nullable=True))
    _add_column_if_missing("project_runs", sa.Column("executor_model", sa.String(length=128), nullable=True))
    _add_column_if_missing("project_runs", sa.Column("reviewer_model", sa.String(length=128), nullable=True))

    _create_index_if_missing("papers", "ix_papers_created_at", ["created_at"])
    _create_index_if_missing("papers", "ix_papers_read_status", ["read_status"])
    _create_index_if_missing("papers", "ix_papers_favorited", ["favorited"])
    _create_index_if_missing(
        "papers",
        "ix_papers_read_status_created_at",
        ["read_status", "created_at"],
    )
    _create_index_if_missing("prompt_traces", "ix_prompt_traces_created_at", ["created_at"])
    _create_index_if_missing("pipeline_runs", "ix_pipeline_runs_created_at", ["created_at"])
    _create_index_if_missing("topic_subscriptions", "ix_topic_subscriptions_kind", ["kind"])
    _create_index_if_missing(
        "topic_subscriptions",
        "ix_topic_subscriptions_default_folder_id",
        ["default_folder_id"],
    )
    _create_index_if_missing(
        "generated_contents",
        "ix_generated_contents_created_at",
        ["created_at"],
    )
    _create_index_if_missing("agent_messages", "ix_agent_messages_paper_id", ["paper_id"])
    _create_index_if_missing("agent_messages", "ix_agent_messages_created_at", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_messages_created_at", table_name="agent_messages")
    op.drop_index("ix_agent_messages_paper_id", table_name="agent_messages")
    op.drop_index("ix_generated_contents_created_at", table_name="generated_contents")
    op.drop_index(
        "ix_topic_subscriptions_default_folder_id",
        table_name="topic_subscriptions",
    )
    op.drop_index("ix_topic_subscriptions_kind", table_name="topic_subscriptions")
    op.drop_index("ix_pipeline_runs_created_at", table_name="pipeline_runs")
    op.drop_index("ix_prompt_traces_created_at", table_name="prompt_traces")
    op.drop_index("ix_papers_read_status_created_at", table_name="papers")
    op.drop_index("ix_papers_favorited", table_name="papers")
    op.drop_index("ix_papers_read_status", table_name="papers")
    op.drop_index("ix_papers_created_at", table_name="papers")

    op.drop_column("project_runs", "reviewer_model")
    op.drop_column("project_runs", "executor_model")
    op.drop_column("image_analyses", "image_path")
    op.drop_column("agent_sessions", "backend_id")
    op.drop_column("feishu_configs", "timeout_action")

    for column_name in (
        "model_image",
        "image_api_base_url",
        "image_api_key",
        "image_provider",
        "embedding_api_base_url",
        "embedding_api_key",
        "embedding_provider",
    ):
        op.drop_column("llm_provider_configs", column_name)

    op.drop_column("pipeline_runs", "decision_note")

    for column_name in ("total_cost_usd", "output_cost_usd", "input_cost_usd"):
        op.drop_column("prompt_traces", column_name)

    for column_name in (
        "retry_limit",
        "max_results_per_run",
        "date_filter_end",
        "date_filter_start",
        "date_filter_days",
        "enable_date_filter",
        "schedule_time_utc",
        "schedule_frequency",
        "default_folder_id",
        "from_year",
        "venue_names",
        "venue_type",
        "venue_tier",
        "priority_mode",
        "search_field",
        "source",
        "sort_by",
        "kind",
    ):
        op.drop_column("topic_subscriptions", column_name)

    op.drop_column("papers", "favorited")
