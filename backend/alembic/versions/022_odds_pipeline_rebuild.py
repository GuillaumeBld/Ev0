"""Odds pipeline rebuild: add player_odds_snapshots + odds_scrape_state,
drop odds_snapshots + oddsportal_poll_state.

Revision ID: 022
Revises: 021
Create Date: 2026-04-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. player_odds_snapshots
    op.create_table(
        "player_odds_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("bookmaker", sa.String(30), nullable=False),
        sa.Column("market_type", sa.String(20), nullable=False),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("odds", sa.Float(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "fixture_id", "bookmaker", "market_type", "player_name",
            name="uq_player_odds_snapshot",
        ),
    )
    op.create_index("ix_player_odds_fixture", "player_odds_snapshots", ["fixture_id"])

    # 2. odds_scrape_state
    op.create_table(
        "odds_scrape_state",
        sa.Column(
            "fixture_id", sa.Integer(),
            sa.ForeignKey("fixtures.id"), primary_key=True,
        ),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scrape_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("betclic_ok", sa.Boolean(), server_default="false"),
        sa.Column("unibet_ok", sa.Boolean(), server_default="false"),
    )
    op.create_index(
        "ix_odds_scrape_state_next", "odds_scrape_state", ["next_scrape_at"]
    )

    # 3. Drop dead tables (ignore if already absent)
    op.execute("DROP TABLE IF EXISTS oddsportal_poll_state CASCADE")
    op.execute("DROP TABLE IF EXISTS odds_snapshots CASCADE")


def downgrade() -> None:
    op.drop_index("ix_odds_scrape_state_next", table_name="odds_scrape_state")
    op.drop_table("odds_scrape_state")
    op.drop_index("ix_player_odds_fixture", table_name="player_odds_snapshots")
    op.drop_table("player_odds_snapshots")
