"""WC2026 match center — list + detail with on-demand sync from Bzzoiro."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzPlayerMatchStat, BzzTeam

router = APIRouter(prefix="/wc2026/matches", tags=["wc2026"])

WC_LEAGUE_API_ID = 27


def _bzz_client() -> BzzoiroClient:
    key = os.environ.get("BZZOIRO_API_KEY", "")
    return BzzoiroClient(key)


# ── Pydantic out models ───────────────────────────────────────────────────────

class MatchListItem(BaseModel):
    bzz_id: int
    home_team: str
    away_team: str
    home_team_id: int | None
    away_team_id: int | None
    event_date: datetime | None
    status: str | None
    period: str | None
    round_number: int | None
    group_name: str | None
    home_score: int | None
    away_score: int | None
    home_score_ht: int | None
    away_score_ht: int | None
    home_xg: float | None
    away_xg: float | None
    has_detail: bool


class ShotPoint(BaseModel):
    x: float
    y: float
    xg: float
    type: str        # "goal" | "miss" | "blocked" | "saved"
    body: str | None # "right-foot" | "left-foot" | "head" | ...
    sit: str | None  # "open-play" | "set-piece" | "fast-break"
    player_id: int | None
    is_home: bool
    minute: int | None


class XgMinute(BaseModel):
    m: int
    xg_home: float
    xg_away: float
    cum_home: float
    cum_away: float


class Incident(BaseModel):
    type: str          # goal | substitution | card | period | injuryTime
    minute: int | None
    added_time: int | None = None
    is_home: bool | None = None
    # Goal
    player: str | None = None
    player_id: int | None = None
    assist: str | None = None
    is_own_goal: bool | None = None
    # Card
    card_type: str | None = None
    # Sub
    player_in: str | None = None
    player_out: str | None = None
    player_in_id: int | None = None
    player_out_id: int | None = None
    # Period
    text: str | None = None
    home_score: int | None = None
    away_score: int | None = None
    # Injury time
    length: int | None = None


class LineupPlayer(BaseModel):
    id: int
    name: str
    short_name: str | None
    position: str        # G | D | M | F
    jersey_number: int | None


class TeamLineup(BaseModel):
    team_id: int | None
    formation: str | None
    players: list[LineupPlayer]   # 11 starters
    substitutes: list[LineupPlayer]


class PlayerStat(BaseModel):
    player_id: int
    player_name: str | None
    team_id: int | None
    is_home: bool | None
    minutes_played: int | None
    rating: float | None
    goals: int | None
    goal_assist: int | None
    expected_goals: float | None
    expected_assists: float | None
    total_shots: int | None
    shots_on_target: int | None
    key_pass: int | None
    total_pass: int | None
    accurate_pass: int | None
    duel_won: int | None
    duel_lost: int | None
    yellow_card: int | None
    red_card: int | None


class TeamStats(BaseModel):
    shots: int | None = None
    shots_on_target: int | None = None
    possession: float | None = None
    corners: int | None = None
    fouls: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    passes: int | None = None
    pass_accuracy: float | None = None
    offsides: int | None = None


class MatchDetail(BaseModel):
    bzz_id: int
    home_team: str
    away_team: str
    home_team_id: int | None
    away_team_id: int | None
    event_date: datetime | None
    status: str | None
    period: str | None
    round_number: int | None
    group_name: str | None
    home_score: int | None
    away_score: int | None
    home_score_ht: int | None
    away_score_ht: int | None
    home_xg: float | None
    away_xg: float | None
    incidents: list[Incident]
    shotmap: list[ShotPoint]
    xg_per_minute: list[XgMinute]
    home_stats: TeamStats
    away_stats: TeamStats
    player_stats: list[PlayerStat]
    home_lineup: TeamLineup | None
    away_lineup: TeamLineup | None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_incidents(raw: list[dict[str, Any]]) -> list[Incident]:
    out = []
    for inc in raw:
        t = inc.get("type", "")
        out.append(Incident(
            type=t,
            minute=inc.get("minute"),
            added_time=inc.get("added_time"),
            is_home=inc.get("is_home"),
            player=inc.get("player"),
            player_id=inc.get("player_id"),
            assist=inc.get("assist"),
            is_own_goal=inc.get("isOwnGoal"),
            card_type=inc.get("card_type"),
            player_in=inc.get("player_in"),
            player_out=inc.get("player_out"),
            player_in_id=inc.get("player_in_id"),
            player_out_id=inc.get("player_out_id"),
            text=inc.get("text"),
            home_score=inc.get("home_score"),
            away_score=inc.get("away_score"),
            length=inc.get("length"),
        ))
    return out


def _parse_shotmap(raw: list[dict[str, Any]]) -> list[ShotPoint]:
    out = []
    for s in raw:
        pos = s.get("pos") or {}
        out.append(ShotPoint(
            x=pos.get("x", 0),
            y=pos.get("y", 0),
            xg=s.get("xg", 0),
            type=s.get("type", "miss"),
            body=s.get("body"),
            sit=s.get("sit"),
            player_id=s.get("player_id"),
            is_home=s.get("home", False),
            minute=s.get("min"),
        ))
    return out


def _parse_team_stats(raw: dict[str, Any]) -> TeamStats:
    return TeamStats(
        shots=raw.get("totalShots") or raw.get("total_shots"),
        shots_on_target=raw.get("shotsOnTarget") or raw.get("shots_on_target"),
        possession=raw.get("ballPossession") or raw.get("possession"),
        corners=raw.get("cornerKicks") or raw.get("corners"),
        fouls=raw.get("fouls"),
        yellow_cards=raw.get("yellowCards") or raw.get("yellow_cards"),
        red_cards=raw.get("redCards") or raw.get("red_cards"),
        passes=raw.get("totalPasses") or raw.get("passes"),
        pass_accuracy=raw.get("accuratePasses") or raw.get("pass_accuracy"),
        offsides=raw.get("offsides"),
    )


def _parse_lineup(raw: dict[str, Any] | None, team_key: str) -> TeamLineup | None:
    if not raw:
        return None
    side = raw.get(team_key) or {}
    def _player(p: dict) -> LineupPlayer:
        return LineupPlayer(
            id=p.get("id", 0),
            name=p.get("name", "?"),
            short_name=p.get("short_name"),
            position=p.get("position", "?"),
            jersey_number=p.get("jersey_number"),
        )
    return TeamLineup(
        team_id=side.get("team_id"),
        formation=side.get("formation"),
        players=[_player(p) for p in side.get("players") or []],
        substitutes=[_player(p) for p in side.get("substitutes") or []],
    )


def _parse_player_stats(
    raw: list[dict[str, Any]],
    name_map: dict[int, str],
    home_team_id: int | None,
) -> list[PlayerStat]:
    out = []
    for p in raw:
        pid = p.get("player_id") or p.get("id")
        tid = p.get("team_id")
        out.append(PlayerStat(
            player_id=pid,
            player_name=name_map.get(pid),
            team_id=tid,
            is_home=(tid == home_team_id) if (tid and home_team_id) else None,
            minutes_played=p.get("minutes_played"),
            rating=p.get("rating"),
            goals=p.get("goals"),
            goal_assist=p.get("goal_assist"),
            expected_goals=p.get("expected_goals"),
            expected_assists=p.get("expected_assists"),
            total_shots=p.get("total_shots"),
            shots_on_target=p.get("shots_on_target"),
            key_pass=p.get("key_pass"),
            total_pass=p.get("total_pass"),
            accurate_pass=p.get("accurate_pass"),
            duel_won=p.get("duel_won"),
            duel_lost=p.get("duel_lost"),
            yellow_card=p.get("yellow_card"),
            red_card=p.get("red_card"),
        ))
    return out


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[MatchListItem])
async def list_matches(
    session: AsyncSession = Depends(get_db),
) -> list[MatchListItem]:
    """Return all WC2026 matches ordered by date."""
    result = await session.execute(
        select(
            BzzEvent.api_id,
            BzzEvent.home_team_api_id,
            BzzEvent.away_team_api_id,
            BzzEvent.event_date,
            BzzEvent.status,
            BzzEvent.period,
            BzzEvent.round_number,
            BzzEvent.home_score,
            BzzEvent.away_score,
            BzzEvent.home_score_ht,
            BzzEvent.away_score_ht,
            BzzEvent.home_xg,
            BzzEvent.away_xg,
            BzzEvent.incidents,
        ).where(BzzEvent.league_api_id == WC_LEAGUE_API_ID)
        .order_by(BzzEvent.event_date)
    )
    rows = result.all()

    # Load team names
    team_ids = set()
    for row in rows:
        if row.home_team_api_id:
            team_ids.add(row.home_team_api_id)
        if row.away_team_api_id:
            team_ids.add(row.away_team_api_id)

    teams_result = await session.execute(
        select(BzzTeam.api_id, BzzTeam.name).where(BzzTeam.api_id.in_(team_ids))
    )
    team_names: dict[int, str] = {r.api_id: r.name for r in teams_result.all()}

    # Load group names from cached incidents (stored in incidents col after first sync)
    # For now we'll get group_name from a fresh fetch if needed, or skip
    out = []
    for row in rows:
        home_name = team_names.get(row.home_team_api_id, f"Team {row.home_team_api_id}")
        away_name = team_names.get(row.away_team_api_id, f"Team {row.away_team_api_id}")
        # has_detail = incidents column has real data (list, not null)
        has_detail = (
            row.incidents is not None
            and isinstance(row.incidents, (list, dict))
            and row.incidents != "null"
        )
        out.append(MatchListItem(
            bzz_id=row.api_id,
            home_team=home_name,
            away_team=away_name,
            home_team_id=row.home_team_api_id,
            away_team_id=row.away_team_api_id,
            event_date=row.event_date,
            status=row.status,
            period=row.period,
            round_number=row.round_number,
            group_name=None,  # populated on detail fetch
            home_score=row.home_score,
            away_score=row.away_score,
            home_score_ht=row.home_score_ht,
            away_score_ht=row.away_score_ht,
            home_xg=row.home_xg,
            away_xg=row.away_xg,
            has_detail=has_detail,
        ))
    return out


@router.get("/{bzz_id}", response_model=MatchDetail)
async def get_match_detail(
    bzz_id: int,
    session: AsyncSession = Depends(get_db),
) -> MatchDetail:
    """Return full match detail. Fetches from Bzzoiro on first access and caches."""
    result = await session.execute(
        select(BzzEvent).where(BzzEvent.api_id == bzz_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail=f"Match not found: {bzz_id}")

    # Load team names
    team_ids = [x for x in [event.home_team_api_id, event.away_team_api_id] if x]
    teams_result = await session.execute(
        select(BzzTeam.api_id, BzzTeam.name).where(BzzTeam.api_id.in_(team_ids))
    )
    team_names: dict[int, str] = {r.api_id: r.name for r in teams_result.all()}
    home_name = team_names.get(event.home_team_api_id, "?")
    away_name = team_names.get(event.away_team_api_id, "?")

    incidents_raw: list[dict] = []
    shotmap_raw: list[dict] = []
    xgpm_raw: list[dict] = []
    team_stats_home_raw: dict = {}
    team_stats_away_raw: dict = {}
    player_stats_raw: list[dict] = []
    lineups_raw: dict = {}
    group_name: str | None = None

    # Check cache: incidents stored as list in JSONB
    cached_incidents = event.incidents
    cached_shotmap = event.shotmap
    cached_lineups = event.lineups
    has_cache = (
        isinstance(cached_incidents, list) and len(cached_incidents) > 0
    )

    if not has_cache:
        # Fetch from Bzzoiro API
        async with _bzz_client() as client:
            # Incidents
            try:
                inc_data = await client.get_page(f"/api/v2/events/{bzz_id}/incidents/")
                incidents_raw = inc_data.get("incidents", []) if isinstance(inc_data, dict) else inc_data
            except Exception:
                incidents_raw = []

            # Stats + shotmap
            try:
                stats_data = await client.get_page(f"/api/v2/events/{bzz_id}/stats/")
                shotmap_raw = stats_data.get("shotmap") or []
                xgpm_raw = stats_data.get("xg_per_minute") or []
                raw_stats = stats_data.get("stats") or {}
                team_stats_home_raw = raw_stats.get("home") or {}
                team_stats_away_raw = raw_stats.get("away") or {}
            except Exception:
                pass

            # Player stats
            try:
                ps_data = await client.get_page(f"/api/v2/events/{bzz_id}/player-stats/")
                player_stats_raw = ps_data.get("player_stats", []) if isinstance(ps_data, dict) else []
            except Exception:
                player_stats_raw = []

            # Lineups
            try:
                lu_data = await client.get_page(f"/api/v2/events/{bzz_id}/lineups/")
                lineups_raw = lu_data.get("lineups") or {}
            except Exception:
                lineups_raw = {}

            # Header for group_name
            try:
                hdr = await client.get_page(f"/api/v2/events/{bzz_id}/")
                group_name = hdr.get("group_name")
            except Exception:
                pass

        # Cache incidents + shotmap + lineups in DB
        if incidents_raw or shotmap_raw or lineups_raw:
            await session.execute(
                update(BzzEvent)
                .where(BzzEvent.api_id == bzz_id)
                .values(incidents=incidents_raw, shotmap=shotmap_raw, lineups=lineups_raw)
            )
            await session.commit()

        # Upsert player stats into bzz_player_match_stats
        if player_stats_raw:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            for ps in player_stats_raw:
                pid = ps.get("player_id")
                tid = ps.get("team_id")
                if not pid:
                    continue
                stmt = (
                    pg_insert(BzzPlayerMatchStat)
                    .values(
                        player_api_id=pid,
                        event_api_id=bzz_id,
                        team_api_id=tid,
                        is_home=(tid == event.home_team_api_id) if tid else None,
                        minutes_played=ps.get("minutes_played"),
                        rating=ps.get("rating"),
                        goals=ps.get("goals"),
                        goal_assist=ps.get("goal_assist"),
                        expected_goals=ps.get("expected_goals"),
                        expected_assists=ps.get("expected_assists"),
                        total_shots=ps.get("total_shots"),
                        shots_on_target=ps.get("shots_on_target"),
                        key_pass=ps.get("key_pass"),
                        total_pass=ps.get("total_pass"),
                        accurate_pass=ps.get("accurate_pass"),
                        total_long_balls=ps.get("total_long_balls"),
                        accurate_long_balls=ps.get("accurate_long_balls"),
                        total_cross=ps.get("total_cross"),
                        accurate_cross=ps.get("accurate_cross"),
                        duel_won=ps.get("duel_won"),
                        duel_lost=ps.get("duel_lost"),
                        aerial_won=ps.get("aerial_won"),
                        aerial_lost=ps.get("aerial_lost"),
                        total_tackle=ps.get("total_tackle"),
                        won_tackle=ps.get("won_tackle"),
                        total_clearance=ps.get("total_clearance"),
                        interception=ps.get("interception"),
                        ball_recovery=ps.get("ball_recovery"),
                        yellow_card=ps.get("yellow_card"),
                        red_card=ps.get("red_card"),
                        fouls=ps.get("fouls"),
                        was_fouled=ps.get("was_fouled"),
                        dispossessed=ps.get("dispossessed"),
                        possession_lost=ps.get("possession_lost"),
                        saves=ps.get("saves"),
                        goals_conceded=ps.get("goals_conceded"),
                    )
                    .on_conflict_do_update(
                        constraint="uq_bzz_player_match",
                        set_={
                            "rating": ps.get("rating"),
                            "goals": ps.get("goals"),
                            "goal_assist": ps.get("goal_assist"),
                            "expected_goals": ps.get("expected_goals"),
                            "expected_assists": ps.get("expected_assists"),
                            "minutes_played": ps.get("minutes_played"),
                        },
                    )
                )
                await session.execute(stmt)
            await session.commit()

    else:
        # Load from cache
        incidents_raw = cached_incidents if isinstance(cached_incidents, list) else []
        shotmap_raw = cached_shotmap if isinstance(cached_shotmap, list) else []
        lineups_raw = cached_lineups if isinstance(cached_lineups, dict) else {}

        # Lineups not cached yet — fetch and store
        if not lineups_raw:
            async with _bzz_client() as client:
                try:
                    lu_data = await client.get_page(f"/api/v2/events/{bzz_id}/lineups/")
                    lineups_raw = lu_data.get("lineups") or {}
                except Exception:
                    lineups_raw = {}
            if lineups_raw:
                await session.execute(
                    update(BzzEvent)
                    .where(BzzEvent.api_id == bzz_id)
                    .values(lineups=lineups_raw)
                )
                await session.commit()

        # Player stats from DB
        ps_result = await session.execute(
            select(BzzPlayerMatchStat)
            .where(BzzPlayerMatchStat.event_api_id == bzz_id)
        )
        for ps in ps_result.scalars().all():
            player_stats_raw.append({
                "player_id": ps.player_api_id,
                "team_id": ps.team_api_id,
                "is_home": ps.is_home,
                "minutes_played": ps.minutes_played,
                "rating": ps.rating,
                "goals": ps.goals,
                "goal_assist": ps.goal_assist,
                "expected_goals": ps.expected_goals,
                "expected_assists": ps.expected_assists,
                "total_shots": ps.total_shots,
                "shots_on_target": ps.shots_on_target,
                "key_pass": ps.key_pass,
                "total_pass": ps.total_pass,
                "accurate_pass": ps.accurate_pass,
                "duel_won": ps.duel_won,
                "duel_lost": ps.duel_lost,
                "yellow_card": ps.yellow_card,
                "red_card": ps.red_card,
            })

    # Build player name map from bzz_players
    player_ids = [p.get("player_id") or p.get("id") for p in player_stats_raw if p.get("player_id") or p.get("id")]
    name_map: dict[int, str] = {}
    if player_ids:
        bp_result = await session.execute(
            select(BzzPlayer.api_id, BzzPlayer.name).where(BzzPlayer.api_id.in_(player_ids))
        )
        name_map = {r.api_id: r.name for r in bp_result.all()}

    return MatchDetail(
        bzz_id=event.api_id,
        home_team=home_name,
        away_team=away_name,
        home_team_id=event.home_team_api_id,
        away_team_id=event.away_team_api_id,
        event_date=event.event_date,
        status=event.status,
        period=event.period,
        round_number=event.round_number,
        group_name=group_name,
        home_score=event.home_score,
        away_score=event.away_score,
        home_score_ht=event.home_score_ht,
        away_score_ht=event.away_score_ht,
        home_xg=event.home_xg,
        away_xg=event.away_xg,
        incidents=_parse_incidents(incidents_raw),
        shotmap=_parse_shotmap(shotmap_raw),
        xg_per_minute=[
            XgMinute(
                m=x.get("m", 0),
                xg_home=x.get("xg_home", 0),
                xg_away=x.get("xg_away", 0),
                cum_home=x.get("cum_home", 0),
                cum_away=x.get("cum_away", 0),
            )
            for x in xgpm_raw
        ],
        home_stats=_parse_team_stats(team_stats_home_raw),
        away_stats=_parse_team_stats(team_stats_away_raw),
        player_stats=_parse_player_stats(
            player_stats_raw, name_map, event.home_team_api_id
        ),
        home_lineup=_parse_lineup(lineups_raw, "home"),
        away_lineup=_parse_lineup(lineups_raw, "away"),
    )
