"""Add home_bzz_team_id and away_bzz_team_id to fixtures.

Links each fixture directly to bzz_teams via integer ID so the
pricing engine can find players without fuzzy name matching.

Revision ID: 028
Revises: 027
Create Date: 2026-05-19
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fixtures", sa.Column("home_bzz_team_id", sa.Integer(), nullable=True))
    op.add_column("fixtures", sa.Column("away_bzz_team_id", sa.Integer(), nullable=True))
    op.create_index("ix_fixtures_home_bzz_team_id", "fixtures", ["home_bzz_team_id"])
    op.create_index("ix_fixtures_away_bzz_team_id", "fixtures", ["away_bzz_team_id"])


def downgrade() -> None:
    op.drop_index("ix_fixtures_away_bzz_team_id", table_name="fixtures")
    op.drop_index("ix_fixtures_home_bzz_team_id", table_name="fixtures")
    op.drop_column("fixtures", "away_bzz_team_id")
    op.drop_column("fixtures", "home_bzz_team_id")
