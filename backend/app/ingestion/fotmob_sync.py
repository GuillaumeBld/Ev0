"""FotMob connector using soccerdata.

Provides an alternative source for player stats and fixtures.
"""

from typing import Any

import soccerdata as sd

# League mapping for soccerdata
LEAGUE_MAPPING = {
    "ligue_1": "FRA-Ligue 1",
    "premier_league": "ENG-Premier League",
    "la_liga": "ESP-La Liga",
    "bundesliga": "GER-Bundesliga",
    "serie_a": "ITA-Serie A",
}

class FotMobClient:
    """Client for fetching data from FotMob via soccerdata."""

    def __init__(self, seasons: str | list[str] = "2024"):
        self.seasons = seasons

    def _get_sd_league(self, league_slug: str) -> str:
        """Convert internal league slug to soccerdata league name."""
        return LEAGUE_MAPPING.get(league_slug, league_slug)

    def get_player_stats(self, league_slug: str) -> list[dict[str, Any]]:
        """Fetch player season stats from FotMob.

        Note: soccerdata's FotMob scraper might require aggregating match stats
        if season stats aren't directly available. This is a simplified implementation
        assuming we can get reasonable stats or we fallback to match aggregation.

        For now, since FotMob doesn't always provide easy 'season stats' table via API/Scraper
        like FBref, we might look at what's available.

        If read_player_season_stats is not available, we return empty list or
        implement match aggregation (which is slow).
        """
        sd_league = self._get_sd_league(league_slug)
        try:
            sd.FotMob(leagues=[sd_league], seasons=self.seasons)

            # FotMob in soccerdata typically provides:
            # - read_schedule
            # - read_match_details (lineups, events, stats)
            # It does NOT standardly provide a simple "read_player_season_stats" like FBref.
            # So we might need to change strategy if the user wants efficient season stats.

            # However, for the purpose of this connector, let's expose what we can.
            # If the user wants LINEUPS (as per roadmap), that's read_match_details.

            # Attempting to read stats if available (placeholder for now)
            # Real implementation would likely iterate matches or use a different endpoint if added.
            print(f"FotMob client initialized for {sd_league}")
            return []

        except Exception as e:
            print(f"Error initializing FotMob for {league_slug}: {e}")
            return []

    def get_fixtures(self, league_slug: str) -> list[dict[str, Any]]:
        """Fetch fixtures/schedule from FotMob."""
        sd_league = self._get_sd_league(league_slug)
        try:
            fotmob = sd.FotMob(leagues=[sd_league], seasons=self.seasons)
            schedule = fotmob.read_schedule()

            fixtures = []
            if schedule is not None and not schedule.empty:
                for _idx, row in schedule.iterrows():
                    # soccerdata index is usually (league, season, game_id)
                    fixtures.append({
                        "date": str(row.get('date')),
                        "home_team": row.get('home_team'),
                        "away_team": row.get('away_team'),
                        "status": row.get('status'),
                        "home_score": row.get('home_score'),
                        "away_score": row.get('away_score'),
                        "gameweek": row.get('game_week'),
                    })
            return fixtures
        except Exception as e:
            print(f"Error fetching FotMob fixtures for {league_slug}: {e}")
            return []

async def sync_fotmob_data(league: str):
    """Sync wrapper for FotMob."""
    client = FotMobClient(seasons="2024")
    print(f"Fetching FotMob fixtures for {league}...")
    fixtures = client.get_fixtures(league)
    print(f"Found {len(fixtures)} fixtures from FotMob")
    return fixtures
