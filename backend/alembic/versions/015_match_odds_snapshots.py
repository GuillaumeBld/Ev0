"""match_odds_snapshots table for market-implied xG

Revision ID: 015
Revises: 014
Create Date: 2026-04-02
"""

import sqlalchemy as sa  # noqa: I001
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_odds_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fixture_id",
            sa.Integer(),
            sa.ForeignKey("fixtures.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("bookmaker", sa.String(50), nullable=False, index=True),
        sa.Column("market_type", sa.String(50), nullable=False, index=True),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("odds", sa.Float(), nullable=False),
        sa.Column(
            "snapshot_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
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
        sa.UniqueConstraint(
            "fixture_id",
            "bookmaker",
            "market_type",
            "outcome",
            "snapshot_utc",
            name="uq_match_odds_snapshot",
        ),
    )


def downgrade() -> None:
    op.drop_table("match_odds_snapshots")
