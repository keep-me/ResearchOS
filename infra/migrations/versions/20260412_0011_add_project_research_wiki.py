"""add project research wiki tables

Revision ID: 20260412_0011_add_project_research_wiki
Revises: 20260411_0010_add_llm_image_generation_config
Create Date: 2026-04-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260412_0011_add_project_research_wiki"
down_revision: Union[str, None] = "20260411_0010_add_llm_image_generation_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from packages.storage import models  # noqa: F401
    from packages.storage.db import Base

    bind = op.get_bind()
    for table_name in (
        "projects",
        "project_deployment_targets",
        "project_runs",
    ):
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)

    op.create_table(
        "project_research_wiki_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("node_key", sa.String(length=256), nullable=False),
        sa.Column("node_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_paper_id", sa.String(length=36), nullable=True),
        sa.Column("source_run_id", sa.String(length=36), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_paper_id"], ["papers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_run_id"], ["project_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "node_key", name="uq_project_research_wiki_node_key"),
    )
    op.create_index(
        "ix_project_research_wiki_nodes_project_id",
        "project_research_wiki_nodes",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_research_wiki_nodes_source_paper_id",
        "project_research_wiki_nodes",
        ["source_paper_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_research_wiki_nodes_source_run_id",
        "project_research_wiki_nodes",
        ["source_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_research_wiki_nodes_type_status",
        "project_research_wiki_nodes",
        ["project_id", "node_type", "status"],
        unique=False,
    )

    op.create_table(
        "project_research_wiki_edges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_node_id", sa.String(length=36), nullable=False),
        sa.Column("target_node_id", sa.String(length=36), nullable=False),
        sa.Column("edge_type", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_node_id"],
            ["project_research_wiki_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id"],
            ["project_research_wiki_nodes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "source_node_id",
            "target_node_id",
            "edge_type",
            name="uq_project_research_wiki_edge",
        ),
    )
    op.create_index(
        "ix_project_research_wiki_edges_project_id",
        "project_research_wiki_edges",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_research_wiki_edges_source_node_id",
        "project_research_wiki_edges",
        ["source_node_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_research_wiki_edges_target_node_id",
        "project_research_wiki_edges",
        ["target_node_id"],
        unique=False,
    )
    op.create_index(
        "ix_project_research_wiki_edges_type",
        "project_research_wiki_edges",
        ["project_id", "edge_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_project_research_wiki_edges_type", table_name="project_research_wiki_edges")
    op.drop_index("ix_project_research_wiki_edges_target_node_id", table_name="project_research_wiki_edges")
    op.drop_index("ix_project_research_wiki_edges_source_node_id", table_name="project_research_wiki_edges")
    op.drop_index("ix_project_research_wiki_edges_project_id", table_name="project_research_wiki_edges")
    op.drop_table("project_research_wiki_edges")

    op.drop_index("ix_project_research_wiki_nodes_type_status", table_name="project_research_wiki_nodes")
    op.drop_index("ix_project_research_wiki_nodes_source_run_id", table_name="project_research_wiki_nodes")
    op.drop_index("ix_project_research_wiki_nodes_source_paper_id", table_name="project_research_wiki_nodes")
    op.drop_index("ix_project_research_wiki_nodes_project_id", table_name="project_research_wiki_nodes")
    op.drop_table("project_research_wiki_nodes")
