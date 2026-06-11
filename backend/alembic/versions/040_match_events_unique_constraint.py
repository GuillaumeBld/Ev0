"""match_events: deduplicate rows and add unique constraint.

Revision ID: 040_match_events_unique_constraint
Revises: 039_wc2026_player_pricing
Create Date: 2026-06-11
"""
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove duplicate rows, keeping the lowest id for each (fixture, player, type, minute)
    op.execute("""
        DELETE FROM match_events
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM match_events
            GROUP BY fixture_id, player_name, event_type, minute
        )
    """)

    op.create_unique_constraint(
        "uq_match_event",
        "match_events",
        ["fixture_id", "player_name", "event_type", "minute"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_match_event", "match_events", type_="unique")
