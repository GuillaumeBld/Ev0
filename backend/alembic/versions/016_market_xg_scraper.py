"""Market xG scraper: extend match_odds_snapshots, add poll_state and team_xg_estimates

Revision ID: 016
Revises: 015
Create Date: 2026-04-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Extend match_odds_snapshots with scraper tracking columns
    op.add_column(
        "match_odds_snapshots",
        sa.Column("source", sa.String(20), nullable=True),
    )
    op.add_column(
        "match_odds_snapshots",
        sa.Column("source_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "match_odds_snapshots",
        sa.Column("parse_version", sa.String(20), nullable=True),
    )
    op.add_column(
        "match_odds_snapshots",
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 2. Add xg_source to recommendations
    op.add_column(
        "recommendations",
        sa.Column("xg_source", sa.String(20), nullable=True),
    )

    # 3. Create oddsportal_poll_state
    op.create_table(
        "oddsportal_poll_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "fixture_id",
            sa.Integer(),
            sa.ForeignKey("fixtures.id"),
            nullable=False,
        ),
        sa.Column("oddsportal_url", sa.String(500), nullable=False),
        sa.Column("betclic_url", sa.String(500), nullable=True),
        sa.Column("unibet_url", sa.String(500), nullable=True),
        sa.Column("next_due_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_scraped_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stopped", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("stopped_reason", sa.String(50), nullable=True),
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
        sa.UniqueConstraint("fixture_id", name="uq_poll_state_fixture"),
    )
    op.create_index("ix_poll_state_fixture_id", "oddsportal_poll_state", ["fixture_id"])
    op.create_index(
        "ix_poll_state_next_due", "oddsportal_poll_state", ["next_due_at_utc"]
    )

    # 4. Create team_xg_estimates
    op.create_table(
        "team_xg_estimates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "fixture_id",
            sa.Integer(),
            sa.ForeignKey("fixtures.id"),
            nullable=False,
        ),
        sa.Column("as_of_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lambda_home", sa.Float(), nullable=False),
        sa.Column("lambda_away", sa.Float(), nullable=False),
        sa.Column("fit_residual", sa.Float(), nullable=False),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("data_source", sa.String(20), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "input_snapshot_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_xg_fixture_id",
        "team_xg_estimates",
        ["fixture_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_team_xg_fixture_id", table_name="team_xg_estimates")
    op.drop_table("team_xg_estimates")
    op.drop_index("ix_poll_state_next_due", table_name="oddsportal_poll_state")
    op.drop_index("ix_poll_state_fixture_id", table_name="oddsportal_poll_state")
    op.drop_table("oddsportal_poll_state")
    op.drop_column("recommendations", "xg_source")
    op.drop_column("match_odds_snapshots", "fallback_used")
    op.drop_column("match_odds_snapshots", "parse_version")
    op.drop_column("match_odds_snapshots", "source_url")
    op.drop_column("match_odds_snapshots", "source")
