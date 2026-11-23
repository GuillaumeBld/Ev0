"""Relier une cote bookmaker au joueur Atlas (bzz_players.api_id).

Les bookmakers ne donnent qu'un nom, sous des formes variées :
« Samba, Floyd » (Unibet), « Allan » (PMU), « Allan Andrade Elias »,
« Rasmus Hojlund » sans ø, « Philip Foden » pour Phil Foden... Le résolveur
compare ce nom à l'effectif Atlas des DEUX équipes du match, celui-là même
que le calculateur cote. Ordre :

1. nom normalisé identique (accents, casse, ponctuation ignorés) ;
2. nom de famille commun + prénom compatible (même initiale, prénom court
   contenu dans le long — Phil/Philip —, ou prénom Atlas parmi les prénoms
   donnés par le bookmaker) ; un nom bookmaker d'un seul mot ne vaut que
   s'il est le prénom OU le nom d'un seul joueur de l'effectif ; un nom
   ATLAS d'un seul mot (Neto, Danilo, Marcos...) ne vaut qu'à l'identique —
   « Pedro Neto » ou « Marcos Senesi » ne désignent jamais l'Atlas « Neto »
   ou « Marcos » ;
3. sinon None.

Plusieurs candidats = None : on ne devine jamais, et aucune distance
d'édition approximative.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.odds import normalize_selection_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SquadPlayer:
    api_id: int
    name: str


# Lettres que la décomposition Unicode ne ramène pas à l'ASCII.
_SPECIAL = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "ß": "ss", "ł": "l", "Ł": "l", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d",
    "þ": "th", "Þ": "th", "ı": "i",
})


def _tokens(name: str) -> list[str]:
    """Mots normalisés du nom (même normalisation que normalize_selection_name)."""
    text = (name or "").translate(_SPECIAL).replace(".", ". ")
    norm = normalize_selection_name(text)
    return [t for t in norm.split("-") if t] if norm else []


def _bookmaker_tokens(name: str) -> list[str]:
    """Remet le nom du bookmaker dans l'ordre « Prénom Nom »."""
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first} {last}"
    toks = _tokens(name)
    # « MBAPPE K. » : initiale en dernier → la ramener devant.
    if len(toks) >= 2 and len(toks[-1]) == 1 and len(toks[0]) > 1:
        toks = [toks[-1], *toks[:-1]]
    return toks


def _common_suffix(a: list[str], b: list[str]) -> int:
    k = 0
    while k < len(a) and k < len(b) and a[-1 - k] == b[-1 - k]:
        k += 1
    return k


def _given_compatible(bk_given: list[str], at_given: list[str]) -> bool:
    bf, af = bk_given[0], at_given[0]
    if len(bf) == 1 or len(af) == 1:
        return bf[0] == af[0]
    if bf.startswith(af) or af.startswith(bf):
        return True
    return af in bk_given or bf in at_given


def _compatible(bk: list[str], at: list[str]) -> bool:
    if not at:
        return False
    if len(bk) == 1:
        return bk[0] in (at[0], at[-1])
    if len(at) == 1:
        # Nom Atlas d'un seul mot : seule l'égalité exacte (étape 1) compte.
        return False
    k = _common_suffix(bk, at)
    if k == 0:
        return False
    bk_given, at_given = bk[:-k], at[:-k]
    if not bk_given or not at_given:
        return True
    return _given_compatible(bk_given, at_given)


def _single(ids: set[int]) -> int | None:
    return next(iter(ids)) if len(ids) == 1 else None


def resolve_odds_player(bookmaker_name: str, squad: list[SquadPlayer]) -> int | None:
    """api_id du joueur Atlas désigné par le bookmaker, ou None (absent/ambigu)."""
    bk = _bookmaker_tokens(bookmaker_name or "")
    if not bk or not squad:
        return None
    tokenized = [(p.api_id, _tokens(p.name)) for p in squad]

    exact = {pid for pid, at in tokenized if at == bk}
    if exact:
        return _single(exact)

    return _single({pid for pid, at in tokenized if _compatible(bk, at)})


async def load_fixture_squad(session: AsyncSession, fixture_id: int) -> list[SquadPlayer]:
    """Effectif Atlas des deux équipes du match — le même vivier que le calculateur.

    Club : `club_squad_filter` (Transfermarkt seule autorité).
    International (is_international) : `national_team_api_id` des deux
    sélections de l'événement Bzzoiro, plus, en Coupe du monde, les joueurs
    des compositions Bzzoiro (comme `load_match_pricing`).
    """
    from app.models.bzzoiro import BzzEvent, BzzPlayer
    from app.models.fixtures import Fixture
    from app.services.competitions import is_international, is_wc2026
    from app.services.atlas_source import club_squad_filter

    fixture = await session.get(Fixture, fixture_id)
    if fixture is None:
        return []

    filters = []
    if is_international(fixture.competition_api_id):
        bzz_api_id_str = (fixture.external_id or "").removeprefix("bzz_")
        event = None
        if bzz_api_id_str.isdigit():
            event = (await session.execute(
                select(BzzEvent).where(BzzEvent.api_id == int(bzz_api_id_str))
            )).scalar_one_or_none()
        if event is not None and event.home_team_api_id and event.away_team_api_id:
            filters.append(BzzPlayer.national_team_api_id.in_(
                [event.home_team_api_id, event.away_team_api_id]
            ))
            if is_wc2026(fixture.competition_api_id) and event.lineups:
                lineup_ids: set[int] = set()
                for side in ("home", "away"):
                    for section in ("players", "substitutes"):
                        for p in (event.lineups.get(side) or {}).get(section) or []:
                            if p.get("id"):
                                lineup_ids.add(int(p["id"]))
                if lineup_ids:
                    filters.append(BzzPlayer.api_id.in_(lineup_ids))
        else:
            logger.warning(
                "load_fixture_squad: match international %s (league=%r, external_id=%r) "
                "— événement Bzzoiro absent ou sans sélections, repli sur les effectifs club",
                fixture_id, fixture.league, fixture.external_id,
            )

    if not filters:
        for team_id in (fixture.home_bzz_team_id, fixture.away_bzz_team_id):
            if team_id is not None:
                filters.append(club_squad_filter(team_id))

    if not filters:
        return []

    rows = (await session.execute(
        select(BzzPlayer.api_id, BzzPlayer.name).where(or_(*filters))
    )).all()
    return [SquadPlayer(api_id=api_id, name=name) for api_id, name in rows]
