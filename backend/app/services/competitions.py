"""Competition d'un match : la verite est `fixtures.competition_api_id`.

C'est l'identifiant INTERNE Bzzoiro (celui de `BzzEvent.league_api_id` et la
cle de `LEAGUE_REGISTRY`). L'etiquette historique `fixtures.league` en est
derivee ici, jamais l'inverse : les anciens identifiants externes ne sont plus
acceptes (le 14/11/2026 des matchs saoudiens etaient etiquetes premier_league).
"""
from __future__ import annotations

from app.ingestion.bzzoiro.constants import (
    INTERNATIONAL_LEAGUE_INTERNAL_IDS,
    TARGET_LEAGUE_INTERNAL_IDS,
)
from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY

# id interne -> etiquette historique (6 championnats + internationales).
_ETIQUETTE_HISTORIQUE: dict[int, str] = {
    v: k for k, v in TARGET_LEAGUE_INTERNAL_IDS.items()
}
for _cle, _id in INTERNATIONAL_LEAGUE_INTERNAL_IDS.items():
    _ETIQUETTE_HISTORIQUE.setdefault(_id, _cle)

# Perimetre de synchro du calendrier : inchange (l'elargir est une etape ulterieure).
SYNCED_COMPETITION_IDS: frozenset[int] = frozenset(_ETIQUETTE_HISTORIQUE)


def league_key_for(competition_api_id: int) -> str:
    """Etiquette `fixtures.league` d'une competition (id interne Bzzoiro)."""
    return _ETIQUETTE_HISTORIQUE.get(competition_api_id, f"comp_{competition_api_id}")


def competition_name(competition_api_id: int | None) -> str | None:
    """Nom de la competition, None si inconnue de l'Atlas."""
    if competition_api_id is None:
        return None
    meta = LEAGUE_REGISTRY.get(competition_api_id)
    return meta.name if meta else None
