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

# xG attendus pour l'ensemble du tournoi, par bookmaker (source externe).
# Clés = noms de nations tels qu'ils apparaissent dans wc2026_squad_players.
BM_XG: dict[str, float] = {
    "Espagne":            13.03,
    "Brésil":             12.33,
    "Allemagne":          11.78,
    "Angleterre":         11.74,
    "France":             10.90,
    "Argentine":          10.83,
    "Portugal":           10.23,
    "Belgique":            9.56,
    "Suisse":              8.27,
    "Pays-Bas":            7.97,
    "Colombie":            7.69,
    "Norvège":             7.15,
    "Mexique":             7.08,
    "Équateur":            6.63,
    "Uruguay":             6.51,
    "Canada":              6.23,
    "États-Unis":          6.20,
    "Croatie":             6.06,
    "Maroc":               5.98,
    "Côte d'Ivoire":       5.85,
    "Autriche":            5.78,
    "Turquie":             5.73,
    "Japon":               5.38,
    "Sénégal":             5.31,
    "Égypte":              4.96,
    "Écosse":              4.64,
    "Corée du Sud":        4.48,
    "République Tchèque":  4.29,
    "Suède":               4.21,
    "Bosnie-Herzégovine":  4.15,
    "Algérie":             4.08,
    "Paraguay":            3.95,
    "Iran":                3.80,
    "Ghana":               3.22,
    "Australie":           3.19,
    "RD Congo":            2.94,
    "Panama":              2.94,
    "Nouvelle-Zélande":    2.74,
    "Afrique du Sud":      2.64,
    "Ouzbékistan":         2.62,
    "Tunisie":             2.56,
    "Cap-Vert":            2.51,
    "Arabie Saoudite":     2.35,
    "Curaçao":             2.17,
    "Haïti":               2.08,
    "Jordanie":            2.05,
    "Qatar":               1.99,
    "Irak":                1.53,
}


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
    xg_tournoi: float | None    # xG BM attendus pour tout le tournoi (équipe)
    xg_left: float | None       # xg_tournoi - xG cumulé équipe en tournoi
    xa_left: float | None       # (xg_tournoi × 0.7) - xA cumulé équipe en tournoi
    team_xg_share: float | None # % du xG tournoi généré par ce joueur


@router.get("/rankings", response_model=list[PlayerRanking])
async def get_rankings(
    session: AsyncSession = Depends(get_db),
) -> list[PlayerRanking]:
    """Classement cumulatif des joueurs CDM 2026 depuis les stats Bzzoiro."""

    # ── 1. Aggregation depuis bzz_player_match_stats ──────────────────────────
    # DISTINCT ON (name, event) : Bzzoiro peut renvoyer des player_id différents
    # pour le même joueur entre deux syncs (ID historique vs ID CDM spécifique).
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
                    LEFT JOIN bzz_teams t ON t.api_id = COALESCE(
                        ps.team_api_id,
                        CASE WHEN ps.is_home = true  THEN e.home_team_api_id
                             WHEN ps.is_home = false THEN e.away_team_api_id
                             ELSE NULL END
                    )
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

    # ── 3. Première passe — construction intermédiaire ────────────────────────
    intermediate: list[dict] = []
    for r in rows:
        bzz_name: str = r["bzz_name"]
        squad_entry = squad.get(_norm(bzz_name))

        nation     = squad_entry["nation"]     if squad_entry else None
        flag_emoji = squad_entry["flag_emoji"] if squad_entry else None
        position   = squad_entry["position"]   if squad_entry else _bzz_pos_to_squad(r["bzz_pos"])

        intermediate.append({
            "player_name": bzz_name,
            "nation": nation,
            "flag_emoji": flag_emoji,
            "position": position,
            "matches": int(r["matches"]),
            "minutes": int(r["minutes"]),
            "goals": int(r["goals"]),
            "assists": int(r["assists"]),
            "xg": round(float(r["xg"]), 2),
            "xa": round(float(r["xa"]), 2),
            "shots": int(r["shots"]),
            "shots_on_target": int(r["shots_on_target"]),
            "bzz_pos": r["bzz_pos"],
        })

    # ── 4. xG et xA cumulés par équipe en tournoi ─────────────────────────────
    team_xg: dict[str, float] = {}
    team_xa: dict[str, float] = {}
    for p in intermediate:
        n = p["nation"]
        if n:
            team_xg[n] = team_xg.get(n, 0.0) + p["xg"]
            team_xa[n] = team_xa.get(n, 0.0) + p["xa"]

    # ── 5. Résultats finaux ───────────────────────────────────────────────────
    result: list[PlayerRanking] = []
    for p in intermediate:
        xg      = p["xg"]
        xa      = p["xa"]
        minutes = p["minutes"]
        nation  = p["nation"]

        xg_per_90 = round(xg / minutes * 90, 2) if minutes >= 10 else None
        xa_per_90 = round(xa / minutes * 90, 2) if minutes >= 10 else None

        bm         = BM_XG.get(nation) if nation else None
        txg        = team_xg.get(nation, 0.0) if nation else 0.0
        txa        = team_xa.get(nation, 0.0) if nation else 0.0
        xg_left    = round(bm - txg, 2)         if bm is not None else None
        xa_left    = round(bm * 0.7 - txa, 2)   if bm is not None else None
        team_xg_share = round(xg / txg * 100, 1) if txg > 0 and xg > 0 else None

        result.append(PlayerRanking(
            player_name=p["player_name"],
            nation=nation,
            flag_emoji=p["flag_emoji"],
            position=p["position"],
            matches=p["matches"],
            minutes=minutes,
            goals=p["goals"],
            assists=p["assists"],
            xg=xg,
            xa=xa,
            shots=p["shots"],
            shots_on_target=p["shots_on_target"],
            finishing_delta=round(p["goals"] - xg, 2),
            creation_delta=round(p["assists"] - xa, 2),
            xg_per_90=xg_per_90,
            xa_per_90=xa_per_90,
            xg_tournoi=bm,
            xg_left=xg_left,
            xa_left=xa_left,
            team_xg_share=team_xg_share,
        ))

    return result


def _bzz_pos_to_squad(pos: str | None) -> str | None:
    """Convertit la position Bzzoiro (G/D/M/F) vers le format squad (GK/DEF/MID/FWD)."""
    return {"G": "GK", "D": "DEF", "M": "MID", "F": "FWD"}.get(pos or "", None)
