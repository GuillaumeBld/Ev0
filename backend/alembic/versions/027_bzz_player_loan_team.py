"""Add loan_team_name and loan_team_api_id to bzz_players.

Players on loan are stored with their parent club as current_team.
These columns track the active loan destination so the UI can show the
player under the correct squad.

Revision ID: 027
Revises: 026
Create Date: 2026-05-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bzz_players", sa.Column("loan_team_api_id", sa.Integer(), nullable=True))
    op.add_column("bzz_players", sa.Column("loan_team_name", sa.String(200), nullable=True))
    op.create_index("ix_bzz_players_loan_team_api_id", "bzz_players", ["loan_team_api_id"])


def downgrade() -> None:
    op.drop_index("ix_bzz_players_loan_team_api_id", table_name="bzz_players")
    op.drop_column("bzz_players", "loan_team_name")
    op.drop_column("bzz_players", "loan_team_api_id")
