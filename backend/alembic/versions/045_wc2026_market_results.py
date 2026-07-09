"""wc2026_market_results : résultats réels des marchés outright (dead-heat).

Revision ID: 045
Revises: 044
Create Date: 2026-07-09
"""
import sqlalchemy as sa
from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wc2026_market_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("market", sa.String(30), nullable=False, index=True),
        sa.Column("player_name", sa.String(100), nullable=False),
        sa.Column("nation", sa.String(60), nullable=True),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("is_winner", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dead_heat", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("finalized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("wc2026_market_results")
