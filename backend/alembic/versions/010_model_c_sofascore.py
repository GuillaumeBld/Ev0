"""Model C — add Sofascore fields to player_stats, players, teams

Revision ID: 010
Revises: 009
Create Date: 2026-03-09

Adds:
  - players.sofascore_id
  - teams.sofascore_id
  - player_stats: touches_attack_pen_area, xgchain, xgbuildup,
                  big_chances_created, accurate_crosses, total_crosses,
                  through_balls, sofascore_rating,
                  xgchain_per_90, shots_on_target_per_90,
                  touches_attack_pen_area_per_90, bcc_per_90,
                  accurate_crosses_per_90, through_balls_per_90
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── players ───────────────────────────────────────────────────
    op.add_column(
        "players",
        sa.Column("sofascore_id", sa.String(100), nullable=True),
    )
    op.create_index("ix_players_sofascore_id", "players", ["sofascore_id"])

    # ── teams ─────────────────────────────────────────────────────
    op.add_column(
        "teams",
        sa.Column("sofascore_id", sa.String(100), nullable=True),
    )

    # ── player_stats — Sofascore counting stats ───────────────────
    op.add_column(
        "player_stats",
        sa.Column("touches_attack_pen_area", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_stats",
        sa.Column("xgchain", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_stats",
        sa.Column("xgbuildup", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_stats",
        sa.Column("big_chances_created", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_stats",
        sa.Column("accurate_crosses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_stats",
        sa.Column("total_crosses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_stats",
        sa.Column("through_balls", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "player_stats",
        sa.Column("sofascore_rating", sa.Float(), nullable=True),
    )

    # ── player_stats — stored per-90 rates ───────────────────────
    op.add_column(
        "player_stats",
        sa.Column("xgchain_per_90", sa.Float(), nullable=True),
    )
    op.add_column(
        "player_stats",
        sa.Column("shots_on_target_per_90", sa.Float(), nullable=True),
    )
    op.add_column(
        "player_stats",
        sa.Column("touches_attack_pen_area_per_90", sa.Float(), nullable=True),
    )
    op.add_column(
        "player_stats",
        sa.Column("bcc_per_90", sa.Float(), nullable=True),
    )
    op.add_column(
        "player_stats",
        sa.Column("accurate_crosses_per_90", sa.Float(), nullable=True),
    )
    op.add_column(
        "player_stats",
        sa.Column("through_balls_per_90", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    # player_stats per-90
    op.drop_column("player_stats", "through_balls_per_90")
    op.drop_column("player_stats", "accurate_crosses_per_90")
    op.drop_column("player_stats", "bcc_per_90")
    op.drop_column("player_stats", "touches_attack_pen_area_per_90")
    op.drop_column("player_stats", "shots_on_target_per_90")
    op.drop_column("player_stats", "xgchain_per_90")
    # player_stats counting
    op.drop_column("player_stats", "sofascore_rating")
    op.drop_column("player_stats", "through_balls")
    op.drop_column("player_stats", "total_crosses")
    op.drop_column("player_stats", "accurate_crosses")
    op.drop_column("player_stats", "big_chances_created")
    op.drop_column("player_stats", "xgbuildup")
    op.drop_column("player_stats", "xgchain")
    op.drop_column("player_stats", "touches_attack_pen_area")
    # teams
    op.drop_column("teams", "sofascore_id")
    # players
    op.drop_index("ix_players_sofascore_id", table_name="players")
    op.drop_column("players", "sofascore_id")
