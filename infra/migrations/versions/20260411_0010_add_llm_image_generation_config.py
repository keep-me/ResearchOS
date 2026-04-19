"""add image generation config fields to llm_provider_configs

Revision ID: 20260411_0010_add_llm_image_generation_config
Revises: 20260308_0009_add_date_filter_settings
Create Date: 2026-04-11

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260411_0010_add_llm_image_generation_config"
down_revision: Union[str, None] = "20260308_0009_add_date_filter_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect

    from packages.storage import models  # noqa: F401
    from packages.storage.db import Base

    bind = op.get_bind()
    Base.metadata.tables["llm_provider_configs"].create(bind=bind, checkfirst=True)
    inspector = inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns("llm_provider_configs")
    }

    if "image_provider" not in existing_columns:
        op.add_column(
            "llm_provider_configs",
            sa.Column("image_provider", sa.String(length=32), nullable=False, server_default=""),
        )
    if "image_api_key" not in existing_columns:
        op.add_column(
            "llm_provider_configs",
            sa.Column("image_api_key", sa.String(length=512), nullable=False, server_default=""),
        )
    if "image_api_base_url" not in existing_columns:
        op.add_column(
            "llm_provider_configs",
            sa.Column("image_api_base_url", sa.String(length=512), nullable=False, server_default=""),
        )
    if "model_image" not in existing_columns:
        op.add_column(
            "llm_provider_configs",
            sa.Column("model_image", sa.String(length=128), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("llm_provider_configs", "model_image")
    op.drop_column("llm_provider_configs", "image_api_base_url")
    op.drop_column("llm_provider_configs", "image_api_key")
    op.drop_column("llm_provider_configs", "image_provider")
