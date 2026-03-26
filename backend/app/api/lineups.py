# backend/app/api/lineups.py
"""API CRUD pour les compositions d'équipe."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.lineup_resolver import resolve_lineup
from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer
from app.models.players import Player

router = APIRouter(tags=["lineups"])


# ── Schemas ────────────────────────────────────────────────────────────────

class LineupPlayerIn(BaseModel):
    player_name: str
    position: str       # GK | DEF | MID | FWD
    is_starter: bool = True
    jersey_number: int | None = None


class LineupIn(BaseModel):
    fixture_id: int
    team: str
    players: list[LineupPlayerIn]


class LineupPlayerOut(BaseModel):
    player_name: str
    position: str
    is_starter: bool
    jersey_number: int | None
    is_striker: bool = False


class LineupOut(BaseModel):
    lineup_id: int | None
    lineup_type: str
    team: str
    players: list[LineupPlayerOut]


class FixtureLineupsOut(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    home: LineupOut | None
    away: LineupOut | None


# ── Helpers ────────────────────────────────────────────────────────────────

async def _hydrate_strikers(
    players: list[TeamLineupPlayer], session: AsyncSession
) -> list[LineupPlayerOut]:
    """Enrichit chaque joueur avec son flag is_striker depuis la table Player."""
    names = [p.player_name for p in players]
    result = await session.execute(
        select(Player.name, Player.is_striker).where(Player.name.in_(names))
    )
    striker_map = {row.name: row.is_striker for row in result}
    return [
        LineupPlayerOut(
            player_name=p.player_name,
            position=p.position,
            is_starter=p.is_starter,
            jersey_number=p.jersey_number,
            is_striker=striker_map.get(p.player_name, False),
        )
        for p in players
    ]


# ── Routes ─────────────────────────────────────────────────────────────────
# NOTE: route ordering matters — static segments (e.g. "team-players") must
# be declared before dynamic segments (e.g. {lineup_id}) on the same prefix
# to avoid FastAPI routing the static path to the dynamic handler.

@router.get("/lineups/fixture/{fixture_id}", response_model=FixtureLineupsOut)
async def get_fixture_lineups(
    fixture_id: int, session: AsyncSession = Depends(get_db)
):
    fx = await session.get(Fixture, fixture_id)
    if fx is None:
        raise HTTPException(status_code=404, detail="Fixture not found")

    home_res = await resolve_lineup(fixture_id, fx.home_team, session)
    away_res = await resolve_lineup(fixture_id, fx.away_team, session)

    home_out = None
    if home_res:
        home_players = await _hydrate_strikers(home_res.players, session)
        home_out = LineupOut(
            lineup_id=home_res.lineup_id,
            lineup_type=home_res.lineup_type,
            team=home_res.team,
            players=home_players,
        )

    away_out = None
    if away_res:
        away_players = await _hydrate_strikers(away_res.players, session)
        away_out = LineupOut(
            lineup_id=away_res.lineup_id,
            lineup_type=away_res.lineup_type,
            team=away_res.team,
            players=away_players,
        )

    return FixtureLineupsOut(
        fixture_id=fixture_id,
        home_team=fx.home_team,
        away_team=fx.away_team,
        home=home_out,
        away=away_out,
    )


# Static segment "team-players" must come BEFORE any dynamic {lineup_id} GET
# route (if one is added later) to avoid shadowing.
@router.get("/lineups/team-players/{team}", response_model=list[str])
async def get_team_players(team: str, session: AsyncSession = Depends(get_db)):
    """Retourne les noms des joueurs en DB pour cette équipe (pour le sélecteur)."""
    result = await session.execute(
        select(Player.name)
        .where(Player.team.ilike(f"%{team}%"))
        .order_by(Player.name)
        .limit(100)
    )
    return [row[0] for row in result]


@router.post("/lineups", status_code=201, response_model=LineupOut)
async def create_lineup(body: LineupIn, session: AsyncSession = Depends(get_db)):
    """Créer ou remplacer une compo probable_manual."""
    fx = await session.get(Fixture, body.fixture_id)
    if fx is None:
        raise HTTPException(status_code=404, detail="Fixture not found")

    existing = await session.execute(
        select(TeamLineup).where(
            TeamLineup.fixture_id == body.fixture_id,
            TeamLineup.team == body.team,
            TeamLineup.lineup_type == "probable_manual",
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await session.delete(old)
        await session.flush()

    lineup = TeamLineup(
        fixture_id=body.fixture_id,
        team=body.team,
        lineup_type="probable_manual",
        source="manual",
        created_by="user",
    )
    session.add(lineup)
    await session.flush()

    for p in body.players:
        session.add(
            TeamLineupPlayer(
                lineup_id=lineup.id,
                player_name=p.player_name,
                position=p.position,
                is_starter=p.is_starter,
                jersey_number=p.jersey_number,
            )
        )

    await session.commit()

    players_result = await session.execute(
        select(TeamLineupPlayer).where(TeamLineupPlayer.lineup_id == lineup.id)
    )
    players_out = await _hydrate_strikers(players_result.scalars().all(), session)

    return LineupOut(
        lineup_id=lineup.id,
        lineup_type=lineup.lineup_type,
        team=lineup.team,
        players=players_out,
    )


@router.delete("/lineups/{lineup_id}", status_code=204)
async def delete_lineup(lineup_id: int, session: AsyncSession = Depends(get_db)):
    lineup = await session.get(TeamLineup, lineup_id)
    if lineup is None:
        raise HTTPException(status_code=404, detail="Lineup not found")
    if lineup.lineup_type == "official":
        raise HTTPException(status_code=403, detail="Les compos officielles ne peuvent pas être supprimées")
    await session.delete(lineup)
    await session.commit()
