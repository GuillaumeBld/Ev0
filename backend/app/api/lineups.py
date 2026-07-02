# backend/app/api/lineups.py
"""API CRUD pour les compositions d'équipe."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.ingestion.lineup_resolver import resolve_lineup
from app.models.bzzoiro import BzzPlayer
from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer
from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer

router = APIRouter(tags=["lineups"])

# Nom Bzzoiro (anglais, fixture.home/away_team) → nom nation français (wc2026_expected_lineups)
_TEAM_TO_NATION: dict[str, str] = {
    "Algeria":                "Algérie",
    "Argentina":              "Argentine",
    "Australia":              "Australie",
    "Austria":                "Autriche",
    "Belgium":                "Belgique",
    "Bosnia and Herzegovina": "Bosnie-Herzégovine",
    "Bosnia & Herzegovina":   "Bosnie-Herzégovine",
    "Brazil":                 "Brésil",
    "Cabo Verde":             "Cap-Vert",
    "Canada":                 "Canada",
    "Colombia":               "Colombie",
    "Croatia":                "Croatie",
    "Curaçao":                "Curaçao",
    "Czechia":                "République Tchèque",
    "Czech Republic":         "République Tchèque",
    "Côte d'Ivoire":          "Côte d'Ivoire",
    "DR Congo":               "RD Congo",
    "Ecuador":                "Équateur",
    "Egypt":                  "Égypte",
    "England":                "Angleterre",
    "France":                 "France",
    "Germany":                "Allemagne",
    "Ghana":                  "Ghana",
    "Haiti":                  "Haïti",
    "Iran":                   "Iran",
    "Iraq":                   "Irak",
    "Ivory Coast":            "Côte d'Ivoire",
    "Japan":                  "Japon",
    "Jordan":                 "Jordanie",
    "Mexico":                 "Mexique",
    "Morocco":                "Maroc",
    "Netherlands":            "Pays-Bas",
    "New Zealand":            "Nouvelle-Zélande",
    "Norway":                 "Norvège",
    "Panama":                 "Panama",
    "Paraguay":               "Paraguay",
    "Portugal":               "Portugal",
    "Qatar":                  "Qatar",
    "Saudi Arabia":           "Arabie Saoudite",
    "Scotland":               "Écosse",
    "Senegal":                "Sénégal",
    "South Africa":           "Afrique du Sud",
    "South Korea":            "Corée du Sud",
    "Spain":                  "Espagne",
    "Sweden":                 "Suède",
    "Switzerland":            "Suisse",
    "Tunisia":                "Tunisie",
    "Turkey":                 "Turquie",
    "Türkiye":                "Turquie",
    "USA":                    "États-Unis",
    "United States":          "États-Unis",
    "Uruguay":                "Uruguay",
    "Uzbekistan":             "Ouzbékistan",
}

# Priorité des contextes : le plus petit = le plus récent (dernier match connu)
_CONTEXT_PRIORITY: dict[str, int] = {
    "final":      0,
    "3rd_place":  1,
    "sf":         2,
    "qf":         3,
    "r16":        4,
    "r32":        5,
    "matchday_3": 6,
    "matchday_2": 7,
    "matchday_1": 8,
    "default":    9,
}


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
    """Enrichit chaque joueur avec is_striker déduit de la position Bzzoiro (F = attaquant)."""
    names = [p.player_name for p in players]
    result = await session.execute(
        select(BzzPlayer.name, BzzPlayer.position).where(BzzPlayer.name.in_(names))
    )
    striker_map = {row.name: (row.position == "F") for row in result}
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


async def _resolve_wc2026_lineup(team: str, session: AsyncSession) -> "LineupOut | None":
    """Fallback WC2026 : lit wc2026_expected_lineups pour la nation correspondant à team.

    Choisit le contexte le plus récent disponible (final > sf > qf > … > matchday_1).
    Retourne None si aucune entrée n'existe pour cette nation.
    """
    nation = _TEAM_TO_NATION.get(team, team)
    lineups_res = await session.execute(
        select(WC2026ExpectedLineup).where(WC2026ExpectedLineup.nation == nation)
    )
    lineups = lineups_res.scalars().all()
    if not lineups:
        return None

    best = min(lineups, key=lambda lu: _CONTEXT_PRIORITY.get(lu.context, 99))

    players_res = await session.execute(
        select(WC2026ExpectedLineupPlayer)
        .where(WC2026ExpectedLineupPlayer.lineup_id == best.id)
        .order_by(
            WC2026ExpectedLineupPlayer.is_starter.desc(),
            WC2026ExpectedLineupPlayer.line_index,
            WC2026ExpectedLineupPlayer.slot_index,
        )
    )
    players = players_res.scalars().all()
    if not players:
        return None

    return LineupOut(
        lineup_id=best.id,
        lineup_type="last_known",
        team=team,
        players=[
            LineupPlayerOut(
                player_name=p.player_name,
                position=p.position,
                is_starter=p.is_starter,
                jersey_number=None,
                is_striker=(p.position == "FWD"),
            )
            for p in players
        ],
    )


# ── Routes ─────────────────────────────────────────────────────────────────
# NOTE: route ordering matters — static segments (e.g. "team-players") must
# be declared before dynamic segments (e.g. {lineup_id}) on the same prefix
# to avoid FastAPI routing the static path to the dynamic handler.

@router.get("/lineups/fixture/{fixture_id}", response_model=FixtureLineupsOut)
async def get_fixture_lineups(
    fixture_id: str, session: AsyncSession = Depends(get_db)
):
    try:
        fx = await session.get(Fixture, int(fixture_id))
    except ValueError:
        fx = None
    if fx is None:
        result = await session.execute(
            select(Fixture).where(Fixture.external_id == fixture_id)
        )
        fx = result.scalar_one_or_none()
    if fx is None:
        raise HTTPException(status_code=404, detail="Fixture not found")
    fixture_id = fx.id

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

    # Fallback WC2026 : si resolve_lineup n'a rien trouvé, on tire la dernière
    # compo connue depuis wc2026_expected_lineups (peuplée par sync_bzzoiro_lineups).
    if fx.league == "world_cup_2026":
        if home_out is None:
            home_out = await _resolve_wc2026_lineup(fx.home_team, session)
        if away_out is None:
            away_out = await _resolve_wc2026_lineup(fx.away_team, session)

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
        select(BzzPlayer.name)
        .where(BzzPlayer.current_team_name.ilike(f"%{team}%"))
        .order_by(BzzPlayer.name)
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
