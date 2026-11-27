"""Temps de jeu mesure : bzz_player_season_stats.minutes_xg.

Revision ID: 072
Revises: 071
Create Date: 2026-11-27

Bzzoiro ne mesure pas le xG partout (34 competitions sur 75 cette saison,
107 matchs sur 162 de Conference League). Diviser le xG d'une saison par
TOUT le temps de jeu comptait ces matchs comme 0 xG : rythmes divises par 2
a 5 (Svanback 0,09 xG/90 pour 0,45 but/90 reel).

Un match est « mesure » si au moins un joueur y a un xG : dans un match
mesure, Bzzoiro laisse la case VIDE (pas 0) au joueur qui n'a pas tire, donc
le test se fait au niveau du match, jamais du joueur.

Backfill :
- ligne couverte par ses feuilles de match (>= 90 % de son temps de jeu) :
  temps passe dans les matchs mesures ;
- sinon (saisons sans feuilles, avant 2024-25) : tout le temps de jeu si la
  competition-saison a du xG, 0 sinon.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "072"
down_revision: str | None = "071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BACKFILL = """
WITH mesures AS (
    SELECT event_api_id FROM bzz_player_match_stats
    GROUP BY event_api_id HAVING count(expected_goals) > 0
),
feuilles AS (
    SELECT s.id,
           sum(ms.minutes_played) AS m_feuilles,
           sum(ms.minutes_played) FILTER (WHERE ms.event_api_id IN (SELECT event_api_id FROM mesures)) AS m_mesurees,
           sum(ms.goals) FILTER (WHERE ms.event_api_id IN (SELECT event_api_id FROM mesures)) AS b_mesures,
           sum(ms.goal_assist) FILTER (WHERE ms.event_api_id IN (SELECT event_api_id FROM mesures)) AS p_mesurees
    FROM bzz_player_season_stats s
    JOIN bzz_events e
      ON e.league_api_id = s.league_api_id
     AND e.status = 'finished'
     AND e.event_date >= make_date(left(s.season, 4)::int, 8, 1)
     AND e.event_date <  make_date(left(s.season, 4)::int + 1, 8, 1)
    JOIN bzz_player_match_stats ms
      ON ms.event_api_id = e.api_id AND ms.player_api_id = s.player_api_id
    WHERE s.season ~ '^[0-9]{4}-[0-9]{4}$'
    GROUP BY s.id
),
ligues_mesurees AS (
    SELECT league_api_id, season FROM bzz_player_season_stats
    GROUP BY league_api_id, season HAVING sum(coalesce(expected_goals, 0)) > 0
)
UPDATE bzz_player_season_stats s
SET minutes_xg = CASE
    WHEN f.m_feuilles IS NOT NULL AND f.m_feuilles >= 0.9 * coalesce(s.minutes_played, 0)
        THEN coalesce(f.m_mesurees, 0)
    WHEN lm.league_api_id IS NOT NULL THEN s.minutes_played
    ELSE 0
END,
goals_xg = CASE
    WHEN f.m_feuilles IS NOT NULL AND f.m_feuilles >= 0.9 * coalesce(s.minutes_played, 0)
        THEN coalesce(f.b_mesures, 0)
    WHEN lm.league_api_id IS NOT NULL THEN s.goals
    ELSE 0
END,
assists_xg = CASE
    WHEN f.m_feuilles IS NOT NULL AND f.m_feuilles >= 0.9 * coalesce(s.minutes_played, 0)
        THEN coalesce(f.p_mesurees, 0)
    WHEN lm.league_api_id IS NOT NULL THEN s.goal_assist
    ELSE 0
END
FROM bzz_player_season_stats s2
LEFT JOIN feuilles f ON f.id = s2.id
LEFT JOIN ligues_mesurees lm ON lm.league_api_id = s2.league_api_id AND lm.season = s2.season
WHERE s2.id = s.id
"""


def upgrade() -> None:
    op.add_column("bzz_player_season_stats", sa.Column("minutes_xg", sa.Integer(), nullable=True))
    op.add_column("bzz_player_season_stats", sa.Column("goals_xg", sa.Integer(), nullable=True))
    op.add_column("bzz_player_season_stats", sa.Column("assists_xg", sa.Integer(), nullable=True))
    op.execute(BACKFILL)


def downgrade() -> None:
    op.drop_column("bzz_player_season_stats", "assists_xg")
    op.drop_column("bzz_player_season_stats", "goals_xg")
    op.drop_column("bzz_player_season_stats", "minutes_xg")
