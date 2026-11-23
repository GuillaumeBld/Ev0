"""Ecussons Atlas et competition, partages par toutes les reponses match.

Source unique : `canonical_teams` (via `team_badges`) et `fixtures.competition_api_id`.
Un club ou une competition inconnu reste None : jamais de valeur inventee.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.atlas_source import TeamBadge, team_badges
from app.services.competitions import competition_name


class TeamBadgeOut(BaseModel):
    name_fr: str | None = None
    crest_url: str | None = None
    api_football_id: int | None = None
    country_iso: str | None = None


def badge_out(badge: TeamBadge | None) -> TeamBadgeOut | None:
    if badge is None:
        return None
    return TeamBadgeOut(
        name_fr=badge.name_fr,
        crest_url=badge.crest_url,
        api_football_id=badge.api_football_id,
        country_iso=badge.country_iso,
    )


class MatchBadges(BaseModel):
    """Champs additifs d'une reponse match (heritee par les modeles de reponse)."""

    home_badge: TeamBadgeOut | None = None
    away_badge: TeamBadgeOut | None = None
    competition_api_id: int | None = None
    competition_name: str | None = None


def badge_fields(
    badges: dict[int, TeamBadge],
    home_bzz_team_id: int | None,
    away_bzz_team_id: int | None,
    competition_api_id: int | None,
) -> dict[str, Any]:
    """Les quatre champs, a passer en `**` au constructeur du modele de reponse."""
    return {
        "home_badge": badge_out(badges.get(home_bzz_team_id)) if home_bzz_team_id is not None else None,
        "away_badge": badge_out(badges.get(away_bzz_team_id)) if away_bzz_team_id is not None else None,
        "competition_api_id": competition_api_id,
        "competition_name": competition_name(competition_api_id),
    }


async def badge_fields_for_fixtures(
    db: AsyncSession, fixtures: Iterable[Any]
) -> dict[int, dict[str, Any]]:
    """Champs ecussons/competition par id de fixture, en UN seul SELECT pour le lot."""
    fixtures = list(fixtures)
    badges = await team_badges(
        db,
        [f.home_bzz_team_id for f in fixtures] + [f.away_bzz_team_id for f in fixtures],
    )
    return {
        f.id: badge_fields(
            badges, f.home_bzz_team_id, f.away_bzz_team_id, f.competition_api_id
        )
        for f in fixtures
    }
