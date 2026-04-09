"""Drop all FK constraints on bzz_events and bzz_player_match_stats.

The Bzzoiro API returns data across all leagues globally; strict FKs
prevent ingesting events/stats for teams/leagues not yet in our tables.
We treat these as loose cross-references (stable IDs, no cascade needed).

Revision ID: 020
Revises: 019
Create Date: 2026-04-09
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # bzz_events FKs
    op.drop_constraint("bzz_events_league_api_id_fkey", "bzz_events", type_="foreignkey")
    op.drop_constraint("bzz_events_home_team_api_id_fkey", "bzz_events", type_="foreignkey")
    op.drop_constraint("bzz_events_away_team_api_id_fkey", "bzz_events", type_="foreignkey")

    # bzz_player_match_stats FKs
    op.drop_constraint(
        "bzz_player_match_stats_player_api_id_fkey",
        "bzz_player_match_stats",
        type_="foreignkey",
    )
    op.drop_constraint(
        "bzz_player_match_stats_event_api_id_fkey",
        "bzz_player_match_stats",
        type_="foreignkey",
    )
    op.drop_constraint(
        "bzz_player_match_stats_team_api_id_fkey",
        "bzz_player_match_stats",
        type_="foreignkey",
    )

    # bzz_player_season_stats FKs
    op.drop_constraint(
        "bzz_player_season_stats_player_api_id_fkey",
        "bzz_player_season_stats",
        type_="foreignkey",
    )
    op.drop_constraint(
        "bzz_player_season_stats_league_api_id_fkey",
        "bzz_player_season_stats",
        type_="foreignkey",
    )

    # bzz_predictions FK
    op.drop_constraint(
        "bzz_predictions_event_api_id_fkey",
        "bzz_predictions",
        type_="foreignkey",
    )

    # bzz_teams FK
    op.drop_constraint(
        "bzz_teams_league_api_id_fkey",
        "bzz_teams",
        type_="foreignkey",
    )


def downgrade() -> None:
    pass  # not worth re-adding; data integrity enforced at application level
