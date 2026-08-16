"""Add Transfermarkt squad sync support (canonical_teams link, squad_sync_runs, tm_absent_streak).

Revision ID: 049
Revises: 048
Create Date: 2026-08-15
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "049"
down_revision: str | None = "048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_teams",
        sa.Column("transfermarkt_club_id", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_canonical_teams_transfermarkt_club_id",
        "canonical_teams",
        ["transfermarkt_club_id"],
    )

    op.create_table(
        "squad_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mode", sa.String(10), nullable=True),
        sa.Column("clubs_total", sa.Integer(), nullable=True),
        sa.Column("clubs_ok", sa.Integer(), nullable=True),
        sa.Column("clubs_failed", sa.Integer(), nullable=True),
        sa.Column("players_updated", sa.Integer(), nullable=True),
        sa.Column("players_detached", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(10), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "bzz_players",
        sa.Column(
            "tm_absent_streak",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("bzz_players", "tm_absent_streak")
    op.drop_table("squad_sync_runs")
    op.drop_constraint("uq_canonical_teams_transfermarkt_club_id", "canonical_teams")
    op.drop_column("canonical_teams", "transfermarkt_club_id")
