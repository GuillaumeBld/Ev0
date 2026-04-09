"""Add internal_id to bzz_players.

The Bzzoiro /api/player-stats/ endpoint requires the player's internal DB id
(not the api_id) to filter by player. This column stores that internal id so
we can correctly query per-player stats.

Revision ID: 021
Revises: 020
Create Date: 2026-04-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bzz_players", sa.Column("internal_id", sa.Integer(), nullable=True))
    op.create_index("ix_bzz_players_internal_id", "bzz_players", ["internal_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_bzz_players_internal_id", table_name="bzz_players")
    op.drop_column("bzz_players", "internal_id")
