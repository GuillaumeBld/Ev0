"""Sofascore scraper — player statistics ingestion.

Fetches per-player stats from Sofascore's unofficial API.
Replaces FBref metrics that are no longer available:
  - Big Chances Created (BCC)     → replaces SCA
  - Accurate Crosses              → replaces FBref crosses
  - Through Balls                 → replaces Passes into Box (PPA)
  - Shots on Target               → added for Model C goalscorer
  - Touches in Attack Pen. Area   → added for Model C goalscorer
  - Key Passes                    → kept from Sofascore

Note: Sofascore is blocked on the VPS (Cloudflare). Fetch locally and
import the results via a one-off script, or configure a proxy.
"""

import asyncio
import httpx

# ── Sofascore tournament/season IDs ──────────────────────────────
LEAGUES: dict[str, dict] = {
    "ligue_1": {
        "tournament_id": 34,
        "season_id": 77356,
        "label": "Ligue 1 2025/26",
    },
    "premier_league": {
        "tournament_id": 17,
        "season_id": 76986,
        "label": "Premier League 2025/26",
    },
    "laliga": {
        "tournament_id": 8,
        "season_id": 77358,
        "label": "LaLiga 2025/26",
    },
    "bundesliga": {
        "tournament_id": 35,
        "season_id": 77360,
        "label": "Bundesliga 2025/26",
    },
    "serie_a": {
        "tournament_id": 23,
        "season_id": 77359,
        "label": "Serie A 2025/26",
    },
    "champions_league": {
        "tournament_id": 7,
        "season_id": 77571,
        "label": "Champions League 2025/26",
    },
}

# Fields fetched from Sofascore stats API
STAT_FIELDS = (
    "goals,assists,keyPasses,bigChancesCreated,accurateCrosses,totalCrosses,"
    "shotsOnTarget,totalShots,touchesInAttackPenaltyArea,"
    "throughBalls,minutesPlayed,appearances,rating"
)

BASE_URL = "https://api.sofascore.com/api/v1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

RATE_LIMIT_SECONDS = 1.5   # delay between requests
PAGE_SIZE = 100             # players per page


# ── Data model ────────────────────────────────────────────────────

class SofascorePlayerStats:
    """Player stats container from Sofascore."""

    def __init__(self, raw: dict):
        player = raw.get("player", {})
        stats = raw.get("statistics", {})

        self.sofascore_id: int = player.get("id", 0)
        self.name: str = player.get("name", "")
        self.position: str | None = _normalize_position(player.get("position"))
        self.team: str = raw.get("team", {}).get("name", "")
        self.team_sofascore_id: int = raw.get("team", {}).get("id", 0)

        # Sofascore raw counts (season totals)
        self.goals: int = stats.get("goals", 0) or 0
        self.assists: int = stats.get("assists", 0) or 0
        self.key_passes: int = stats.get("keyPasses", 0) or 0
        self.big_chances_created: int = stats.get("bigChancesCreated", 0) or 0
        self.accurate_crosses: int = stats.get("accurateCrosses", 0) or 0
        self.total_crosses: int = stats.get("totalCrosses", 0) or 0
        self.shots_on_target: int = stats.get("shotsOnTarget", 0) or 0
        self.total_shots: int = stats.get("totalShots", 0) or 0
        self.touches_attack_pen_area: int = stats.get("touchesInAttackPenaltyArea", 0) or 0
        self.through_balls: int = stats.get("throughBalls", 0) or 0
        self.minutes_played: int = stats.get("minutesPlayed", 0) or 0
        self.appearances: int = stats.get("appearances", 0) or 0
        self.rating: float = stats.get("rating", 0.0) or 0.0

    @property
    def minutes_per_90(self) -> float:
        return self.minutes_played / 90.0 if self.minutes_played > 0 else 0.0

    def per_90(self, value: int | float) -> float:
        """Convert season total to per-90-minute rate."""
        if self.minutes_played < 1:
            return 0.0
        return round((value / self.minutes_played) * 90, 4)

    def to_dict(self) -> dict:
        """Return per-90 stats dict ready to merge with Understat data."""
        return {
            "sofascore_id": self.sofascore_id,
            "name": self.name,
            "position": self.position,
            "team": self.team,
            "minutes_played": self.minutes_played,
            "appearances": self.appearances,
            # Season totals
            "goals": self.goals,
            "assists": self.assists,
            "key_passes": self.key_passes,
            "big_chances_created": self.big_chances_created,
            "accurate_crosses": self.accurate_crosses,
            "total_crosses": self.total_crosses,
            "shots_on_target": self.shots_on_target,
            "total_shots": self.total_shots,
            "touches_attack_pen_area": self.touches_attack_pen_area,
            "through_balls": self.through_balls,
            # Per-90 rates (used in pricing model)
            "key_passes_per_90": self.per_90(self.key_passes),
            "bcc_per_90": self.per_90(self.big_chances_created),
            "accurate_crosses_per_90": self.per_90(self.accurate_crosses),
            "shots_on_target_per_90": self.per_90(self.shots_on_target),
            "touches_attack_pen_area_per_90": self.per_90(self.touches_attack_pen_area),
            "through_balls_per_90": self.per_90(self.through_balls),
            "rating": self.rating,
        }


