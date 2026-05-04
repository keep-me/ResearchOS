"""add research kg tables for GraphRAG

Revision ID: 20260430_0013_add_research_kg
Revises: 20260414_0012_schema_reconciliation
Create Date: 2026-04-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "20260430_0013_add_research_kg"
down_revision: str | None = "20260414_0012_schema_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(inspector, table_name: str) -> bool:
    return table_name in inspector.get_table_names()


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


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


def _drop_table_if_exists(table_name: str) -> None:
    inspector = inspect(op.get_bind())
    if _has_table(inspector, table_name):
        op.drop_table(table_name)


def upgrade() -> None:
    inspector = inspect(op.get_bind())

    if not _has_table(inspector, "research_kg_nodes"):
        op.create_table(
            "research_kg_nodes",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("node_type", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=512), nullable=False),
            sa.Column("normalized_name", sa.String(length=512), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "node_type",
                "normalized_name",
                name="uq_research_kg_node_type_name",
            ),
        )

    if not _has_table(inspector, "research_kg_edges"):
        op.create_table(
            "research_kg_edges",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("source_node_id", sa.String(length=36), nullable=False),
            sa.Column("target_node_id", sa.String(length=36), nullable=False),
            sa.Column("edge_type", sa.String(length=64), nullable=False),
            sa.Column("evidence", sa.Text(), nullable=False),
            sa.Column("weight", sa.Float(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["source_node_id"],
                ["research_kg_nodes.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["target_node_id"],
                ["research_kg_nodes.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_node_id",
                "target_node_id",
                "edge_type",
                name="uq_research_kg_edge",
            ),
        )

    if not _has_table(inspector, "research_kg_paper_states"):
        op.create_table(
            "research_kg_paper_states",
            sa.Column("paper_id", sa.String(length=36), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("node_count", sa.Integer(), nullable=False),
            sa.Column("edge_count", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("built_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("paper_id"),
        )

    _create_index_if_missing(
        "research_kg_nodes",
        "ix_research_kg_nodes_created_at",
        ["created_at"],
    )
    _create_index_if_missing(
        "research_kg_nodes",
        "ix_research_kg_nodes_node_type",
        ["node_type"],
    )
    _create_index_if_missing(
        "research_kg_nodes",
        "ix_research_kg_nodes_type_name",
        ["node_type", "normalized_name"],
    )
    _create_index_if_missing(
        "research_kg_nodes",
        "ix_research_kg_nodes_updated_at",
        ["updated_at"],
    )
    _create_index_if_missing(
        "research_kg_edges",
        "ix_research_kg_edges_created_at",
        ["created_at"],
    )
    _create_index_if_missing(
        "research_kg_edges",
        "ix_research_kg_edges_source_node_id",
        ["source_node_id"],
    )
    _create_index_if_missing(
        "research_kg_edges",
        "ix_research_kg_edges_source_target",
        ["source_node_id", "target_node_id"],
    )
    _create_index_if_missing(
        "research_kg_edges",
        "ix_research_kg_edges_target_node_id",
        ["target_node_id"],
    )
    _create_index_if_missing(
        "research_kg_edges",
        "ix_research_kg_edges_type",
        ["edge_type"],
    )
    _create_index_if_missing(
        "research_kg_edges",
        "ix_research_kg_edges_updated_at",
        ["updated_at"],
    )
    _create_index_if_missing(
        "research_kg_paper_states",
        "ix_research_kg_paper_states_built_at",
        ["built_at"],
    )
    _create_index_if_missing(
        "research_kg_paper_states",
        "ix_research_kg_paper_states_status",
        ["status"],
    )
    _create_index_if_missing(
        "research_kg_paper_states",
        "ix_research_kg_paper_states_updated_at",
        ["updated_at"],
    )


def downgrade() -> None:
    _drop_table_if_exists("research_kg_paper_states")
    _drop_table_if_exists("research_kg_edges")
    _drop_table_if_exists("research_kg_nodes")
