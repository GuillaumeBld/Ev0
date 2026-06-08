"""WC 2026 squad endpoints."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.wc2026 import WC2026SquadPlayer

router = APIRouter(prefix="/wc2026", tags=["wc2026"])


def _normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", ascii_str.lower())


def _row_to_player_dict(row: dict) -> dict:
    return {
        "player_name": row["player_name"],
        "nation": row.get("nation"),
        "group_letter": row.get("group_letter"),
        "club": row["club"],
        "position": row["position"],
        "shirt_number": row["shirt_number"],
        "matches_played": row["matches_played"],
        "minutes_played": row["minutes_played"],
        "goals": row["goals"],
        "assists": row["assists"],
        "xg": row["xg"],
        "xa": row["xa"],
        "xg_per90": row["xg_per90"],
        "xa_per90": row["xa_per90"],
        "avg_rating": row["avg_rating"],
        "saves": row["saves"],
        "form_goals_5": row["form_goals_5"],
        "form_xg_5": row["form_xg_5"],
        "form_rating_5": row["form_rating_5"],
    }


class WCPlayerOut(BaseModel):
    player_name: str
    nation: str | None = None
    group_letter: str | None = None
    club: str | None
    position: str
    shirt_number: int | None
    matches_played: int | None = None
    minutes_played: int | None = None
    goals: int | None = None
    assists: int | None = None
    xg: float | None = None
    xa: float | None = None
    xg_per90: float | None = None
    xa_per90: float | None = None
    avg_rating: float | None = None
    saves: int | None = None
    form_goals_5: int | None = None
    form_xg_5: float | None = None
    form_rating_5: float | None = None


class WCPlayersPageOut(BaseModel):
    players: list[WCPlayerOut]
    total: int
    page: int
    page_size: int


class WCNationOut(BaseModel):
    nation: str
    group_letter: str
    flag_emoji: str | None
    player_count: int


class WCSquadOut(BaseModel):
    nation: str
    group_letter: str
    flag_emoji: str | None
    gk: list[WCPlayerOut]
    def_: list[WCPlayerOut]
    mid: list[WCPlayerOut]
    fwd: list[WCPlayerOut]

    model_config = {"populate_by_name": True}


def _group_by_position(players: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {"gk": [], "def_": [], "mid": [], "fwd": []}
    pos_map = {"GK": "gk", "DEF": "def_", "MID": "mid", "FWD": "fwd"}
    for p in players:
        key = pos_map.get(p.position, "mid")
        grouped[key].append({
            "player_name": p.player_name,
            "club": p.club,
            "position": p.position,
            "shirt_number": p.shirt_number,
        })
    return grouped


def _build_squad_response(
    nation: str,
    group_letter: str,
    players: list[Any],
) -> dict[str, Any]:
    grouped = _group_by_position(players)
    flag = players[0].flag_emoji if players else None
    return {
        "nation": nation,
        "group_letter": group_letter,
        "flag_emoji": flag,
        "gk": grouped["gk"],
        "def_": grouped["def_"],
        "mid": grouped["mid"],
        "fwd": grouped["fwd"],
    }


@router.get("/nations", response_model=list[WCNationOut])
async def get_nations(session: AsyncSession = Depends(get_db)) -> list[WCNationOut]:
    """List all WC nations sorted by group then name."""
    rows = await session.execute(
        select(
            WC2026SquadPlayer.nation,
            WC2026SquadPlayer.group_letter,
            WC2026SquadPlayer.flag_emoji,
            sa_func.count(WC2026SquadPlayer.id).label("player_count"),
        )
        .group_by(
            WC2026SquadPlayer.nation,
            WC2026SquadPlayer.group_letter,
            WC2026SquadPlayer.flag_emoji,
        )
        .order_by(WC2026SquadPlayer.group_letter, WC2026SquadPlayer.nation)
    )
    return [
        WCNationOut(
            nation=r.nation,
            group_letter=r.group_letter,
            flag_emoji=r.flag_emoji,
            player_count=r.player_count,
        )
        for r in rows
    ]


_WC_SORT_FIELD_MAP: dict[str, str] = {
    "goals": "agg.goals",
    "assists": "agg.assists",
    "xg": "agg.xg",
    "xa": "agg.xa",
    "xg_per90": "agg.xg_per90",
    "xa_per90": "agg.xa_per90",
    "avg_rating": "agg.avg_rating",
    "matches_played": "agg.matches_played",
    "minutes_played": "agg.minutes_played",
    "saves": "agg.saves",
    "form_xg_5": "fs.form_xg_5",
    "form_goals_5": "fs.form_goals_5",
    "form_rating_5": "fs.form_rating_5",
}

_WC_CTE = """
WITH norm_bzz AS (
    SELECT DISTINCT ON (lower(regexp_replace(unaccent(name), '[^a-z0-9 ]', '', 'g')))
        api_id,
        lower(regexp_replace(unaccent(name), '[^a-z0-9 ]', '', 'g')) AS normalized_name
    FROM bzz_players
    ORDER BY lower(regexp_replace(unaccent(name), '[^a-z0-9 ]', '', 'g')), api_id ASC
),
agg_stats AS (
    SELECT
        bss.player_api_id,
        SUM(bss.matches_played)                                              AS matches_played,
        SUM(bss.minutes_played)                                              AS minutes_played,
        SUM(bss.goals)                                                       AS goals,
        SUM(bss.goal_assist)                                                 AS assists,
        SUM(bss.expected_goals)                                              AS xg,
        SUM(bss.expected_assists)                                            AS xa,
        CASE
            WHEN SUM(bss.minutes_played) > 0
            THEN SUM(bss.expected_goals) / SUM(bss.minutes_played) * 90
            ELSE NULL
        END                                                                  AS xg_per90,
        CASE
            WHEN SUM(bss.minutes_played) > 0
            THEN SUM(bss.expected_assists) / SUM(bss.minutes_played) * 90
            ELSE NULL
        END                                                                  AS xa_per90,
        SUM(bss.avg_rating * bss.matches_played) FILTER (WHERE bss.avg_rating IS NOT NULL)
            / NULLIF(SUM(bss.matches_played) FILTER (WHERE bss.avg_rating IS NOT NULL), 0)
                                                                             AS avg_rating,
        SUM(bss.saves)                                                       AS saves
    FROM bzz_player_season_stats bss
    WHERE bss.season = '2025-2026'
    GROUP BY bss.player_api_id
),
form_stats AS (
    SELECT DISTINCT ON (bss.player_api_id)
        bss.player_api_id,
        bss.form_goals_5,
        bss.form_xg_5,
        bss.form_rating_5
    FROM bzz_player_season_stats bss
    WHERE bss.season = '2025-2026'
    ORDER BY bss.player_api_id, bss.minutes_played DESC NULLS LAST
)
"""

_WC_PLAYERS_BODY = """
FROM wc2026_squad_players wsp
LEFT JOIN norm_bzz nb
    ON lower(regexp_replace(unaccent(wsp.player_name), '[^a-z0-9 ]', '', 'g')) = nb.normalized_name
