"""Create wc2026_team_advancement table.

Revision ID: 042
Revises: 369a4a81793a
Create Date: 2026-06-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "042"
down_revision: str | None = "369a4a81793a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc2026_team_advancement",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nation", sa.String(60), nullable=False),
        sa.Column("elo", sa.Float(), nullable=False),
        sa.Column("p_r32", sa.Float(), nullable=False),
        sa.Column("p_r16", sa.Float(), nullable=False),
        sa.Column("p_qf", sa.Float(), nullable=False),
        sa.Column("p_sf", sa.Float(), nullable=False),
        sa.Column("p_finalist", sa.Float(), nullable=False),
        sa.Column("p_winner", sa.Float(), nullable=False),
        sa.Column("e_games", sa.Float(), nullable=False),
        sa.Column("n_sim", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nation", name="uq_wc2026_team_advancement_nation"),
    )


def downgrade() -> None:
    op.drop_table("wc2026_team_advancement")
