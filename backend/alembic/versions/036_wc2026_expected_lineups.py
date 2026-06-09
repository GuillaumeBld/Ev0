"""Create wc2026_expected_lineups and wc2026_expected_lineup_players tables.

Revision ID: 036
Revises: 035
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "036"
down_revision: str | None = "035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc2026_expected_lineups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nation", sa.String(60), nullable=False),
        sa.Column("context", sa.String(20), nullable=False),
        sa.Column("formation", sa.String(10), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nation", "context", name="uq_wc2026_lineup_nation_context"),
    )
    op.create_index("ix_wc2026_lineup_nation", "wc2026_expected_lineups", ["nation"])

    op.create_table(
        "wc2026_expected_lineup_players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lineup_id", sa.Integer(), sa.ForeignKey("wc2026_expected_lineups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("player_name", sa.String(100), nullable=False),
        sa.Column("position", sa.String(4), nullable=False),
        sa.Column("line_index", sa.SmallInteger(), nullable=False),
        sa.Column("slot_index", sa.SmallInteger(), nullable=False),
        sa.Column("is_starter", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("role", sa.String(20), nullable=False, server_default="starter"),
        sa.Column("expected_minutes", sa.SmallInteger(), nullable=False, server_default="85"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lineup_id", "player_name", name="uq_wc2026_lineup_player"),
    )
    op.create_index("ix_wc2026_lineup_players_lineup", "wc2026_expected_lineup_players", ["lineup_id"])


def downgrade() -> None:
    op.drop_index("ix_wc2026_lineup_players_lineup", table_name="wc2026_expected_lineup_players")
    op.drop_table("wc2026_expected_lineup_players")
    op.drop_index("ix_wc2026_lineup_nation", table_name="wc2026_expected_lineups")
    op.drop_table("wc2026_expected_lineups")
