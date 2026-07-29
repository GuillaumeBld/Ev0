"""Create player_career_seasons table (carriere joueur, source Transfermarkt).

Revision ID: 048
Revises: 047
Create Date: 2026-07-29
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "048"
down_revision: str | None = "047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_career_seasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_api_id", sa.Integer(), nullable=False),
        sa.Column("tm_id", sa.Integer(), nullable=True),
        sa.Column("season", sa.String(10), nullable=False),
        sa.Column("season_start_year", sa.Integer(), nullable=True),
        sa.Column("competition_code", sa.String(20), nullable=True),
        sa.Column("competition", sa.String(120), nullable=True),
        sa.Column("appearances", sa.Integer(), nullable=False),
        sa.Column("goals", sa.Integer(), nullable=False),
        sa.Column("assists", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["player_api_id"], ["bzz_players.api_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_api_id", "season", "competition_code", name="uq_player_career_season"
        ),
    )
    op.create_index(
        "ix_player_career_seasons_player_api_id",
        "player_career_seasons",
        ["player_api_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_player_career_seasons_player_api_id", "player_career_seasons")
    op.drop_table("player_career_seasons")
