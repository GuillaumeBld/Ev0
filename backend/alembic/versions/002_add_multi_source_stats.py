"""Add multi-source stats support.

Revision ID: 002_multi_source
Revises: 001_initial
Create Date: 2026-02-08 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new columns to players
    op.add_column("players", sa.Column("fbref_id", sa.String(100), nullable=True))
    op.add_column("players", sa.Column("understat_id", sa.String(100), nullable=True))
    op.add_column("players", sa.Column("league", sa.String(50), nullable=True))

    op.create_index("ix_players_fbref_id", "players", ["fbref_id"])
    op.create_index("ix_players_understat_id", "players", ["understat_id"])

    # Add source column to player_stats
    op.add_column(
        "player_stats", sa.Column("source", sa.String(20), nullable=False, server_default="fbref")
    )
    op.add_column("player_stats", sa.Column("npxg_per_90", sa.Float(), nullable=True))

    # Drop old constraint and add new one with source
    op.drop_constraint("uq_player_stats_snapshot", "player_stats", type_="unique")
    op.create_unique_constraint(
        "uq_player_stats_snapshot_source",
        "player_stats",
        ["player_id", "as_of_utc", "league", "season", "source"],
    )

    # Create teams table
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("fbref_id", sa.String(100), nullable=True),
        sa.Column("understat_id", sa.String(100), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("short_name", sa.String(50), nullable=True),
        sa.Column("league", sa.String(50), nullable=False),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id"),
    )
    op.create_index("ix_teams_name", "teams", ["name"])
    op.create_index("ix_teams_normalized_name", "teams", ["normalized_name"])


def downgrade() -> None:
    op.drop_index("ix_teams_normalized_name", "teams")
    op.drop_index("ix_teams_name", "teams")
    op.drop_table("teams")

    op.drop_constraint("uq_player_stats_snapshot_source", "player_stats", type_="unique")
    op.create_unique_constraint(
        "uq_player_stats_snapshot", "player_stats", ["player_id", "as_of_utc", "league", "season"]
    )
    op.drop_column("player_stats", "npxg_per_90")
    op.drop_column("player_stats", "source")

    op.drop_index("ix_players_understat_id", "players")
    op.drop_index("ix_players_fbref_id", "players")
    op.drop_column("players", "league")
    op.drop_column("players", "understat_id")
    op.drop_column("players", "fbref_id")
