"""Enrichit player_career_seasons au gabarit Bzzoiro complet + source.

Ajoute les colonnes de detail (tirs, passes, tacles, cartons, fautes,
hors-jeu) en NULLABLE : un champ absent chez Transfermarkt reste NULL,
jamais devine a 0 (distingue « 0 tir » de « inconnu »). Ajoute aussi
`source` (defaut 'transfermarkt') pour pouvoir, plus tard, distinguer
la provenance ligne par ligne comme sur player_selection_season_stats
(migration 062).

Les colonnes existantes (appearances, goals, assists, minutes) ne sont
pas touchees et restent NOT NULL.

Revision ID: 063
Revises: 062
Create Date: 2026-11-09
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "063"
down_revision: str | None = "062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("player_career_seasons", sa.Column("starts", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("total_shots", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("shots_on_target", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("total_pass", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("accurate_pass", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("total_tackle", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("won_tackle", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("yellow_card", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("red_card", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("fouls_committed", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("fouls_suffered", sa.Integer(), nullable=True))
    op.add_column("player_career_seasons", sa.Column("offsides", sa.Integer(), nullable=True))
    op.add_column(
        "player_career_seasons",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="transfermarkt"),
    )


def downgrade() -> None:
    op.drop_column("player_career_seasons", "source")
    op.drop_column("player_career_seasons", "offsides")
    op.drop_column("player_career_seasons", "fouls_suffered")
    op.drop_column("player_career_seasons", "fouls_committed")
    op.drop_column("player_career_seasons", "red_card")
    op.drop_column("player_career_seasons", "yellow_card")
    op.drop_column("player_career_seasons", "won_tackle")
    op.drop_column("player_career_seasons", "total_tackle")
    op.drop_column("player_career_seasons", "accurate_pass")
    op.drop_column("player_career_seasons", "total_pass")
    op.drop_column("player_career_seasons", "shots_on_target")
    op.drop_column("player_career_seasons", "total_shots")
    op.drop_column("player_career_seasons", "starts")
