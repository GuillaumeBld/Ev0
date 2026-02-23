"""Add match_events table

Revision ID: 007
Revises: 006
Create Date: 2026-02-23

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "match_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fixture_id", "player_name", "event_type", "minute", name="uq_match_event"
        ),
    )
    op.create_index("ix_match_events_fixture_id", "match_events", ["fixture_id"])
    op.create_index("ix_match_events_event_type", "match_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_match_events_event_type")
    op.drop_index("ix_match_events_fixture_id")
    op.drop_table("match_events")
