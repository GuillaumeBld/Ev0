"""Create wc2026_scouting_stats table.

Revision ID: 035
Revises: 034
Create Date: 2026-06-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc2026_scouting_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scouting_id", sa.Integer(), nullable=True),
        sa.Column("player_name", sa.String(100), nullable=False),
        sa.Column("normalized_name", sa.String(100), nullable=False),
        sa.Column("nation", sa.String(60), nullable=True),
        sa.Column("season", sa.String(9), nullable=False, server_default="2025-2026"),
        sa.Column("stats", JSONB(), nullable=False),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scouting_id", "season", name="uq_scouting_id_season"),
    )
    op.create_index("ix_scouting_normalized_name", "wc2026_scouting_stats", ["normalized_name"])
    op.create_index("ix_scouting_nation", "wc2026_scouting_stats", ["nation"])


def downgrade() -> None:
    op.drop_index("ix_scouting_nation", table_name="wc2026_scouting_stats")
    op.drop_index("ix_scouting_normalized_name", table_name="wc2026_scouting_stats")
    op.drop_table("wc2026_scouting_stats")