LEFT JOIN agg_stats agg ON agg.player_api_id = nb.api_id
LEFT JOIN form_stats fs  ON fs.player_api_id  = nb.api_id
WHERE (:nation::text IS NULL OR wsp.nation = :nation::text)
  AND (:position::text = '' OR wsp.position = :position::text)
  AND (:search::text = '' OR lower(wsp.player_name) LIKE lower('%' || :search::text || '%'))
"""

_SQUAD_SQL = text(_WC_CTE + """
SELECT
    wsp.player_name,
    wsp.club,
    wsp.position,
    wsp.shirt_number,
    wsp.flag_emoji,
    wsp.nation,
    wsp.group_letter,
    agg.matches_played,
    agg.minutes_played,
    agg.goals,
    agg.assists,
    agg.xg,
    agg.xa,
    agg.xg_per90,
    agg.xa_per90,
    agg.avg_rating,
    agg.saves,
    fs.form_goals_5,
    fs.form_xg_5,
    fs.form_rating_5
FROM wc2026_squad_players wsp
LEFT JOIN norm_bzz nb
    ON lower(regexp_replace(unaccent(wsp.player_name), '[^a-z0-9 ]', '', 'g')) = nb.normalized_name
LEFT JOIN agg_stats agg ON agg.player_api_id = nb.api_id
LEFT JOIN form_stats fs  ON fs.player_api_id  = nb.api_id
WHERE wsp.nation = :nation
ORDER BY wsp.shirt_number
""")


@router.get("/squads", response_model=WCSquadOut)
async def get_squad(
    nation: str = Query(..., description="Nation name (French), e.g. 'France'"),
    session: AsyncSession = Depends(get_db),
) -> WCSquadOut:
    """Return squad grouped by position for a given nation, enriched with club stats."""
    result = await session.execute(_SQUAD_SQL, {"nation": nation})
    rows = result.mappings().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"Nation '{nation}' not found")

    grouped: dict[str, list[WCPlayerOut]] = {"gk": [], "def_": [], "mid": [], "fwd": []}
    pos_map = {"GK": "gk", "DEF": "def_", "MID": "mid", "FWD": "fwd"}
    flag = rows[0]["flag_emoji"]
    group_letter = rows[0]["group_letter"]

    for row in rows:
        key = pos_map.get(row["position"], "mid")
        grouped[key].append(WCPlayerOut(**_row_to_player_dict(dict(row))))

    return WCSquadOut(
        nation=nation,
        group_letter=group_letter,
        flag_emoji=flag,
        gk=grouped["gk"],
        def_=grouped["def_"],
        mid=grouped["mid"],
        fwd=grouped["fwd"],
    )


@router.get("/players", response_model=WCPlayersPageOut)
async def list_wc_players(
    nation: str | None = Query(None),
    position: str = Query(""),
    search: str = Query(""),
    sort_by: str = Query("xg_per90"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    session: AsyncSession = Depends(get_db),
) -> WCPlayersPageOut:
    """List all WC2026 players with optional filters and pagination."""
    sort_col = _WC_SORT_FIELD_MAP.get(sort_by, "agg.xg_per90")
    order_dir = "ASC" if sort_order.lower() == "asc" else "DESC"
    page_size = 50

    params: dict[str, Any] = {
        "nation": nation,
        "position": position,
        "search": search,
    }

    count_result = await session.execute(
        text(_WC_CTE + "SELECT COUNT(*) " + _WC_PLAYERS_BODY),
        params,
    )
    total: int = count_result.scalar_one()

    data_result = await session.execute(
        text(f"""
        {_WC_CTE}
        SELECT
            wsp.player_name,
            wsp.nation,
            wsp.group_letter,
            wsp.flag_emoji,
            wsp.club,
            wsp.position,
            wsp.shirt_number,
            agg.matches_played,
            agg.minutes_played,
            agg.goals,
            agg.assists,
            agg.xg,
            agg.xa,
            agg.xg_per90,
            agg.xa_per90,
            agg.avg_rating,
            agg.saves,
            fs.form_goals_5,
            fs.form_xg_5,
            fs.form_rating_5
        {_WC_PLAYERS_BODY}
        ORDER BY {sort_col} {order_dir} NULLS LAST, wsp.player_name ASC
        LIMIT :page_size OFFSET :offset
        """),
        {**params, "page_size": page_size, "offset": (page - 1) * page_size},
    )
    rows = data_result.mappings().all()

    return WCPlayersPageOut(
        players=[WCPlayerOut(**_row_to_player_dict(dict(r))) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
