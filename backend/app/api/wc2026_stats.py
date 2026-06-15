"""WC2026 player rankings — stats cumulées depuis bzz_player_match_stats."""
from __future__ import annotations

import unicodedata
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db

router = APIRouter(prefix="/wc2026/stats", tags=["wc2026"])

WC_LEAGUE_ID = 27


def _norm(name: str) -> str:
    n = unicodedata.normalize("NFKD", name.lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


class PlayerRanking(BaseModel):
    player_name: str
    nation: str | None
    flag_emoji: str | None
    position: str | None        # GK / DEF / MID / FWD (depuis squad)
    matches: int
    minutes: int
    goals: int
    assists: int
    xg: float
    xa: float
    shots: int
    shots_on_target: int
    finishing_delta: float      # goals - xg
    creation_delta: float       # assists - xa
    xg_per_90: float | None
    xa_per_90: float | None


@router.get("/rankings", response_model=list[PlayerRanking])
async def get_rankings(
    session: AsyncSession = Depends(get_db),
) -> list[PlayerRanking]:
    """Classement cumulatif des joueurs CDM 2026 depuis les stats Bzzoiro."""

    # ── 1. Aggregation depuis bzz_player_match_stats ──────────────────────────
    # DISTINCT ON (name, event) : Bzzoiro peut renvoyer des player_id différents
    # pour le même joueur entre deux syncs (ID historique vs ID CDM spécifique).
    # On garde le player_api_id le plus élevé (le plus récent) par (nom, match).
    rows: list[dict[str, Any]] = (
        await session.execute(
            text("""
                WITH deduped AS (
                    SELECT DISTINCT ON (p.name, ps.event_api_id)
                        p.name       AS player_name,
                        p.position   AS bzz_pos,
                        t.name       AS team_name,
                        ps.minutes_played,
                        ps.goals,
                        ps.goal_assist,
                        ps.expected_goals,
                        ps.expected_assists,
                        ps.total_shots,
                        ps.shots_on_target
                    FROM bzz_player_match_stats ps
                    JOIN bzz_players p ON p.api_id = ps.player_api_id
                    JOIN bzz_events  e ON e.api_id = ps.event_api_id
                    JOIN bzz_teams   t ON t.api_id = ps.team_api_id
                    WHERE e.league_api_id = :lid
                      AND COALESCE(ps.minutes_played, 1) > 0
                    ORDER BY p.name, ps.event_api_id, ps.player_api_id DESC
                )
                SELECT
                    player_name                              AS bzz_name,
                    bzz_pos,
                    team_name,
                    COUNT(*)                                 AS matches,
                    COALESCE(SUM(minutes_played), 0)         AS minutes,
                    COALESCE(SUM(goals), 0)                  AS goals,
                    COALESCE(SUM(goal_assist), 0)            AS assists,
                    COALESCE(SUM(expected_goals), 0.0)       AS xg,
                    COALESCE(SUM(expected_assists), 0.0)     AS xa,
                    COALESCE(SUM(total_shots), 0)            AS shots,
                    COALESCE(SUM(shots_on_target), 0)        AS shots_on_target
                FROM deduped
                GROUP BY player_name, bzz_pos, team_name
                ORDER BY xg DESC
            """),
            {"lid": WC_LEAGUE_ID},
        )
    ).mappings().all()

    # ── 2. Chargement du squad WC2026 pour enrichissement ─────────────────────
    squad_rows = (
        await session.execute(
            text("SELECT player_name, nation, flag_emoji, position FROM wc2026_squad_players")
        )
    ).mappings().all()

    squad: dict[str, dict] = {
        _norm(r["player_name"]): dict(r) for r in squad_rows
    }

    # ── 3. Construction des résultats ─────────────────────────────────────────
    result: list[PlayerRanking] = []
    for r in rows:
        bzz_name: str = r["bzz_name"]
        squad_entry = squad.get(_norm(bzz_name))

        nation     = squad_entry["nation"]     if squad_entry else None
        flag_emoji = squad_entry["flag_emoji"] if squad_entry else None
        position   = squad_entry["position"]   if squad_entry else _bzz_pos_to_squad(r["bzz_pos"])

        goals   = int(r["goals"])
        assists = int(r["assists"])
        xg      = round(float(r["xg"]), 2)
        xa      = round(float(r["xa"]), 2)
        minutes = int(r["minutes"])

        xg_per_90 = round(xg / minutes * 90, 2) if minutes >= 10 else None
        xa_per_90 = round(xa / minutes * 90, 2) if minutes >= 10 else None

        result.append(PlayerRanking(
            player_name=bzz_name,
            nation=nation,
            flag_emoji=flag_emoji,
            position=position,
            matches=int(r["matches"]),
            minutes=minutes,
            goals=goals,
            assists=assists,
            xg=xg,
            xa=xa,
            shots=int(r["shots"]),
            shots_on_target=int(r["shots_on_target"]),
            finishing_delta=round(goals - xg, 2),
            creation_delta=round(assists - xa, 2),
            xg_per_90=xg_per_90,
            xa_per_90=xa_per_90,
        ))

    return result


def _bzz_pos_to_squad(pos: str | None) -> str | None:
    """Convertit la position Bzzoiro (G/D/M/F) vers le format squad (GK/DEF/MID/FWD)."""
    return {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}.get(pos or "", None)
