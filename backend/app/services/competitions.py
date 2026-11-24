"""Competition d'un match : la verite est `fixtures.competition_api_id`.

C'est l'identifiant INTERNE Bzzoiro (celui de `BzzEvent.league_api_id` et la
cle de `LEAGUE_REGISTRY`). L'etiquette historique `fixtures.league` en est
derivee ici, jamais l'inverse : les anciens identifiants externes ne sont plus
acceptes (le 14/11/2026 des matchs saoudiens etaient etiquetes premier_league).
"""
from __future__ import annotations

from sqlalchemy import and_, or_

from app.ingestion.bzzoiro.constants import (
    INTERNATIONAL_LEAGUE_INTERNAL_IDS,
    TARGET_LEAGUE_INTERNAL_IDS,
)
from app.ingestion.bzzoiro.league_registry import (
    KIND_CUP,
    KIND_DOMESTIC_1,
    KIND_DOMESTIC_2,
    KIND_SELECTION,
    KIND_UEFA_CLUB,
    KIND_WORLD,
    LEAGUE_REGISTRY,
)

# id interne -> etiquette historique (6 championnats + internationales).
_ETIQUETTE_HISTORIQUE: dict[int, str] = {
    v: k for k, v in TARGET_LEAGUE_INTERNAL_IDS.items()
}
for _cle, _id in INTERNATIONAL_LEAGUE_INTERNAL_IDS.items():
    _ETIQUETTE_HISTORIQUE.setdefault(_id, _cle)

# Perimetre de synchro du calendrier : TOUTES les competitions de clubs de
# l'Atlas (championnats de 1re et 2e division, coupes d'Europe, reste du
# monde, coupes nationales) + les competitions de selections historiques.
# Decide le 24/11/2026 : on price le xG de tous les matchs que l'Atlas connait
# (715 matchs de clubs sur 10 jours, contre 134 avec l'ancien perimetre de
# 9 competitions). Les championnats feminins restent hors calendrier.
# Etiquette derivee : comp_<id> (pas d'etiquette historique).
CALENDAR_KINDS: frozenset[str] = frozenset({
    KIND_DOMESTIC_1, KIND_DOMESTIC_2, KIND_UEFA_CLUB, KIND_WORLD, KIND_CUP,
})

SYNCED_COMPETITION_IDS: frozenset[int] = frozenset(_ETIQUETTE_HISTORIQUE) | {
    cid for cid, meta in LEAGUE_REGISTRY.items() if meta.kind in CALENDAR_KINDS
}


def league_key_for(competition_api_id: int) -> str:
    """Etiquette `fixtures.league` d'une competition (id interne Bzzoiro)."""
    return _ETIQUETTE_HISTORIQUE.get(competition_api_id, f"comp_{competition_api_id}")


def _noms_affiches() -> dict[int, str]:
    """Nom affiche de chaque competition, sans homonyme.

    L'Atlas a deux « Bundesliga » (Allemagne, Autriche) et deux « Superliga »
    (Roumanie, Danemark) : la mieux classee garde le nom nu, les autres
    portent leur pays, sinon leurs matchs se melangent dans le calculateur.
    """
    par_nom: dict[str, list[int]] = {}
    for cid, meta in LEAGUE_REGISTRY.items():
        par_nom.setdefault(meta.name, []).append(cid)
    out: dict[int, str] = {}
    for nom, ids in par_nom.items():
        ids.sort(key=lambda c: (LEAGUE_REGISTRY[c].rank, c))
        for i, cid in enumerate(ids):
            pays = LEAGUE_REGISTRY[cid].country
            out[cid] = nom if i == 0 or not pays else f"{nom} ({pays})"
    return out


_NOM_AFFICHE: dict[int, str] = _noms_affiches()


def competition_name(competition_api_id: int | None) -> str | None:
    """Nom de la competition, None si inconnue de l'Atlas."""
    if competition_api_id is None:
        return None
    return _NOM_AFFICHE.get(competition_api_id)


