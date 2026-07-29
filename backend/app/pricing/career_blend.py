"""Beta pricing — rythme de référence mélangé (buteur / passeur).

Mélange, saison par saison, le meilleur estimateur disponible :
    - saisons couvertes par Bzzoiro (bzz_player_season_stats) -> xG/xA
      (xg_per_90 / xa_per_90, agrégés TOUTES compétitions) ;
    - saisons plus anciennes (hors couverture Bzzoiro) -> buts/passes réels
      depuis la carrière (player_career_seasons, brique 1), agrégés TOUTES
      compétitions.

Une saison couverte par Bzzoiro ne doit JAMAIS être reprise depuis
player_career_seasons (anti-double-comptage) : Bzzoiro (xG) prime, la
carrière ne comble que les saisons antérieures à cette couverture.

Formule (validée) :
    rythme = Σ_s [minutes_s * decay^age_s * taux_s] / Σ_s [minutes_s * decay^age_s]

avec age_s = (année de début de la saison courante) - (année de début de la
saison s), et decay = BLEND_DECAY. Les saisons avec < 90 minutes jouées sont
ignorées (bruit statistique).

team_xg.py (moteur Alpha, gelé) n'est pas touché par ce module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bzzoiro import BzzPlayerSeasonStat
from app.models.player_career import PlayerCareerSeason
from app.services.season_service import current_season, season_start

logger = logging.getLogger(__name__)

BLEND_DECAY = 0.50  # ajustable, calibré plus tard
MIN_SEASON_MINUTES = 90  # en dessous, la saison est ignorée (bruit)


@dataclass(frozen=True)
class BlendedRhythm:
    """Rythme de référence mélangé (Beta) pour un joueur."""

    goal_rate_per_90: float
    assist_rate_per_90: float
    seasons_used: int
    has_career: bool


def blend_rate(
    entries: list[tuple[int, float, float]], decay: float = BLEND_DECAY
) -> float | None:
    """Moyenne pondérée par ancienneté : Σ[minutes*decay^age*taux] / Σ[minutes*decay^age].

    entries: liste de (age_s, minutes_s, taux_s). Les saisons avec
    minutes_s < MIN_SEASON_MINUTES sont ignorées. Retourne None si aucune
    saison valide.
    """
    numerator = 0.0
    denominator = 0.0
    for age, minutes, rate in entries:
        if minutes < MIN_SEASON_MINUTES:
            continue
        weight = minutes * (decay**age)
        numerator += weight * rate
        denominator += weight
    if denominator <= 0:
        return None
    return numerator / denominator


def _season_start_year(season: str) -> int | None:
    """Année de début d'une saison "NNNN-NNNN", ou None si format invalide."""
    try:
        return season_start(season).year
    except ValueError:
        logger.warning("bzz_player_season_stats.season au format invalide, ignoré: %r", season)
        return None


async def blended_rhythm(session: AsyncSession, player_api_id: int) -> BlendedRhythm | None:
    """Rythme mélangé (buteur + passeur) d'un joueur, ou None si aucune donnée."""
    season_str = await current_season(session)
    current_year = season_start(season_str).year

    bzz_result = await session.execute(
        select(
            BzzPlayerSeasonStat.season,
            BzzPlayerSeasonStat.minutes_played,
            BzzPlayerSeasonStat.expected_goals,
            BzzPlayerSeasonStat.expected_assists,
        ).where(BzzPlayerSeasonStat.player_api_id == player_api_id)
    )

    # Agrégation toutes compétitions confondues, par saison (une ligne bzz
    # par (joueur, ligue, saison) -> plusieurs lignes possibles par saison).
    bzz_by_season: dict[str, dict[str, float]] = {}
    for season, minutes, xg, xa in bzz_result.all():
        if season is None:
            continue
        bucket = bzz_by_season.setdefault(season, {"minutes": 0.0, "xg": 0.0, "xa": 0.0})
        bucket["minutes"] += minutes or 0
        bucket["xg"] += xg or 0.0
        bucket["xa"] += xa or 0.0

    goal_entries: list[tuple[int, float, float]] = []
    assist_entries: list[tuple[int, float, float]] = []
    bzz_covered_years: set[int] = set()

    for season, bucket in bzz_by_season.items():
        year = _season_start_year(season)
        if year is None:
            continue
        # La saison est "couverte" par bzz dès qu'une ligne existe, même si
        # elle sera écartée du mélange faute de minutes -> anti-double-
        # comptage : jamais reprise depuis la carrière.
        bzz_covered_years.add(year)

        minutes = bucket["minutes"]
        if minutes < MIN_SEASON_MINUTES:
            continue
        age = current_year - year
        goal_rate = bucket["xg"] / (minutes / 90.0)
        assist_rate = bucket["xa"] / (minutes / 90.0)
        goal_entries.append((age, minutes, goal_rate))
        assist_entries.append((age, minutes, assist_rate))

    career_result = await session.execute(
        select(
            PlayerCareerSeason.season_start_year,
            PlayerCareerSeason.goals,
            PlayerCareerSeason.assists,
            PlayerCareerSeason.minutes,
        ).where(PlayerCareerSeason.player_api_id == player_api_id)
    )

    # Agrégation toutes compétitions confondues, par année de début de
    # saison — en excluant toute saison déjà couverte par bzz.
    career_by_year: dict[int, dict[str, float]] = {}
    for year, goals, assists, minutes in career_result.all():
        if year is None or year in bzz_covered_years:
            continue
        bucket = career_by_year.setdefault(year, {"minutes": 0.0, "goals": 0.0, "assists": 0.0})
        bucket["minutes"] += minutes or 0
        bucket["goals"] += goals or 0
        bucket["assists"] += assists or 0

    n_career_used = 0
    for year, bucket in career_by_year.items():
        minutes = bucket["minutes"]
        if minutes < MIN_SEASON_MINUTES:
            continue
        age = current_year - year
        goal_rate = bucket["goals"] / (minutes / 90.0)
        assist_rate = bucket["assists"] / (minutes / 90.0)
        goal_entries.append((age, minutes, goal_rate))
        assist_entries.append((age, minutes, assist_rate))
        n_career_used += 1

    blended_goal = blend_rate(goal_entries)
    blended_assist = blend_rate(assist_entries)

    if blended_goal is None or blended_assist is None:
        return None

    return BlendedRhythm(
        goal_rate_per_90=blended_goal,
        assist_rate_per_90=blended_assist,
        seasons_used=len(goal_entries),
        has_career=n_career_used > 0,
    )
