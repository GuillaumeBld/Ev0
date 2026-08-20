"""Add team_xg_estimates.phase (bibliotheque ouverture / closing).

Revision ID: 052
Revises: 051
Create Date: 2026-08-19
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "052"
down_revision: str | None = "051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # La table est vide depuis sa creation : server_default sans backfill.
    op.add_column(
        "team_xg_estimates",
        sa.Column("phase", sa.String(10), nullable=False, server_default="closing"),
    )
    op.create_unique_constraint(
        "uq_team_xg_estimates_fixture_phase", "team_xg_estimates", ["fixture_id", "phase"]
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_team_xg_estimates_fixture_phase", "team_xg_estimates", type_="unique"
    )
    op.drop_column("team_xg_estimates", "phase")
