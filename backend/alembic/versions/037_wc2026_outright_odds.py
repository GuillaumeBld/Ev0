"""Create wc2026_outright_odds table.

Revision ID: 037
Revises: 036
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: str | None = "036"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc2026_outright_odds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nation", sa.String(60), nullable=True),
        sa.Column("player_name", sa.String(100), nullable=True),
        sa.Column("market_type", sa.String(30), nullable=False),
        sa.Column("bookmaker", sa.String(20), nullable=False),
        sa.Column("odds", sa.Float(), nullable=False),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nation", "player_name", "market_type", "bookmaker",
            name="uq_wc2026_outright",
        ),
    )
    op.create_index("ix_wc2026_outright_nation", "wc2026_outright_odds", ["nation"])
    op.create_index("ix_wc2026_outright_market", "wc2026_outright_odds", ["market_type"])


def downgrade() -> None:
    op.drop_index("ix_wc2026_outright_market", table_name="wc2026_outright_odds")
    op.drop_index("ix_wc2026_outright_nation", table_name="wc2026_outright_odds")
    op.drop_table("wc2026_outright_odds")
