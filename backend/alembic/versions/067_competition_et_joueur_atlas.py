"""Identifiants Atlas sur les matchs et les cotes bookmakers.

Revision ID: 067
Revises: 066
Create Date: 2026-11-23

Deux colonnes nullables et indexees, sans backfill (sures sur table peuplee) :
`fixtures.competition_api_id` (id interne Bzzoiro de la ligue, cle du registre
Atlas) et `player_odds_snapshots.player_api_id` (`bzz_players.api_id`).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "067"
down_revision: str | None = "066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fixtures", sa.Column("competition_api_id", sa.Integer(), nullable=True))
    op.create_index("ix_fixtures_competition_api_id", "fixtures", ["competition_api_id"])
    op.add_column(
        "player_odds_snapshots", sa.Column("player_api_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_player_odds_snapshots_player_api_id", "player_odds_snapshots", ["player_api_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_player_odds_snapshots_player_api_id", table_name="player_odds_snapshots")
    op.drop_column("player_odds_snapshots", "player_api_id")
    op.drop_index("ix_fixtures_competition_api_id", table_name="fixtures")
    op.drop_column("fixtures", "competition_api_id")
