"""Resolution fixture <-> evenement PS3838.

SEUL endroit du chantier ou un nom d'equipe est compare. Une fois l'identifiant
pose, les cotes sont recuperees par identifiant : plus aucun rapprochement
approximatif au moment du scraping.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.ps3838.client import Ps3838Event

logger = logging.getLogger(__name__)

MAX_KICKOFF_DELTA = timedelta(hours=2)

_STOP = {
    "fc", "cf", "sc", "ac", "cd", "ud", "sd", "as", "ss", "ssc", "afc", "rc",
    "club", "de", "la", "le", "los", "el", "sk", "nk", "gnk", "calcio", "cfr",
}

# Marqueurs de variante (reserve / jeunes / feminine) : un token identique de
# part et d'autre change l'equipe, meme si le reste du nom se contient
# (ex: 'Real Sociedad' vs 'Real Sociedad II'). Repris de l'approche
# _TEAM_VARIANT_MARKERS de odds_scheduler.py. Le 'b' isole ne doit matcher
# qu'en tant que token entier ('Bayern' ne tokenise jamais en 'b').
_VARIANT_MARKERS = frozenset({
    "ii", "iii", "b", "w", "res", "reserves", "fem", "women", "youth",
})
_VARIANT_AGE_RE = re.compile(r"^u\d{2}$")


# Lettres qui ne se DECOMPOSENT pas en NFKD : ce ne sont pas des lettres
# accentuees mais des caracteres a part entiere. Sans cette table, l'encodage
# ascii les SUPPRIME au lieu de les replier -- 'Bodo/Glimt' devenait 'Bod',
# et l'equipe ne pouvait jamais etre ancree.
_FOLD_EXTRA = str.maketrans({
    "\u00f8": "o", "\u00d8": "O",     # o barre (norvegien, danois)
    "\u00e6": "ae", "\u00c6": "AE",   # ae ligature
    "\u00e5": "a", "\u00c5": "A",     # a rond
    "\u0142": "l", "\u0141": "L",     # l barre (polonais)
    "\u0111": "d", "\u0110": "D",     # d barre (croate, serbe)
    "\u00f0": "d", "\u00d0": "D",     # eth (islandais)
    "\u00fe": "th", "\u00de": "Th",   # thorn (islandais)
    "\u00df": "ss",                     # eszett (allemand)
})


def _fold(name: str) -> str:
    """Nom plie en ascii minuscule, ponctuation remplacee par des espaces."""
    s = (name or "").translate(_FOLD_EXTRA)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s)


def _raw_tokens(name: str) -> set[str]:
    """Tokens bruts, sans filtre de longueur ni mots vides -- utilise pour
    detecter les marqueurs de variante AVANT que norm_team ne les supprime
    (ii, b, w font 1 ou 2 caracteres)."""
    return {t for t in _fold(name).split() if t}


def _variant_markers(name: str) -> frozenset[str]:
    """Marqueurs de variante (reserve/jeunes/feminine) presents dans le nom."""
    return frozenset(
        t for t in _raw_tokens(name)
        if t in _VARIANT_MARKERS or _VARIANT_AGE_RE.match(t)
    )


def norm_team(name: str) -> set[str]:
    """Tokens normalises : lettres pliees, mots vides et suffixes de club retires."""
    return {t for t in _fold(name).split() if t and t not in _STOP and len(t) > 2}


def build_alias_map(canonical_teams) -> dict[frozenset[str], int]:
    """Chaque nom et alias d'une equipe canonique -> son identifiant.

    La cle est l'ensemble de tokens normalises, pour que 'Olympique Lyonnais'
    et 'Lyon' pointent vers la meme equipe. Premier arrive, premier servi : un
    alias ne vole jamais la cle qu'une autre equipe a deja prise.
    """
    out: dict[frozenset[str], int] = {}
    for ct in canonical_teams:
        for name in (ct.name_fr, getattr(ct, "name_en", None), *(ct.aliases or [])):
            if not name:
                continue
            key = frozenset(norm_team(name))
            if key:
                out.setdefault(key, ct.id)
    return out


def _same_team(a: str, b: str, alias_map: dict[frozenset[str], int] | None = None) -> bool:
    """Deux noms designent-ils la meme equipe ?

    Deux chemins, dans cet ordre : l'equipe canonique si les deux noms y sont
    connus (c'est ce qui rapproche 'Olympique Lyonnais' et 'Lyon'), sinon
    l'inclusion de tokens. Le garde-fou reserve/premiere equipe s'applique
    AVANT les deux : aucun alias ne doit jamais faire passer une reserve pour
    l'equipe premiere.
    """
    ta, tb = norm_team(a), norm_team(b)
    if not ta or not tb:
        return False

    # Les deux cotes doivent porter exactement le meme jeu de marqueurs de
    # variante : 'Real Sociedad' (aucun marqueur) ne doit jamais matcher
    # 'Real Sociedad II' (marqueur 'ii'), meme si le reste du nom se contient.
    if _variant_markers(a) != _variant_markers(b):
        return False

    if alias_map:
        ca, cb = alias_map.get(frozenset(ta)), alias_map.get(frozenset(tb))
        if ca is not None and cb is not None:
            return ca == cb

    # Un cote doit etre entierement contenu dans l'autre : 'Real Madrid' et
    # 'Real Sociedad' partagent 'real' mais ne se contiennent pas.
    return ta <= tb or tb <= ta


def match_event(
    fixture,
    events: list[Ps3838Event],
    alias_map: dict[frozenset[str], int] | None = None,
) -> Ps3838Event | None:
    """Evenement correspondant, ou None. Ne devine jamais.

    Exige les deux equipes dans le bon sens ET un coup d'envoi a +/- 2 h.
    Renvoie None si plusieurs candidats subsistent.
    """
    ko = fixture.kickoff_utc
    if ko is None:
        return None
    if ko.tzinfo is None:
        ko = ko.replace(tzinfo=UTC)

    hits = [
        ev for ev in events
        if abs(ev.kickoff_utc - ko) <= MAX_KICKOFF_DELTA
        and _same_team(fixture.home_team, ev.home, alias_map)
        and _same_team(fixture.away_team, ev.away, alias_map)
    ]
    return hits[0] if len(hits) == 1 else None


async def resolve_anchors(
    session: AsyncSession,
    events: list[Ps3838Event],
    alias_map: dict[frozenset[str], int] | None = None,
) -> tuple[int, list[str]]:
    """Pose ps3838_event_id sur les fixtures a venir non encore ancrees.

    Retourne (nb_resolus, libelles_non_resolus). Les non-resolus sont retournes
    pour surfacage, jamais devines.
    """
    from datetime import datetime

    from app.models.canonical_teams import CanonicalTeam
    from app.models.fixtures import Fixture

    # Les alias canoniques rapprochent les ecritures courtes des ecritures
    # longues ('Lyon' / 'Olympique Lyonnais', 'Inter' / 'Internazionale').
    # Table petite : la charger entierement est moins couteux que de risquer
    # un match manquant. Injectable pour les tests, ou canonical_teams.aliases
    # (un ARRAY Postgres) n'est pas creable sous SQLite.
    if alias_map is None:
        alias_map = build_alias_map(
            (await session.execute(select(CanonicalTeam))).scalars().all()
        )

    now = datetime.now(UTC)
    rows = (await session.execute(
        select(Fixture).where(
            Fixture.ps3838_event_id.is_(None),
            Fixture.kickoff_utc > now,
            Fixture.status.notin_(["finished", "cancelled", "postponed"]),
        )
    )).scalars().all()

    taken = set(
        (await session.execute(
            select(Fixture.ps3838_event_id).where(Fixture.ps3838_event_id.isnot(None))
        )).scalars().all()
    )

    resolved = 0
    unresolved: list[str] = []
    for fx in rows:
        ev = match_event(fx, events, alias_map)
        if ev is None or ev.event_id in taken:
            unresolved.append(f"{fx.home_team} - {fx.away_team} ({fx.kickoff_utc:%d/%m %H:%M})")
            continue
        fx.ps3838_event_id = ev.event_id
        taken.add(ev.event_id)
        resolved += 1

    await session.commit()
    logger.info("PS3838 anchor: %d resolus, %d non resolus", resolved, len(unresolved))
    return resolved, unresolved