def competition_rank(competition_api_id: int | None) -> int | None:
    """Rang d'importance de la competition dans l'Atlas (1 = la plus haute)."""
    meta = LEAGUE_REGISTRY.get(competition_api_id) if competition_api_id is not None else None
    return meta.rank if meta else None


# ── Raisonnement par competition (et non par etiquette) ───────────────────────

WC2026_COMPETITION_API_ID: int = INTERNATIONAL_LEAGUE_INTERNAL_IDS["world_cup_2026"]
UEFA_SUPER_CUP_COMPETITION_API_ID: int = INTERNATIONAL_LEAGUE_INTERNAL_IDS["uefa_super_cup"]

# Competitions de selections nationales. La Supercoupe d'UEFA, rangee parmi les
# internationales cote ingestion, oppose deux clubs : elle se price comme un
# match de clubs. Les competitions de selections de l'Atlas (qualifs, coupes
# continentales) en font partie.
SELECTION_COMPETITION_IDS: frozenset[int] = (
    frozenset(INTERNATIONAL_LEAGUE_INTERNAL_IDS.values())
    - {UEFA_SUPER_CUP_COMPETITION_API_ID}
) | frozenset(
    cid for cid, meta in LEAGUE_REGISTRY.items() if meta.kind == KIND_SELECTION
)


def is_international(competition_api_id: int | None) -> bool:
    """Le match oppose-t-il deux selections nationales ?"""
    return competition_api_id in SELECTION_COMPETITION_IDS


def is_wc2026(competition_api_id: int | None) -> bool:
    return competition_api_id == WC2026_COMPETITION_API_ID


# Competition proposee par defaut a la creation manuelle d'un match.
DEFAULT_MANUAL_COMPETITION_API_ID: int = TARGET_LEAGUE_INTERNAL_IDS["ligue_1"]

_ID_DEPUIS_ETIQUETTE: dict[str, int] = {v: k for k, v in _ETIQUETTE_HISTORIQUE.items()}


def competition_id_for_league(league: str | None) -> int | None:
    """Reciproque de `league_key_for` ; None si l'etiquette est inconnue."""
    if not league:
        return None
    if league in _ID_DEPUIS_ETIQUETTE:
        return _ID_DEPUIS_ETIQUETTE[league]
    if league.startswith("comp_") and league[5:].isdigit():
        return int(league[5:])
    return None


def competition_clause(league_col, competition_col, league: str | None,
                       competition_api_id: int | None):
    """Clause SQL de filtre par competition, None si aucun filtre demande.

    `competition_api_id` prime ; l'etiquette (ancien parametre) reste acceptee
    et est traduite en identifiant. Un match sans identifiant garde le repli
    sur son etiquette, pour ne pas perdre les lignes anterieures.
    """
    cid = competition_api_id
    if cid is None:
        cid = competition_id_for_league(league)
        if cid is None:
            return league_col == league if league else None
        return or_(
            competition_col == cid,
            and_(competition_col.is_(None), league_col == league),
        )
    return competition_col == cid


# ── Niveau match : l'identifiant, sinon l'etiquette historique ────────────────
# Un match sans identifiant (manuel, ou pas encore rattrape) garde le sens que
# lui donnait son etiquette.

def fixture_competition_id(fixture) -> int | None:
    cid = getattr(fixture, "competition_api_id", None)
    if cid is not None:
        return cid
    return competition_id_for_league(getattr(fixture, "league", None))


def is_international_fixture(fixture) -> bool:
    return is_international(fixture_competition_id(fixture))


def is_wc2026_fixture(fixture) -> bool:
    return is_wc2026(fixture_competition_id(fixture))


def clubs_only_clause(league_col, competition_col):
    """Exclut les selections : par identifiant, sinon par etiquette historique."""
    etiquettes = [league_key_for(c) for c in SELECTION_COMPETITION_IDS]
    return or_(
        and_(competition_col.is_(None), league_col.notin_(etiquettes)),
        and_(competition_col.isnot(None), competition_col.notin_(SELECTION_COMPETITION_IDS)),
    )
