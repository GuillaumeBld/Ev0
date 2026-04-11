"""Bzzoiro API constants — single source of truth for league api_ids."""

# Bzzoiro api_ids for the 6 target leagues.
# These are the values returned by the Bzzoiro API in league.api_id
# and used as the `league` query parameter when filtering events/stats.
TARGET_LEAGUE_API_IDS: dict[str, int] = {
    "premier_league": 17,
    "ligue_1": 34,
    "bundesliga": 35,
    "la_liga": 8,
    "serie_a": 23,
    "champions_league": 7,
}

# Ordered list for convenience
TARGET_LEAGUE_API_ID_LIST: list[int] = list(TARGET_LEAGUE_API_IDS.values())

# Season identifier used throughout the codebase
CURRENT_SEASON = "2025-2026"
SEASON_START_DATE = "2025-08-01"
