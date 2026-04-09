"""Bzzoiro Sports Data API tables: bzz_leagues, bzz_teams, bzz_players,
bzz_events, bzz_player_match_stats, bzz_player_season_stats, bzz_predictions

Revision ID: 017
Revises: 016
Create Date: 2026-04-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. bzz_leagues
    op.create_table(
        "bzz_leagues",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("season_id", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_id", name="uq_bzz_leagues_api_id"),
    )
    op.create_index("ix_bzz_leagues_api_id", "bzz_leagues", ["api_id"])

    # 2. bzz_teams
    op.create_table(
        "bzz_teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(50), nullable=True),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_id", name="uq_bzz_teams_api_id"),
    )
    op.create_index("ix_bzz_teams_api_id", "bzz_teams", ["api_id"])

    # 3. bzz_players
    op.create_table(
        "bzz_players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(100), nullable=True),
        sa.Column("nationality", sa.String(100), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column("position", sa.String(1), nullable=True),
        sa.Column("market_value", sa.BigInteger(), nullable=True),
        sa.Column(
            "current_team_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "national_team_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_id", name="uq_bzz_players_api_id"),
    )
    op.create_index("ix_bzz_players_api_id", "bzz_players", ["api_id"])
    op.create_index("ix_bzz_players_current_team_api_id", "bzz_players", ["current_team_api_id"])
    op.create_index("ix_bzz_players_national_team_api_id", "bzz_players", ["national_team_api_id"])

    # 4. bzz_events
    op.create_table(
        "bzz_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_id", sa.Integer(), nullable=False),
        sa.Column(
            "league_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_leagues.api_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "home_team_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "away_team_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("period", sa.String(20), nullable=True),
        sa.Column("current_minute", sa.Integer(), nullable=True),
        sa.Column("round_number", sa.Integer(), nullable=True),
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("home_score_ht", sa.Integer(), nullable=True),
        sa.Column("away_score_ht", sa.Integer(), nullable=True),
        sa.Column("home_xg", sa.Float(), nullable=True),
        sa.Column("away_xg", sa.Float(), nullable=True),
        sa.Column("shotmap", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("incidents", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("momentum", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("average_positions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("lineups", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("odds_1x2", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("odds_over_under", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("odds_btts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_id", name="uq_bzz_events_api_id"),
    )
    op.create_index("ix_bzz_events_api_id", "bzz_events", ["api_id"])
    op.create_index("ix_bzz_events_league_api_id", "bzz_events", ["league_api_id"])
    op.create_index("ix_bzz_events_home_team_api_id", "bzz_events", ["home_team_api_id"])
    op.create_index("ix_bzz_events_away_team_api_id", "bzz_events", ["away_team_api_id"])
    op.create_index("ix_bzz_events_event_date", "bzz_events", ["event_date"])

    # 5. bzz_player_match_stats
    op.create_table(
        "bzz_player_match_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "player_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_players.api_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_events.api_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_teams.api_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_home", sa.Boolean(), nullable=True),
        sa.Column("minutes_played", sa.Integer(), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("touches", sa.Integer(), nullable=True),
        sa.Column("goals", sa.Integer(), nullable=True),
        sa.Column("goal_assist", sa.Integer(), nullable=True),
        sa.Column("expected_goals", sa.Float(), nullable=True),
        sa.Column("expected_assists", sa.Float(), nullable=True),
        sa.Column("total_shots", sa.Integer(), nullable=True),
        sa.Column("shots_on_target", sa.Integer(), nullable=True),
        sa.Column("total_pass", sa.Integer(), nullable=True),
        sa.Column("accurate_pass", sa.Integer(), nullable=True),
        sa.Column("key_pass", sa.Integer(), nullable=True),
        sa.Column("total_long_balls", sa.Integer(), nullable=True),
        sa.Column("accurate_long_balls", sa.Integer(), nullable=True),
        sa.Column("total_cross", sa.Integer(), nullable=True),
        sa.Column("accurate_cross", sa.Integer(), nullable=True),
        sa.Column("duel_won", sa.Integer(), nullable=True),
        sa.Column("duel_lost", sa.Integer(), nullable=True),
        sa.Column("aerial_won", sa.Integer(), nullable=True),
        sa.Column("aerial_lost", sa.Integer(), nullable=True),
        sa.Column("total_tackle", sa.Integer(), nullable=True),
        sa.Column("won_tackle", sa.Integer(), nullable=True),
        sa.Column("total_clearance", sa.Integer(), nullable=True),
        sa.Column("interception", sa.Integer(), nullable=True),
        sa.Column("ball_recovery", sa.Integer(), nullable=True),
        sa.Column("yellow_card", sa.Integer(), nullable=True),
        sa.Column("red_card", sa.Integer(), nullable=True),
        sa.Column("fouls", sa.Integer(), nullable=True),
        sa.Column("was_fouled", sa.Integer(), nullable=True),
        sa.Column("dispossessed", sa.Integer(), nullable=True),
        sa.Column("possession_lost", sa.Integer(), nullable=True),
        sa.Column("saves", sa.Integer(), nullable=True),
        sa.Column("goals_conceded", sa.Integer(), nullable=True),
        # Derived fields
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_api_id", "event_api_id", name="uq_bzz_player_match"),
    )
    op.create_index("ix_bzz_player_match_stats_player_api_id", "bzz_player_match_stats", ["player_api_id"])
    op.create_index("ix_bzz_player_match_stats_event_api_id", "bzz_player_match_stats", ["event_api_id"])
    op.create_index("ix_bzz_player_match_stats_team_api_id", "bzz_player_match_stats", ["team_api_id"])

    # 6. bzz_player_season_stats
    op.create_table(
        "bzz_player_season_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "player_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_players.api_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "league_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_leagues.api_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=True),
        # Totals (Integer)
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
        # Totals (Float)
        sa.Column("expected_goals", sa.Float(), nullable=True),
        sa.Column("expected_assists", sa.Float(), nullable=True),
        # Per-90 (Float nullable)
        sa.Column("xg_per_90", sa.Float(), nullable=True),
        sa.Column("xa_per_90", sa.Float(), nullable=True),
        sa.Column("shots_per_90", sa.Float(), nullable=True),
        sa.Column("shots_on_target_per_90", sa.Float(), nullable=True),
        sa.Column("key_pass_per_90", sa.Float(), nullable=True),
        sa.Column("accurate_cross_per_90", sa.Float(), nullable=True),
        sa.Column("recoveries_per_90", sa.Float(), nullable=True),
        sa.Column("tackles_per_90", sa.Float(), nullable=True),
        sa.Column("interceptions_per_90", sa.Float(), nullable=True),
        # Efficiency (Float nullable)
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
        # Profile (Float nullable)
        sa.Column("avg_minutes_per_match", sa.Float(), nullable=True),
        sa.Column("starts_pct", sa.Float(), nullable=True),
        # Form (nullable)
        sa.Column("form_xg_5", sa.Float(), nullable=True),
        sa.Column("form_rating_5", sa.Float(), nullable=True),
        sa.Column("form_goals_5", sa.Integer(), nullable=True),
        sa.Column("form_assists_5", sa.Integer(), nullable=True),
        sa.Column("rating_trend", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_api_id", "league_api_id", "season", name="uq_bzz_player_season"),
    )
    op.create_index("ix_bzz_player_season_stats_player_api_id", "bzz_player_season_stats", ["player_api_id"])
    op.create_index("ix_bzz_player_season_stats_league_api_id", "bzz_player_season_stats", ["league_api_id"])

    # 7. bzz_predictions
    op.create_table(
        "bzz_predictions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "event_api_id",
            sa.Integer(),
            sa.ForeignKey("bzz_events.api_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at_bzz", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prob_home_win", sa.Float(), nullable=True),
        sa.Column("prob_draw", sa.Float(), nullable=True),
        sa.Column("prob_away_win", sa.Float(), nullable=True),
        sa.Column("predicted_result", sa.String(1), nullable=True),
        sa.Column("expected_home_goals", sa.Float(), nullable=True),
        sa.Column("expected_away_goals", sa.Float(), nullable=True),
        sa.Column("prob_over_15", sa.Float(), nullable=True),
        sa.Column("prob_over_25", sa.Float(), nullable=True),
        sa.Column("prob_over_35", sa.Float(), nullable=True),
        sa.Column("prob_btts_yes", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(50), nullable=True),
        sa.Column("most_likely_score", sa.String(10), nullable=True),
        sa.Column("favorite", sa.String(1), nullable=True),
        sa.Column("favorite_prob", sa.Float(), nullable=True),
        sa.Column("favorite_recommend", sa.Boolean(), nullable=True),
        sa.Column("over_15_recommend", sa.Boolean(), nullable=True),
        sa.Column("over_25_recommend", sa.Boolean(), nullable=True),
        sa.Column("over_35_recommend", sa.Boolean(), nullable=True),
        sa.Column("btts_recommend", sa.Boolean(), nullable=True),
        sa.Column("winner_recommend", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_api_id", name="uq_bzz_predictions_event_api_id"),
    )
    op.create_index("ix_bzz_predictions_event_api_id", "bzz_predictions", ["event_api_id"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_index("ix_bzz_predictions_event_api_id", table_name="bzz_predictions")
    op.drop_table("bzz_predictions")

    op.drop_index("ix_bzz_player_season_stats_league_api_id", table_name="bzz_player_season_stats")
    op.drop_index("ix_bzz_player_season_stats_player_api_id", table_name="bzz_player_season_stats")
    op.drop_table("bzz_player_season_stats")

    op.drop_index("ix_bzz_player_match_stats_team_api_id", table_name="bzz_player_match_stats")
    op.drop_index("ix_bzz_player_match_stats_event_api_id", table_name="bzz_player_match_stats")
    op.drop_index("ix_bzz_player_match_stats_player_api_id", table_name="bzz_player_match_stats")
    op.drop_table("bzz_player_match_stats")

    op.drop_index("ix_bzz_events_event_date", table_name="bzz_events")
    op.drop_index("ix_bzz_events_away_team_api_id", table_name="bzz_events")
    op.drop_index("ix_bzz_events_home_team_api_id", table_name="bzz_events")
    op.drop_index("ix_bzz_events_league_api_id", table_name="bzz_events")
    op.drop_index("ix_bzz_events_api_id", table_name="bzz_events")
    op.drop_table("bzz_events")

    op.drop_index("ix_bzz_players_national_team_api_id", table_name="bzz_players")
    op.drop_index("ix_bzz_players_current_team_api_id", table_name="bzz_players")
    op.drop_index("ix_bzz_players_api_id", table_name="bzz_players")
    op.drop_table("bzz_players")

    op.drop_index("ix_bzz_teams_api_id", table_name="bzz_teams")
    op.drop_table("bzz_teams")

    op.drop_index("ix_bzz_leagues_api_id", table_name="bzz_leagues")
    op.drop_table("bzz_leagues")