# ── HTTP helpers ──────────────────────────────────────────────────

async def _fetch_page(
    client: httpx.AsyncClient,
    tournament_id: int,
    season_id: int,
    offset: int,
) -> dict:
    """Fetch one page of player statistics from Sofascore."""
    url = (
        f"{BASE_URL}/tournament/{tournament_id}/season/{season_id}/statistics"
        f"?limit={PAGE_SIZE}&offset={offset}&order=-rating&fields={STAT_FIELDS}&accumulation=total"
    )
    resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=20.0)
    resp.raise_for_status()
    return resp.json()


async def fetch_league_players(
    tournament_id: int,
    season_id: int,
    max_players: int = 500,
) -> list[SofascorePlayerStats]:
    """Fetch all player stats for a league/season from Sofascore."""
    players: list[SofascorePlayerStats] = []

    async with httpx.AsyncClient() as client:
        offset = 0
        while offset < max_players:
            try:
                data = await _fetch_page(client, tournament_id, season_id, offset)
            except httpx.HTTPStatusError as e:
                print(f"  [Sofascore] HTTP {e.response.status_code} at offset {offset} — stopping")
                break
            except Exception as e:
                print(f"  [Sofascore] Error at offset {offset}: {e} — stopping")
                break

            results = data.get("results", [])
            if not results:
                break

            for entry in results:
                try:
                    players.append(SofascorePlayerStats(entry))
                except Exception as e:
                    print(f"  [Sofascore] Parse error for entry: {e}")

            offset += PAGE_SIZE
            if len(results) < PAGE_SIZE:
                break

            await asyncio.sleep(RATE_LIMIT_SECONDS)

    return players


async def fetch_all_leagues(
    leagues: dict | None = None,
) -> dict[str, list[SofascorePlayerStats]]:
    """Fetch player stats for all configured leagues."""
    target = leagues or LEAGUES
    results: dict[str, list[SofascorePlayerStats]] = {}

    for league_key, cfg in target.items():
        print(f"[Sofascore] Fetching {cfg['label']}...")
        players = await fetch_league_players(
            cfg["tournament_id"],
            cfg["season_id"],
        )
        results[league_key] = players
        print(f"  → {len(players)} players fetched")
        await asyncio.sleep(RATE_LIMIT_SECONDS * 2)

    return results


# ── Normalization helpers ─────────────────────────────────────────

def _normalize_position(raw: str | None) -> str | None:
    """Map Sofascore position strings to canonical FW/MF/DF/GK."""
    if not raw:
        return None
    p = raw.upper()
    mapping = {
        "F": "FW", "ST": "FW", "CF": "FW", "SS": "FW",
        "M": "MF", "AM": "MF", "CM": "MF", "DM": "MF", "LM": "MF", "RM": "MF",
        "D": "DF", "CB": "DF", "LB": "DF", "RB": "DF", "LWB": "DF", "RWB": "DF",
        "G": "GK", "GK": "GK",
    }
    return mapping.get(p, None)


def compute_league_averages(players: list[SofascorePlayerStats]) -> dict[str, float]:
    """
    Compute league-average per-90 stats from all players.
    Used for normalization in the pricing model.
    Only includes players with >= 450 minutes (5 full matches).
    """
    eligible = [p for p in players if p.minutes_played >= 450]
    if not eligible:
        return {}

    n = len(eligible)
    return {
        "bcc_per_90": sum(p.per_90(p.big_chances_created) for p in eligible) / n,
        "accurate_crosses_per_90": sum(p.per_90(p.accurate_crosses) for p in eligible) / n,
        "key_passes_per_90": sum(p.per_90(p.key_passes) for p in eligible) / n,
        "shots_on_target_per_90": sum(p.per_90(p.shots_on_target) for p in eligible) / n,
        "touches_attack_pen_area_per_90": sum(p.per_90(p.touches_attack_pen_area) for p in eligible) / n,
        "through_balls_per_90": sum(p.per_90(p.through_balls) for p in eligible) / n,
    }


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    async def main():
        all_data = await fetch_all_leagues()
        for league, players in all_data.items():
            avgs = compute_league_averages(players)
            print(f"\n=== {league.upper()} — League Averages (per 90) ===")
            for k, v in avgs.items():
                print(f"  {k}: {v:.4f}")
            print(f"  Top 5 players by BCC:")
            top = sorted(players, key=lambda p: p.big_chances_created, reverse=True)[:5]
            for p in top:
                d = p.to_dict()
                print(f"    {p.name} ({p.team}): BCC/90={d['bcc_per_90']}, xCross/90={d['accurate_crosses_per_90']}")

    asyncio.run(main())
