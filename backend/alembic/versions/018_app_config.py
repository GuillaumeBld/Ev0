"""Add app_config table for global key-value configuration.

Revision ID: 018
Revises: 017
Create Date: 2026-04-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.String(500), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_config")
