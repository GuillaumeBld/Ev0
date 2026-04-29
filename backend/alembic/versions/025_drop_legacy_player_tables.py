"""Drop legacy player tables (players, player_stats, teams).

These tables were used by the Understat/Sofascore/FBref ingestion stack
which has been replaced entirely by Bzzoiro.

Revision ID: 025
Revises: 024
Create Date: 2026-04-28
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # player_stats references players.id — drop child first
    op.drop_table("player_stats")
    op.drop_table("players")
    op.drop_table("teams")


def downgrade() -> None:
    # Restore tables in reverse order (no data recovery — schema only)
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(100), unique=True, nullable=False),
        sa.Column("understat_id", sa.String(100), nullable=True),
        sa.Column("sofascore_id", sa.String(100), nullable=True),
        sa.Column("api_football_id", sa.String(100), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(50), nullable=True),
        sa.Column("league", sa.String(50), nullable=False),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(100), unique=True, nullable=False),
        sa.Column("understat_id", sa.String(100), nullable=True),
        sa.Column("sofascore_id", sa.String(100), nullable=True),
        sa.Column("api_football_id", sa.String(100), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("team", sa.String(100), nullable=True),
        sa.Column("team_id", sa.String(100), nullable=True),
        sa.Column("canonical_team_id", sa.Integer(), sa.ForeignKey("canonical_teams.id", ondelete="SET NULL"), nullable=True),
        sa.Column("league", sa.String(50), nullable=True),
        sa.Column("position", sa.String(50), nullable=True),
        sa.Column("is_striker", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("nationality", sa.String(100), nullable=True),
        sa.Column("birth_year", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "player_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("league", sa.String(50), nullable=False),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="average"),
        sa.Column("matches_played", sa.Integer(), server_default="0", nullable=False),
        sa.Column("minutes_played", sa.Integer(), server_default="0", nullable=False),
        sa.Column("starts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("goals", sa.Integer(), server_default="0", nullable=False),
        sa.Column("shots", sa.Integer(), server_default="0", nullable=False),
        sa.Column("shots_on_target", sa.Integer(), server_default="0", nullable=False),
        sa.Column("touches_attack_pen_area", sa.Integer(), server_default="0", nullable=False),
        sa.Column("xg", sa.Float(), server_default="0", nullable=False),
        sa.Column("npxg", sa.Float(), server_default="0", nullable=False),
        sa.Column("xgchain", sa.Float(), server_default="0", nullable=False),
        sa.Column("xgbuildup", sa.Float(), server_default="0", nullable=False),
        sa.Column("assists", sa.Integer(), server_default="0", nullable=False),
        sa.Column("xa", sa.Float(), server_default="0", nullable=False),
        sa.Column("key_passes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("big_chances_created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("accurate_crosses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_crosses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("through_balls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sofascore_rating", sa.Float(), nullable=True),
        sa.Column("xg_per_90", sa.Float(), nullable=True),
        sa.Column("npxg_per_90", sa.Float(), nullable=True),
        sa.Column("xa_per_90", sa.Float(), nullable=True),
        sa.Column("xgchain_per_90", sa.Float(), nullable=True),
        sa.Column("shots_on_target_per_90", sa.Float(), nullable=True),
        sa.Column("touches_attack_pen_area_per_90", sa.Float(), nullable=True),
        sa.Column("bcc_per_90", sa.Float(), nullable=True),
        sa.Column("accurate_crosses_per_90", sa.Float(), nullable=True),
        sa.Column("through_balls_per_90", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
