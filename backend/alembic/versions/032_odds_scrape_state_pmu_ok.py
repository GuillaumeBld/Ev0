"""Add pmu_ok column to odds_scrape_state.

Revision ID: 032
Revises: 031
Create Date: 2026-05-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "odds_scrape_state",
        sa.Column("pmu_ok", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("odds_scrape_state", "pmu_ok")
