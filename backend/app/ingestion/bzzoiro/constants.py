"""Bzzoiro API constants — single source of truth for league identifiers.

IMPORTANT — two distinct ID spaces in Bzzoiro:

  api_id   : the external identifier stored in league.api_id in API responses.
              Used to identify leagues in our DB (BzzLeague.api_id, BzzEvent.league_api_id).

  internal_id : Bzzoiro's internal database primary key (league.id in API responses).
                Must be passed as the `?league=` query parameter when filtering events/stats.
                These are DIFFERENT from api_ids and must not be confused.

Example: Premier League → api_id=17 but internal_id=1.
         Calling ?league=17 returns Saudi Pro League, NOT Premier League.
"""

# External api_ids — stored in our DB, used for DB queries
TARGET_LEAGUE_API_IDS: dict[str, int] = {
    "premier_league": 17,
    "ligue_1": 34,
    "bundesliga": 35,
    "la_liga": 8,
    "serie_a": 23,
    "champions_league": 7,
}

# Internal IDs — used as ?league= filter parameter in API calls
TARGET_LEAGUE_INTERNAL_IDS: dict[str, int] = {
    "premier_league": 1,
    "ligue_1": 6,
    "bundesliga": 5,
    "la_liga": 3,
    "serie_a": 4,
    "champions_league": 7,
}

# International competition internal IDs (confirmed 2026-06-08 via /api/leagues/)
INTERNATIONAL_LEAGUE_INTERNAL_IDS: dict[str, int] = {
    "world_cup_2026": 27,
    "friendly_international": 31,
    "nations_league_uefa": 64,
    "nations_league_concacaf": 65,
    "uefa_super_cup": 90,       # confirmed 2026-08-12 via /api/leagues/ (id=90)
}

INTERNATIONAL_LEAGUE_API_IDS: dict[str, int] = {
    "world_cup_2026": 27,       # api_id == internal_id for international comps
    "friendly_international": 31,
    "nations_league_uefa": 64,
    "nations_league_concacaf": 65,
    "uefa_super_cup": 90,       # api_id null in bzz response → falls back to internal id 90
}

# Convenience lists
TARGET_LEAGUE_API_ID_LIST: list[int] = list(TARGET_LEAGUE_API_IDS.values())
TARGET_LEAGUE_INTERNAL_ID_LIST: list[int] = list(TARGET_LEAGUE_INTERNAL_IDS.values())
INTERNATIONAL_LEAGUE_API_ID_LIST: list[int] = list(INTERNATIONAL_LEAGUE_API_IDS.values())
INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST: list[int] = list(INTERNATIONAL_LEAGUE_INTERNAL_IDS.values())

# Profondeur du rattrapage historique — perimetre valide le 21/08/2026.
# Une saison se definit par une fenetre de dates : le parametre season= de
# l'API est inoperant (il rend 408 110 evenements remontant a 1930).
# Volume mesure sur 2024-2025, six competitions : 2 042 matchs.
BACKFILL_SEASONS: list[str] = [
    "2021-2022",
    "2022-2023",
    "2023-2024",
    "2024-2025",
    "2025-2026",
]

# Effectifs reglementaires par championnat — invariant de segmentation.
# Releve le 22/08/2026 sur /api/events/ : 20, 20, 20, 18, 18, aucun club
# engage dans deux competitions. Un ecart interrompt la reconstruction du
# referentiel avant toute ecriture.
# La Ligue des champions (7) en est absente tant que les tirages de la phase
# de ligue n'ont pas eu lieu.
EFFECTIFS_REGLEMENTAIRES: dict[int, int] = {
    1: 20,  # Premier League
    3: 20,  # La Liga
    4: 20,  # Serie A
    5: 18,  # Bundesliga
    6: 18,  # Ligue 1
}

# Compétitions internationales de CLUBS : leurs stats sont déjà dans
# bzz_player_season_stats (agrégées par aggregate_all_leagues). On les sert en
# résolvant les joueurs « par présence de stats dans la compétition », pas par
# effectif de club engagé (aucun club n'a league_api_id dans cet ensemble).
# Étendu le 14/11/2026 à toutes les compétitions À ÉLIMINATION / À PARTICIPANTS
# où aucun club n'est « engagé » au sens du référentiel : coupes nationales
# (kind `cup` du registre), Ligue des champions CAF (29) et matchs amicaux de
# clubs (79). Même traitement : joueurs résolus par présence de stats dans la
# compétition, clubs déduits du calendrier.
NATIONAL_CUP_LEAGUE_IDS: frozenset[int] = frozenset(
    {39, 40, 44, 43, 41, 42, 92, 93, 110, 46, 56, 35, 81, 51, 48}
)
INTL_CLUB_COMP_LEAGUE_IDS: frozenset[int] = (
    frozenset({7, 8, 83, 90, 32, 33, 29, 79}) | NATIONAL_CUP_LEAGUE_IDS
)

# Sélections ayant de vraies stats de match Bzzoiro (les autres sélections du
# registre n'ont aucune bzz_player_match_stats -> non affichées au Temps 1).
SELECTION_STATS_LEAGUE_IDS: frozenset[int] = frozenset({27, 64, 65})
