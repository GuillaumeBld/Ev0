"""Perimetre du job d'expansion quotidien (Tache 6) — ligues du registre pas
encore couvertes par le pipeline paris.

Isole dans son propre module (plutot qu'en dur dans worker.py) pour que
`app/scripts/build_full_referentiel.py` (orchestrateur one-shot, Tache 6)
partage exactement le meme calcul que le job planifie `worker.py`, sans
importer `app.worker` lui-meme — ce module instancie l'integralite des jobs
paris/odds/autopilot au chargement, ce qu'un script one-shot n'a aucune
raison de tirer avec lui.
"""
from __future__ import annotations

from app.ingestion.bzzoiro.constants import (
    INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST,
    TARGET_LEAGUE_INTERNAL_ID_LIST,
)
from app.ingestion.bzzoiro.league_registry import PLAYER_STATS_LEAGUE_IDS


def expansion_league_ids() -> list[int]:
    """Ligues du registre (`PLAYER_STATS_LEAGUE_IDS`, donc deja hors tournois
    historiques 66/67 — voir `HISTORICAL_SELECTION_LEAGUE_IDS`) qui ne sont
    PAS deja synchronisees par le job paris `job_sync_bzzoiro_events`
    (big-5 + Champions League via `TARGET_LEAGUE_INTERNAL_ID_LIST`,
    international via `INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST`).

    C'est le perimetre exact du nouveau job quotidien d'expansion
    (`job_sync_referentiel_expansion`) : il ne touche jamais au calendrier
    paris ni a son controle de coherence (alertes Telegram "incidents") —
    seulement aux competitions restantes du registre (45 au moment ou ceci
    est ecrit : 57 - 2 historiques - 6 big-5/C1 - 4 internationales
    presentes dans le registre = 45 ; `friendly_international`, id 31,
    n'appartient pas a LEAGUE_REGISTRY donc ne change pas ce compte).
    """
    deja_couvertes = set(TARGET_LEAGUE_INTERNAL_ID_LIST) | set(
        INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST
    )
    return [
        league_id
        for league_id in PLAYER_STATS_LEAGUE_IDS
        if league_id not in deja_couvertes
    ]
