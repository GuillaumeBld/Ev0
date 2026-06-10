"""Create wc2026_player_pricing table.

Revision ID: 039
Revises: 038
Create Date: 2026-06-10
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc2026_player_pricing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nation", sa.String(60), nullable=False),
        sa.Column("player_name", sa.String(100), nullable=False),
        sa.Column("position", sa.String(10), nullable=True),
        sa.Column("lambda_goals", sa.Float(), nullable=False),
        sa.Column("lambda_assists", sa.Float(), nullable=False),
        sa.Column("p_1g", sa.Float(), nullable=True),
        sa.Column("p_2g", sa.Float(), nullable=True),
        sa.Column("p_3g", sa.Float(), nullable=True),
        sa.Column("p_4g", sa.Float(), nullable=True),
        sa.Column("fair_1g", sa.Float(), nullable=True),
        sa.Column("fair_2g", sa.Float(), nullable=True),
        sa.Column("fair_3g", sa.Float(), nullable=True),
        sa.Column("fair_4g", sa.Float(), nullable=True),
        sa.Column("p_1a", sa.Float(), nullable=True),
        sa.Column("p_2a", sa.Float(), nullable=True),
        sa.Column("p_3a", sa.Float(), nullable=True),
        sa.Column("fair_1a", sa.Float(), nullable=True),
        sa.Column("fair_2a", sa.Float(), nullable=True),
        sa.Column("fair_3a", sa.Float(), nullable=True),
        sa.Column("p_top_scorer", sa.Float(), nullable=True),
        sa.Column("p_top_assister", sa.Float(), nullable=True),
        sa.Column("fair_top_scorer", sa.Float(), nullable=True),
        sa.Column("fair_top_assister", sa.Float(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wc2026_player_pricing_nation", "wc2026_player_pricing", ["nation"])


def downgrade() -> None:
    op.drop_index("ix_wc2026_player_pricing_nation", "wc2026_player_pricing")
    op.drop_table("wc2026_player_pricing")
