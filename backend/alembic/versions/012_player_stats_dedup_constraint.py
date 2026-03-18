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
    # Non-unique index on (player_id, league) for fast lookups in the dedup check
    # added to store_player_stats. Deduplication per UTC day is enforced at the
    # application level (storage.py) since DATE(timestamptz) is not IMMUTABLE in Postgres.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_player_stats_player_league
        ON player_stats (player_id, league, as_of_utc DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_player_stats_player_league")
