"""Add current_team_name to bzz_players.

Revision ID: 024
Revises: 023
Create Date: 2026-04-22
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bzz_players",
        sa.Column("current_team_name", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bzz_players", "current_team_name")
