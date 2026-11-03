"""Expansion ~57-competitions (Task 1): canonical_teams.country, leagues
table seeded from LEAGUE_REGISTRY, data_coverage_runs table.

canonical_teams.country is nullable: existing club rows predate this column
and are not retro-filled here — that's a later data task, not this
migration's job. `leagues` mirrors LEAGUE_REGISTRY verbatim (seeded below,
one row per registry entry, keyed by the Bzzoiro internal id). `data_coverage_runs`
tracks per-league completeness checks (clubs/joueurs), one row per run —
see app/models/data_coverage.py for why there's no FK to leagues.id.

Revision ID: 060
Revises: 059
Create Date: 2026-11-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "060"
down_revision: str | None = "059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_teams",
        sa.Column("country", sa.String(length=100), nullable=True),
    )

    leagues_table = op.create_table(
        "leagues",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("tier", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "data_coverage_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("league_api_id", sa.Integer(), nullable=False),
        sa.Column("clubs_total", sa.Integer(), nullable=True),
        sa.Column("clubs_ok", sa.Integer(), nullable=True),
        sa.Column("clubs_incomplets", sa.Integer(), nullable=True),
        sa.Column("joueurs_sans_stats", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_data_coverage_runs_league_api_id",
        "data_coverage_runs",
        ["league_api_id"],
    )

    # Seed leagues from LEAGUE_REGISTRY — the registry is the single source
    # of truth; this migration never invents a row of its own.
    from app.ingestion.bzzoiro.league_registry import seed_rows

    op.bulk_insert(leagues_table, seed_rows())


def downgrade() -> None:
    op.drop_index("ix_data_coverage_runs_league_api_id", table_name="data_coverage_runs")
    op.drop_table("data_coverage_runs")
    op.drop_table("leagues")
    op.drop_column("canonical_teams", "country")
