"""Create wc2026_squad_players table.

Revision ID: 033
Revises: 032
Create Date: 2026-06-08
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc2026_squad_players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nation", sa.String(60), nullable=False),
        sa.Column("group_letter", sa.String(1), nullable=False),
        sa.Column("player_name", sa.String(100), nullable=False),
        sa.Column("club", sa.String(100), nullable=True),
        sa.Column("position", sa.String(3), nullable=False),
        sa.Column("shirt_number", sa.SmallInteger(), nullable=True),
        sa.Column("flag_emoji", sa.String(10), nullable=True),
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
        sa.UniqueConstraint("nation", "player_name", name="uq_wc2026_nation_player"),
    )
    op.create_index("ix_wc2026_nation", "wc2026_squad_players", ["nation"])
    op.create_index("ix_wc2026_group", "wc2026_squad_players", ["group_letter"])


def downgrade() -> None:
    op.drop_index("ix_wc2026_group", table_name="wc2026_squad_players")
    op.drop_index("ix_wc2026_nation", table_name="wc2026_squad_players")
    op.drop_table("wc2026_squad_players")
