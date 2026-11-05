"""Atlas — arbre géographique (ligues par pays) pour l'explorateur de joueurs.

Lecture seule : alimente la carte du monde et les menus de la future page
Atlas. Trois requêtes, pas de N+1 : les ligues concernées, le compte de
clubs par ligue (group by), et le dernier `DataCoverageRun` par ligue (une
seule requête à base de window function, pas une par ligue).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.canonical_teams import CanonicalTeam
from app.models.data_coverage import DataCoverageRun
from app.models.leagues import League

router = APIRouter()
logger = logging.getLogger(__name__)

# Nom de pays (anglais, tel qu'en base) -> (code ISO2, continent).
# England et Scotland partagent GB : la carte traite le Royaume-Uni comme un
# seul pays, leurs ligues se retrouvent donc sous le même nœud.
_COUNTRY_MAP: dict[str, tuple[str, str]] = {
    "England": ("GB", "EUR"),
    "Scotland": ("GB", "EUR"),
    "Spain": ("ES", "EUR"),
    "Italy": ("IT", "EUR"),
    "Germany": ("DE", "EUR"),
    "France": ("FR", "EUR"),
    "Portugal": ("PT", "EUR"),
    "Netherlands": ("NL", "EUR"),
    "Turkey": ("TR", "EUR"),
    "Belgium": ("BE", "EUR"),
    "Switzerland": ("CH", "EUR"),
    "Greece": ("GR", "EUR"),
    "Poland": ("PL", "EUR"),
    "Romania": ("RO", "EUR"),
    "Norway": ("NO", "EUR"),
    "Sweden": ("SE", "EUR"),
    "Denmark": ("DK", "EUR"),
    "Czechia": ("CZ", "EUR"),
    "Bulgaria": ("BG", "EUR"),
    "Finland": ("FI", "EUR"),
    "Brazil": ("BR", "SAM"),
    "Argentina": ("AR", "SAM"),
    "Colombia": ("CO", "SAM"),
    "USA": ("US", "NAM"),
    "United States": ("US", "NAM"),
    "Mexico": ("MX", "NAM"),
    "Japan": ("JP", "ASI"),
    "South Korea": ("KR", "ASI"),
    "China": ("CN", "ASI"),
    "Saudi Arabia": ("SA", "ASI"),
    "Morocco": ("MA", "AFR"),
    "Tunisia": ("TN", "AFR"),
    "Australia": ("AU", "OCE"),
}

# Code ISO2 -> nom affiché, quand il diverge du nom de pays brut trouvé en
# base. Seul GB diverge (fusion England+Scotland) ; tous les autres codes
# reprennent simplement le nom anglais du premier pays rencontré.
_DISPLAY_NAME_OVERRIDES: dict[str, str] = {"GB": "Royaume-Uni"}

_DOMESTIC_KINDS = ("domestic_1", "domestic_2")
_INTL_KINDS = ("uefa_club", "selection")
_KIND_WORLD = "world"
# Tous les kinds que /atlas/geo considère. "world" (grands championnats
# mondiaux hors UEFA — Brésil, Arabie S., MLS, etc. — voir
# app/ingestion/bzzoiro/league_registry.py) était absent ici : ces ligues
# disparaissaient silencieusement de la carte alors que _COUNTRY_MAP les
# connaît déjà. L'aiguillage countries/international se fait ensuite par
# présence d'un `country` mappé, pas par kind (voir boucle plus bas) — donc
# ajouter "world" ici suffit à faire réapparaître ces pays sans toucher au
# routage des deux cas particuliers "world" sans country (Copa Libertadores
# / Copa Sudamericana, continentales et non nationales).
_ALL_KINDS = _DOMESTIC_KINDS + _INTL_KINDS + (_KIND_WORLD,)


class LeagueOut(BaseModel):
    league_api_id: int
    name: str
    tier: int | str
    kind: str
    coverage: str
    club_count: int


class CountryOut(BaseModel):
    code: str
    name: str
    continent: str
    leagues: list[LeagueOut]


class IntlOut(BaseModel):
    league_api_id: int
    name: str
    org: str
    kind: str


class GeoOut(BaseModel):
    countries: list[CountryOut]
    international: list[IntlOut]


def _tier_out(tier: str) -> int | str:
    """`League.tier` est toujours une chaîne en base ("1".."4" ou le
    sentinel littéral "selection" — voir app/models/leagues.py). On la rend
    numérique côté API quand c'est effectivement un entier, pour que le
    front n'ait pas à reparser une chaîne qui ressemble à un nombre."""
    try:
        return int(tier)
    except (TypeError, ValueError):
        return tier


