"""Rattache un joueur à son équipe nationale via sa nationalité.

La nationalité (bzz_players.nationality) colle à canonical_teams.name_en des
équipes de la ligue 27 (Coupe du monde 2026) à ~1:1 ; _NATIONALITY_OVERRIDES
corrige les rares écarts de libellé. Robuste là où bzz_player_match_stats.
team_api_id est vide (59 % des lignes CM).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzPlayer
from app.models.canonical_teams import CanonicalTeam

_WORLD_CUP_LEAGUE_ID = 27

# nationalité (bzz_players.nationality) -> canonical_teams.name_en
_NATIONALITY_OVERRIDES: dict[str, str] = {
    "Cape Verde": "Cabo Verde",
    "Curacao": "Curaçao",
}


async def _national_team_id_by_name_en(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(CanonicalTeam.name_en, CanonicalTeam.bzz_team_id).where(
                CanonicalTeam.league_api_id == _WORLD_CUP_LEAGUE_ID,
                CanonicalTeam.bzz_team_id.is_not(None),
                CanonicalTeam.name_en.is_not(None),
            )
        )
    ).all()
    return {name_en: bzz_team_id for name_en, bzz_team_id in rows}


async def national_team_id_by_player(session: AsyncSession) -> dict[int, int]:
    by_name = await _national_team_id_by_name_en(session)
    players = (
        await session.execute(
            select(BzzPlayer.api_id, BzzPlayer.nationality).where(
                BzzPlayer.nationality.is_not(None)
            )
        )
    ).all()
    out: dict[int, int] = {}
    for api_id, nationality in players:
        name_en = _NATIONALITY_OVERRIDES.get(nationality, nationality)
        team_id = by_name.get(name_en)
        if team_id is not None:
            out[api_id] = team_id
    return out
