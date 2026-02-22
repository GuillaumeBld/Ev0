"""Add api_football_id to players and teams.

Revision ID: 003_add_api_football_id
Revises: 002_add_multi_source_stats
Create Date: 2026-02-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add api_football_id to players table
    op.add_column("players", sa.Column("api_football_id", sa.String(100), nullable=True))
    op.create_index(
        op.f("ix_players_api_football_id"), "players", ["api_football_id"], unique=False
    )

    # Add api_football_id to teams table
    op.add_column("teams", sa.Column("api_football_id", sa.String(100), nullable=True))


def downgrade() -> None:
    # Remove from teams
    op.drop_column("teams", "api_football_id")

    # Remove from players
    op.drop_index(op.f("ix_players_api_football_id"), table_name="players")
    op.drop_column("players", "api_football_id")
