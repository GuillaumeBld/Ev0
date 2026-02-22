"""API-Football integration for reliable player stats.

API-Football provides official data with xG/xA for top leagues.
Documentation: https://www.api-football.com/documentation-v3
"""

from typing import Any

import httpx

from app.config import settings

# League IDs in API-Football
LEAGUE_IDS = {
    "ligue_1": 61,
    "premier_league": 39,
}

# Current season (2024 = 2024/2025 season)
SEASON = 2024


class APIFootballClient:
    """Client for API-Football API."""

    BASE_URL = "https://v3.football.api-sports.io"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.api_football_key
        if not self.api_key:
            raise ValueError("API_FOOTBALL_KEY not configured")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "x-rapidapi-key": self.api_key or "",
            "x-rapidapi-host": "v3.football.api-sports.io",
        }

    async def _get(self, endpoint: str, params: dict | None = None) -> dict[str, Any]:
        """Make GET request to API-Football."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/{endpoint}",
                headers=self.headers,
                params=params or {},
            )
            resp.raise_for_status()
            return resp.json()

    async def get_teams(self, league: str) -> list[dict[str, Any]]:
        """Get all teams for a league.

        Args:
            league: ligue_1 or premier_league

        Returns:
            List of team dicts
        """
        league_id = LEAGUE_IDS.get(league)
        if not league_id:
            raise ValueError(f"Unknown league: {league}")

        data = await self._get(
            "teams",
            {
                "league": league_id,
                "season": SEASON,
            },
        )

        teams = []
        for entry in data.get("response", []):
            team_info = entry.get("team", {})
            teams.append(
                {
                    "api_football_id": str(team_info.get("id", "")),
                    "name": team_info.get("name", ""),
                    "logo": team_info.get("logo", ""),
                    "country": team_info.get("country", ""),
                }
            )

        return teams

    async def get_players(self, league: str, page: int = 1) -> tuple[list[dict], int]:
        """Get players for a league (paginated).

        Args:
            league: ligue_1 or premier_league
            page: Page number (1-indexed)

        Returns:
            Tuple of (players list, total pages)
        """
        league_id = LEAGUE_IDS.get(league)
        if not league_id:
            raise ValueError(f"Unknown league: {league}")

        data = await self._get(
            "players",
            {
                "league": league_id,
                "season": SEASON,
                "page": page,
            },
        )

        paging = data.get("paging", {})
        total_pages = paging.get("total", 1)

        players = []
        for entry in data.get("response", []):
            player_info = entry.get("player", {})
            stats_list = entry.get("statistics", [])

            # Get stats for this league
            stats = next(
                (s for s in stats_list if s.get("league", {}).get("id") == league_id),
                stats_list[0] if stats_list else {},
            )

            games = stats.get("games", {})
            goals = stats.get("goals", {})
            passes = stats.get("passes", {})
            shots = stats.get("shots", {})

            minutes = games.get("minutes") or 0
            goals_scored = goals.get("total") or 0
            assists_made = goals.get("assists") or 0
            total_shots = shots.get("total") or 0

            players.append(
                {
                    "api_football_id": str(player_info.get("id", "")),
                    "name": player_info.get("name", ""),
                    "firstname": player_info.get("firstname", ""),
                    "lastname": player_info.get("lastname", ""),
                    "team": stats.get("team", {}).get("name", ""),
                    "team_id": str(stats.get("team", {}).get("id", "")),
                    "position": games.get("position", ""),
                    "games": games.get("appearences") or 0,
                    "minutes": minutes,
                    "goals": goals_scored,
                    "assists": assists_made,
                    "shots": total_shots,
                    "key_passes": passes.get("key") or 0,
                    # API-Football doesn't have xG in basic endpoint
                    # We'll merge with scraped xG data
                }
            )

        return players, total_pages

    async def get_all_players(self, league: str) -> list[dict[str, Any]]:
        """Get all players for a league (handles pagination).

        Args:
            league: ligue_1 or premier_league

        Returns:
            Complete list of all players
        """
        all_players = []
        page = 1

        while True:
            players, total_pages = await self.get_players(league, page)
            all_players.extend(players)

            print(f"  API-Football: fetched page {page}/{total_pages} ({len(players)} players)")

            if page >= total_pages:
                break
            page += 1

        return all_players

    async def get_fixtures(
        self,
        league: str,
        status: str | None = None,
        next_n: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get fixtures for a league.

        Args:
            league: ligue_1 or premier_league
            status: Filter by status (NS=not started, FT=finished, etc.)
            next_n: Get next N fixtures

        Returns:
            List of fixture dicts
        """
        league_id = LEAGUE_IDS.get(league)
        if not league_id:
            raise ValueError(f"Unknown league: {league}")

        params: dict[str, str | int] = {
            "league": league_id,
            "season": SEASON,
        }

        if status:
            params["status"] = status
        if next_n:
            params["next"] = next_n

        data = await self._get("fixtures", params)

        fixtures = []
        for entry in data.get("response", []):
            fixture = entry.get("fixture", {})
            teams = entry.get("teams", {})
            goals = entry.get("goals", {})

            fixtures.append(
                {
                    "api_football_id": str(fixture.get("id", "")),
                    "date": fixture.get("date", ""),
                    "status": fixture.get("status", {}).get("short", ""),
                    "venue": fixture.get("venue", {}).get("name", ""),
                    "home_team": teams.get("home", {}).get("name", ""),
                    "home_team_id": str(teams.get("home", {}).get("id", "")),
                    "away_team": teams.get("away", {}).get("name", ""),
                    "away_team_id": str(teams.get("away", {}).get("id", "")),
                    "home_goals": goals.get("home"),
                    "away_goals": goals.get("away"),
                }
            )

        return fixtures

    async def get_player_stats_advanced(
        self,
        player_id: int,
        season: int = SEASON,
    ) -> dict[str, Any] | None:
        """Get advanced stats for a single player.

        Note: This endpoint may have different rate limits.

        Args:
            player_id: API-Football player ID
            season: Season year

        Returns:
            Player stats dict or None
        """
        try:
            data = await self._get(
                "players",
                {
                    "id": player_id,
                    "season": season,
                },
            )

            response = data.get("response", [])
            if response:
                return response[0]
            return None

        except Exception as e:
            print(f"Error fetching player {player_id}: {e}")
            return None


async def get_api_football_client() -> APIFootballClient | None:
    """Get API-Football client if API key configured."""
    if not settings.api_football_key:
        return None
    return APIFootballClient()
