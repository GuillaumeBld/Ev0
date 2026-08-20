"""Add team_xg_estimates.odds (cotes brutes ayant produit le lambda).

Revision ID: 053
Revises: 052
Create Date: 2026-08-20
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "053"
down_revision: str | None = "052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable : les lignes ecrites avant cette migration en portent un tant
    # que le rattrapage n'a pas tourne, et une estimation dont les snapshots
    # ont disparu n'est pas rattrapable.
    op.add_column(
        "team_xg_estimates",
        sa.Column("odds", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("team_xg_estimates", "odds")
