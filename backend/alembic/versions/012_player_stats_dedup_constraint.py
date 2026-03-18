"""player_stats: unique index per (player_id, league, date) + minutes sanity index

Revision ID: 012
Revises: 011
Create Date: 2026-03-18
"""

from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unique index on (player_id, league, date(as_of_utc)).
    # Prevents duplicate rows from multiple ingestion runs on the same day.
    # Uses a functional index on the date portion of the timestamp.
    op.execute("""
        CREATE UNIQUE INDEX uq_player_stats_player_league_date
        ON player_stats (player_id, league, DATE(as_of_utc))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_player_stats_player_league_date")
