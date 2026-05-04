"""track topic subscription run state

Revision ID: 20260504_0014_topic_run_state
Revises: 20260430_0013_add_research_kg
Create Date: 2026-05-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision: str = "20260504_0014_topic_run_state"
down_revision: str | None = "20260430_0013_add_research_kg"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_column(inspector, table_name: str, column_name: str) -> bool:
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    inspector = inspect(op.get_bind())
    if not _has_table(inspector, table_name):
        return
    if _has_column(inspector, table_name, column.name):
        return
    op.add_column(table_name, column)


def upgrade() -> None:
    for column in (
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_run_status", sa.String(length=32), nullable=True),
        sa.Column("last_run_count", sa.Integer(), nullable=True),
        sa.Column("last_run_error", sa.Text(), nullable=True),
    ):
        _add_column_if_missing("topic_subscriptions", column)

    bind = op.get_bind()
    inspector = inspect(bind)
    if not (
        _has_table(inspector, "topic_subscriptions") and _has_table(inspector, "collection_actions")
    ):
        return

    # Backfill from the newest existing successful collection action so the UI
    # keeps showing the same last-run value immediately after migration.
    bind.execute(
        text(
            """
            UPDATE topic_subscriptions
            SET
                last_run_at = (
                    SELECT ca.created_at
                    FROM collection_actions ca
                    WHERE ca.topic_id = topic_subscriptions.id
                    ORDER BY ca.created_at DESC
                    LIMIT 1
                ),
                last_run_status = CASE
                    WHEN (
                        SELECT ca.id
                        FROM collection_actions ca
                        WHERE ca.topic_id = topic_subscriptions.id
                        ORDER BY ca.created_at DESC
                        LIMIT 1
                    ) IS NULL THEN last_run_status
                    ELSE 'ok'
                END,
                last_run_count = (
                    SELECT ca.paper_count
                    FROM collection_actions ca
                    WHERE ca.topic_id = topic_subscriptions.id
                    ORDER BY ca.created_at DESC
                    LIMIT 1
                )
            WHERE last_run_at IS NULL
            """
        )
    )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if not _has_table(inspector, "topic_subscriptions"):
        return
    for column_name in (
        "last_run_error",
        "last_run_count",
        "last_run_status",
        "last_run_at",
    ):
        if _has_column(inspector, "topic_subscriptions", column_name):
            op.drop_column("topic_subscriptions", column_name)
