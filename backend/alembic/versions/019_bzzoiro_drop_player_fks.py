"""Drop FK constraints on bzz_players team columns — teams are cross-league refs.

Revision ID: 019
Revises: 018
Create Date: 2026-04-09
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop FK constraints so players from any team can be inserted
    op.drop_constraint(
        "bzz_players_current_team_api_id_fkey", "bzz_players", type_="foreignkey"
    )
    op.drop_constraint(
        "bzz_players_national_team_api_id_fkey", "bzz_players", type_="foreignkey"
    )


def downgrade() -> None:
    op.create_foreign_key(
        "bzz_players_current_team_api_id_fkey",
        "bzz_players",
        "bzz_teams",
        ["current_team_api_id"],
        ["api_id"],
    )
    op.create_foreign_key(
        "bzz_players_national_team_api_id_fkey",
        "bzz_players",
        "bzz_teams",
        ["national_team_api_id"],
        ["api_id"],
    )
