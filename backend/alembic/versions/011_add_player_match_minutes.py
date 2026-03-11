"""Add player_match_minutes table

Revision ID: 011
Revises: 010
Create Date: 2026-03-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_match_minutes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("minutes_played", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fixture_id", "player_name", name="uq_player_match_minutes"),
    )
    op.create_index("ix_player_match_minutes_fixture_id", "player_match_minutes", ["fixture_id"])


def downgrade() -> None:
    op.drop_index("ix_player_match_minutes_fixture_id")
    op.drop_table("player_match_minutes")