def _coverage(status: str | None, tier: str, kind: str) -> str:
    """'ok' -> full, 'partial' -> part. Tout le reste (aucun run, 'empty',
    'failed') retombe sur un défaut : tier 1 (1res divisions européennes) ou
    kind=='world' (1res divisions mondiales hors UEFA, qui ont elles aussi
    des stats complètes côté ingestion) -> full ; sinon on ne sait pas
    vraiment, donc on ne prétend pas 'full' pour une ligue secondaire."""
    if status == "ok":
        return "full"
    if status == "partial":
        return "part"
    return "full" if (tier == "1" or kind == _KIND_WORLD) else "part"


async def _latest_coverage_status(
    db: AsyncSession, league_ids: list[int]
) -> dict[int, str | None]:
    """Un seul SELECT (window function) : le statut du DataCoverageRun le
    plus récent par league_api_id, pas une requête par ligue."""
    if not league_ids:
        return {}
    rn = (
        func.row_number()
        .over(
            partition_by=DataCoverageRun.league_api_id,
            order_by=DataCoverageRun.started_at.desc(),
        )
        .label("rn")
    )
    subq = (
        select(DataCoverageRun.league_api_id, DataCoverageRun.status, rn)
        .where(DataCoverageRun.league_api_id.in_(league_ids))
        .subquery()
    )
    rows = (
        await db.execute(select(subq.c.league_api_id, subq.c.status).where(subq.c.rn == 1))
    ).all()
    return dict(rows)


@router.get("/atlas/geo", response_model=GeoOut)
async def atlas_geo(db: AsyncSession = Depends(get_db)) -> GeoOut:
    leagues = (
        (await db.execute(select(League).where(League.kind.in_(_ALL_KINDS))))
        .scalars()
        .all()
    )
    league_ids = [league.id for league in leagues]

    club_counts: dict[int, int] = dict(
        (
            await db.execute(
                select(CanonicalTeam.league_api_id, func.count())
                .where(CanonicalTeam.league_api_id.in_(league_ids))
                .group_by(CanonicalTeam.league_api_id)
            )
        ).all()
    )

    latest_status = await _latest_coverage_status(db, league_ids)

    # code -> [(rank, LeagueOut), ...] pour pouvoir trier par rank sans
    # exposer le rank dans la réponse.
    countries_raw: dict[str, list[tuple[int, LeagueOut]]] = {}
    country_meta: dict[str, tuple[str, str]] = {}  # code -> (continent, nom brut)
    country_min_rank: dict[str, int] = {}
    intl_raw: list[tuple[int, IntlOut]] = []

    for league in leagues:
        club_count = club_counts.get(league.id, 0)
        coverage = _coverage(latest_status.get(league.id), league.tier, league.kind)

        # Aiguillage par présence de pays, pas par kind : un "world" avec un
        # country connu (Brasileirão, MLS, ...) rejoint les ligues
        # domestiques sous son pays ; un "world"/"uefa_club"/"selection" sans
        # country (Copa Libertadores/Sudamericana, UEFA, sélections) va dans
        # international. Un country renseigné mais absent de _COUNTRY_MAP
        # est ignoré (log, pas de plantage) — inchangé depuis avant ce fix.
        mapped = _COUNTRY_MAP.get(league.country) if league.country else None

        if league.country and mapped is None:
            logger.warning(
                "Atlas: pays sans mapping ignoré: %r (league_id=%s)",
                league.country,
                league.id,
            )
            continue

        if mapped is not None:
            code, continent = mapped
            league_out = LeagueOut(
                league_api_id=league.id,
                name=league.name,
                tier=_tier_out(league.tier),
                kind=league.kind,
                coverage=coverage,
                club_count=club_count,
            )
            countries_raw.setdefault(code, []).append((league.rank, league_out))
            country_meta.setdefault(code, (continent, league.country or code))
            country_min_rank[code] = min(country_min_rank.get(code, league.rank), league.rank)
        else:
            if league.kind == "selection":
                org = "FIFA"
            elif league.kind == _KIND_WORLD:
                org = "CONMEBOL"
            else:
                org = "UEFA"
            intl_raw.append(
                (
                    league.rank,
                    IntlOut(
                        league_api_id=league.id,
                        name=league.name,
                        org=org,
                        kind=league.kind,
                    ),
                )
            )

    ordered_codes = sorted(country_min_rank, key=lambda c: country_min_rank[c])
    countries_out = [
        CountryOut(
            code=code,
            name=_DISPLAY_NAME_OVERRIDES.get(code, country_meta[code][1]),
            continent=country_meta[code][0],
            leagues=[lo for _, lo in sorted(countries_raw[code], key=lambda t: t[0])],
        )
        for code in ordered_codes
    ]
    international_out = [io for _, io in sorted(intl_raw, key=lambda t: t[0])]

    return GeoOut(countries=countries_out, international=international_out)
