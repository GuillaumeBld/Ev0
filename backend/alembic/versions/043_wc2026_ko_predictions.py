"""wc2026_ko_predictions — snapshot ELO + market pre-match for backtest

Revision ID: 043_wc2026_ko_predictions
Revises: 042_wc2026_team_advancement
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = "043_wc2026_ko_predictions"
down_revision = "042_wc2026_team_advancement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wc2026_ko_predictions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fixture_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("home_team", sa.String(), nullable=False),
        sa.Column("away_team", sa.String(), nullable=False),
        sa.Column("matchweek", sa.Integer(), nullable=True),
        sa.Column("kickoff_utc", sa.DateTime(timezone=True), nullable=True),
        # ELO snapshot at prediction time
        sa.Column("elo_home", sa.Float(), nullable=True),
        sa.Column("elo_away", sa.Float(), nullable=True),
        sa.Column("elo_prob_home", sa.Float(), nullable=True),   # P(home wins)
        # Market odds snapshot (Pinnacle, vig-removed)
        sa.Column("pin_odds_home", sa.Float(), nullable=True),   # raw Pinnacle home odds
        sa.Column("pin_odds_draw", sa.Float(), nullable=True),
        sa.Column("pin_odds_away", sa.Float(), nullable=True),
        sa.Column("pin_prob_home", sa.Float(), nullable=True),   # vig-removed P(home wins)
        # Best FR book
        sa.Column("fr_odds_home", sa.Float(), nullable=True),
        sa.Column("fr_odds_away", sa.Float(), nullable=True),
        # Actual result (filled after match)
        sa.Column("home_score", sa.Integer(), nullable=True),
        sa.Column("away_score", sa.Integer(), nullable=True),
        sa.Column("winner", sa.String(), nullable=True),         # 'home' | 'away' | None
        # Meta
        sa.Column("snapshot_utc", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("result_recorded_utc", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_wc2026_ko_predictions_fixture", "wc2026_ko_predictions", ["fixture_id"])


def downgrade() -> None:
    op.drop_index("ix_wc2026_ko_predictions_fixture", "wc2026_ko_predictions")
    op.drop_table("wc2026_ko_predictions")
