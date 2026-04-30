"""add research kg tables for GraphRAG

Revision ID: 20260430_0013_add_research_kg
Revises: 20260414_0012_schema_reconciliation
Create Date: 2026-04-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = "20260430_0013_add_research_kg"
down_revision: str | None = "20260414_0012_schema_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_table_if_exists(table_name: str) -> None:
    inspector = inspect(op.get_bind())
    if table_name in inspector.get_table_names():
        op.drop_table(table_name)


def upgrade() -> None:
    from packages.storage import models  # noqa: F401
    from packages.storage.db import Base

    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    _drop_table_if_exists("research_kg_paper_states")
    _drop_table_if_exists("research_kg_edges")
    _drop_table_if_exists("research_kg_nodes")
