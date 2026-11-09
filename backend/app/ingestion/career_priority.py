"""Vagues de priorite pour la file d'attente d'import carriere.

Pourquoi ce module existe
-------------------------
`players_needing_career` (voir `career_import.py`) couvrait jusqu'ici
uniquement les effectifs des cinq grands championnats + C1 : l'univers
"Atlas" est plus large (toute ligue integree au referentiel `canonical_teams`,
plus les selections nationales via `player_selection_season_stats`). Elargir
sans prioriser noierait les ligues suivies — celles dont le pricing depend
reellement de la carriere — sous le volume des clubs de coupe et du reste de
l'univers.

Deux vagues, pas de score continu : une vague se draine entierement avant la
suivante, ce qui rend l'ordre de traitement previsible et facile a verifier
(voir `players_needing_career`).
"""

from __future__ import annotations

from app.ingestion.bzzoiro.constants import INTL_CLUB_COMP_LEAGUE_IDS

# Selections nationales suivies (Mondial, Ligues des nations UEFA/CONCACAF).
# Memes identifiants que `SELECTION_STATS_LEAGUE_IDS` dans
# `bzzoiro.constants` : ce sont les seules selections avec de vraies stats de
# match Bzzoiro, donc les seules que la file peut effectivement traiter.
FOLLOWED_SELECTION_IDS: frozenset[int] = frozenset({27, 64, 65})


def wave_of(league_api_id: int | None) -> int:
    """Vague de priorite d'un `league_api_id` (club ou selection).

    1 = priorite : ligues suivies (cinq grands championnats + toute ligue
        de club integree qui n'est pas une competition de coupe) et nations
        du Mondial / Ligues des nations.
    2 = reste : competitions internationales de clubs (coupe d'Europe, etc.)
        et tout `league_api_id` absent (joueur sans club/selection rattachee
        au referentiel, le cas le moins sur).
    """
    if league_api_id in INTL_CLUB_COMP_LEAGUE_IDS:
        return 2
    if league_api_id in FOLLOWED_SELECTION_IDS:
        return 1
    return 1 if league_api_id is not None else 2
