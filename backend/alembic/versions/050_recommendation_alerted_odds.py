"""Add recommendations.alerted_odds (memoire du dernier plus haut notifie).

Revision ID: 050
Revises: 049
Create Date: 2026-08-17
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "050"
down_revision: str | None = "049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recommendations",
        sa.Column("alerted_odds", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recommendations", "alerted_odds")
