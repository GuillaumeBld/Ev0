"""Identifiant Atlas des joueurs d'une compo enregistrée.

Revision ID: 069
Revises: 068
Create Date: 2026-11-23

`team_lineup_players.player_api_id` (`bzz_players.api_id`), nullable et
indexée, sans backfill : une compo enregistrée sans identifiant est ignorée
par la résolution de compo (jamais rapprochée par nom).
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "069"
down_revision: str | None = "068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "team_lineup_players", sa.Column("player_api_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_team_lineup_players_player_api_id", "team_lineup_players", ["player_api_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_team_lineup_players_player_api_id", table_name="team_lineup_players")
    op.drop_column("team_lineup_players", "player_api_id")
