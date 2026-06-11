"""Add status tracking columns to wc2026_outright_odds.

Revision ID: 041
Revises: 040
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "041"
down_revision: str | None = "040"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wc2026_outright_odds",
        sa.Column("is_active", sa.Boolean(), server_default="TRUE", nullable=False),
    )
    op.add_column(
        "wc2026_outright_odds",
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "wc2026_outright_odds",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "wc2026_outright_odds",
        sa.Column(
            "odds_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "wc2026_outright_odds",
        sa.Column("republished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_wc2026_outright_is_active", "wc2026_outright_odds", ["is_active"]
    )


def downgrade() -> None:
    op.drop_index("ix_wc2026_outright_is_active", table_name="wc2026_outright_odds")
    op.drop_column("wc2026_outright_odds", "republished_at")
    op.drop_column("wc2026_outright_odds", "odds_changed_at")
    op.drop_column("wc2026_outright_odds", "last_seen_at")
    op.drop_column("wc2026_outright_odds", "first_seen_at")
    op.drop_column("wc2026_outright_odds", "is_active")
