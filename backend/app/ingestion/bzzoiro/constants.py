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
