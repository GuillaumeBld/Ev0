"""Table isolee player_selection_season_stats (selections nationales).

Table dediee, separee de bzz_player_season_stats, pour les stats de
selection nationale. La colonne `source` (defaut 'bzzoiro') permet au
complement Transfermarkt (Temps 2) d'etre ajoute ou retire sans toucher
aux donnees de club.

Revision ID: 062
Revises: 061
Create Date: 2026-11-06
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "062"
down_revision: str | None = "061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_selection_season_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_api_id", sa.Integer(), nullable=False),
        sa.Column("national_team_api_id", sa.Integer(), nullable=False),
        sa.Column("league_api_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="bzzoiro"),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=True),
        # --- Memes colonnes de stats que bzz_player_season_stats ---
        sa.Column("matches_played", sa.Integer(), nullable=True),
        sa.Column("minutes_played", sa.Integer(), nullable=True),
        sa.Column("starts", sa.Integer(), nullable=True),
        sa.Column("goals", sa.Integer(), nullable=True),
        sa.Column("goal_assist", sa.Integer(), nullable=True),
        sa.Column("total_shots", sa.Integer(), nullable=True),
        sa.Column("shots_on_target", sa.Integer(), nullable=True),
        sa.Column("key_pass", sa.Integer(), nullable=True),
        sa.Column("total_cross", sa.Integer(), nullable=True),
        sa.Column("accurate_cross", sa.Integer(), nullable=True),
        sa.Column("total_pass", sa.Integer(), nullable=True),
        sa.Column("accurate_pass", sa.Integer(), nullable=True),
        sa.Column("total_long_balls", sa.Integer(), nullable=True),
        sa.Column("accurate_long_balls", sa.Integer(), nullable=True),
        sa.Column("duel_won", sa.Integer(), nullable=True),
        sa.Column("duel_lost", sa.Integer(), nullable=True),
        sa.Column("aerial_won", sa.Integer(), nullable=True),
        sa.Column("aerial_lost", sa.Integer(), nullable=True),
        sa.Column("total_tackle", sa.Integer(), nullable=True),
        sa.Column("won_tackle", sa.Integer(), nullable=True),
        sa.Column("interception", sa.Integer(), nullable=True),
        sa.Column("ball_recovery", sa.Integer(), nullable=True),
        sa.Column("yellow_card", sa.Integer(), nullable=True),
        sa.Column("red_card", sa.Integer(), nullable=True),
        sa.Column("saves", sa.Integer(), nullable=True),
        sa.Column("expected_goals", sa.Float(), nullable=True),
        sa.Column("expected_assists", sa.Float(), nullable=True),
        sa.Column("xg_per_90", sa.Float(), nullable=True),
        sa.Column("xa_per_90", sa.Float(), nullable=True),
        sa.Column("shots_per_90", sa.Float(), nullable=True),
        sa.Column("shots_on_target_per_90", sa.Float(), nullable=True),
        sa.Column("key_pass_per_90", sa.Float(), nullable=True),
        sa.Column("accurate_cross_per_90", sa.Float(), nullable=True),
        sa.Column("recoveries_per_90", sa.Float(), nullable=True),
        sa.Column("tackles_per_90", sa.Float(), nullable=True),
        sa.Column("interceptions_per_90", sa.Float(), nullable=True),
        sa.Column("shot_accuracy", sa.Float(), nullable=True),
        sa.Column("xg_per_shot", sa.Float(), nullable=True),
        sa.Column("finishing_delta", sa.Float(), nullable=True),
        sa.Column("xa_delta", sa.Float(), nullable=True),
        sa.Column("pass_completion", sa.Float(), nullable=True),
        sa.Column("long_ball_accuracy", sa.Float(), nullable=True),
        sa.Column("cross_accuracy", sa.Float(), nullable=True),
        sa.Column("duel_win_rate", sa.Float(), nullable=True),
        sa.Column("aerial_win_rate", sa.Float(), nullable=True),
        sa.Column("tackle_success_rate", sa.Float(), nullable=True),
        sa.Column("avg_rating", sa.Float(), nullable=True),
        sa.Column("avg_minutes_per_match", sa.Float(), nullable=True),
        sa.Column("starts_pct", sa.Float(), nullable=True),
        sa.Column("form_xg_5", sa.Float(), nullable=True),
        sa.Column("form_rating_5", sa.Float(), nullable=True),
        sa.Column("form_goals_5", sa.Integer(), nullable=True),
        sa.Column("form_assists_5", sa.Integer(), nullable=True),
        sa.Column("rating_trend", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_api_id", "national_team_api_id", "league_api_id", "season", "source",
            name="uq_selection_player_team_league_season_source",
        ),
    )
    op.create_index(
        "ix_player_selection_season_stats_player_api_id",
        "player_selection_season_stats",
        ["player_api_id"],
    )
    op.create_index(
        "ix_player_selection_season_stats_national_team_api_id",
        "player_selection_season_stats",
        ["national_team_api_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_player_selection_season_stats_national_team_api_id",
        table_name="player_selection_season_stats",
    )
    op.drop_index(
        "ix_player_selection_season_stats_player_api_id",
        table_name="player_selection_season_stats",
    )
    op.drop_table("player_selection_season_stats")
