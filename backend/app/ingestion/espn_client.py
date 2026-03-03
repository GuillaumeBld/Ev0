"""ESPN public API client for match events (goals, assists).

ESPN provides free access to match data via their public site API.
This is used as a fallback when FotMob returns 403 on match details.

Leagues supported:
- Ligue 1:       fra.1
- Premier League: eng.1
"""

import asyncio
import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

# ESPN league slugs for our supported leagues
ESPN_LEAGUE_SLUGS = {
    "ligue_1": "fra.1",
    "premier_league": "eng.1",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

_SITE_API = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# Event type IDs from ESPN that indicate goals (non-exhaustive; we filter by scoringPlay=True)
_OWN_GOAL_TEXTS = {"own goal", "own-goal"}
_PENALTY_TEXTS = {"penalty", "penalty kick", "pk"}


def _parse_minute(clock_value: float | None, display: str) -> int | None:
    """Convert ESPN clock value (seconds) or displayValue to an integer minute."""
    if display:
        # displayValue examples: "20'", "45'+6'", "90'+7'"
        raw = display.strip().rstrip("'")
        if "+" in raw:
            base, extra = raw.split("+", 1)
            try:
                return int(base.strip()) + int(extra.strip().rstrip("'"))
            except (ValueError, TypeError):
                pass
        try:
            return int(raw)
        except (ValueError, TypeError):
            pass
    if clock_value is not None:
        return int(clock_value // 60)
    return None


def _is_own_goal(event: dict) -> bool:
    type_text = (event.get("type", {}).get("text") or "").lower()
    return any(og in type_text for og in _OWN_GOAL_TEXTS) or event.get("ownGoal", False)


def _is_penalty(event: dict) -> bool:
    type_text = (event.get("type", {}).get("text") or "").lower()
    return any(pk in type_text for pk in _PENALTY_TEXTS) or event.get("penaltyKick", False)


def _parse_key_events(key_events: list[dict]) -> list[dict]:
    """Parse ESPN keyEvents into our standard event format.

    For scoring plays, ESPN participants list:
    - participants[0]: scorer
    - participants[1]: assister (if present)
    """
    events: list[dict] = []

    for ev in key_events:
        if not ev.get("scoringPlay", False):
            continue

        clock = ev.get("clock", {})
        minute = _parse_minute(clock.get("value"), clock.get("displayValue", ""))
        participants = ev.get("participants", [])

        if not participants:
            continue

        scorer_name = participants[0].get("athlete", {}).get("displayName", "")
        if not scorer_name:
            continue

        own_goal = _is_own_goal(ev)
        penalty = _is_penalty(ev)

        if own_goal:
            events.append({
                "player_name": scorer_name,
                "event_type": "own_goal",
                "minute": minute,
            })
        else:
            events.append({
                "player_name": scorer_name,
                "event_type": "penalty_goal" if penalty else "goal",
                "minute": minute,
            })

            # Second participant is the assister (not present for own goals)
            if len(participants) > 1:
                assister_name = participants[1].get("athlete", {}).get("displayName", "")
                if assister_name:
                    events.append({
                        "player_name": assister_name,
                        "event_type": "assist",
                        "minute": minute,
                    })

    return events


def _name_similarity(a: str, b: str) -> float:
    """Very simple name similarity: fraction of words in 'a' that appear in 'b'."""
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words:
        return 0.0
    return len(a_words & b_words) / len(a_words)


def _find_best_match(
    home_team: str,
    away_team: str,
    espn_events: list[dict],
) -> dict | None:
    """Find the ESPN event that best matches the given home/away team names.

    FotMob and ESPN occasionally disagree on which team is "home" due to
    data source differences. We match on both team names regardless of
    home/away designation, then score on the combined similarity.
    """
    best: dict | None = None
    best_score = 0.0

    for ev in espn_events:
        competitors = ev.get("competitions", [{}])[0].get("competitors", [])
        espn_home = next(
            (c.get("team", {}).get("displayName", "") for c in competitors if c.get("homeAway") == "home"),
            "",
        )
        espn_away = next(
            (c.get("team", {}).get("displayName", "") for c in competitors if c.get("homeAway") == "away"),
            "",
        )

        # Normal orientation (our home == ESPN home, our away == ESPN away)
        normal_score = (
            _name_similarity(home_team, espn_home) + _name_similarity(away_team, espn_away)
        ) / 2.0

        # Flipped orientation (ESPN may have home/away swapped vs FotMob)
        flipped_score = (
            _name_similarity(home_team, espn_away) + _name_similarity(away_team, espn_home)
        ) / 2.0

        combined = max(normal_score, flipped_score)

        if combined > best_score:
            best_score = combined
            best = ev

    # Require at least 0.4 similarity to avoid false matches
    if best_score >= 0.4:
        return best
    return None


class ESPNClient:
    """Client for fetching match events from ESPN's public API.

    Usage:
        async with httpx.AsyncClient() as http:
            client = ESPNClient(http)
            events = await client.get_match_events("ligue_1", "Paris Saint-Germain", "Lyon", "2026-02-23")
    """

    def __init__(self, http_client: httpx.AsyncClient):
        self._http = http_client

    async def _get_scoreboard(self, league_slug: str, match_date: str) -> list[dict]:
        """Fetch all events for a league on a given date (YYYY-MM-DD)."""
        espn_league = ESPN_LEAGUE_SLUGS.get(league_slug)
        if not espn_league:
            logger.warning("ESPNClient: unknown league_slug=%s", league_slug)
            return []

        # ESPN uses YYYYMMDD format
        date_compact = match_date.replace("-", "")
        url = f"{_SITE_API}/{espn_league}/scoreboard"

        try:
            resp = await self._http.get(
                url,
                params={"dates": date_compact},
                headers=_HEADERS,
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json().get("events", [])
        except Exception as exc:
            logger.warning("ESPNClient scoreboard error (%s %s): %s", league_slug, match_date, exc)
            return []

    async def _get_summary(self, league_slug: str, event_id: str) -> list[dict]:
        """Fetch keyEvents for an ESPN event."""
        espn_league = ESPN_LEAGUE_SLUGS.get(league_slug)
        if not espn_league:
            return []

        url = f"{_SITE_API}/{espn_league}/summary"
        try:
            resp = await self._http.get(
                url,
                params={"event": event_id},
                headers=_HEADERS,
                timeout=15.0,
            )
            resp.raise_for_status()
            return resp.json().get("keyEvents", [])
        except Exception as exc:
            logger.warning("ESPNClient summary error (event=%s): %s", event_id, exc)
            return []

    async def get_match_events(
        self,
        league: str,
        home_team: str,
        away_team: str,
        kickoff_date: str,
    ) -> list[dict]:
        """Fetch goal/assist events for a specific match.

        Args:
            league: "ligue_1" or "premier_league"
            home_team: Home team name (as stored in DB, e.g. "Paris Saint-Germain")
            away_team: Away team name
            kickoff_date: Date string "YYYY-MM-DD"

        Returns:
            List of event dicts: {"player_name": str, "event_type": str, "minute": int|None}
            event_type is one of: "goal", "assist", "own_goal", "penalty_goal"
        """
        # Try the given date ± 1 day in case of timezone edge cases
        target = date.fromisoformat(kickoff_date)
        dates_to_try = [
            kickoff_date,
            (target - timedelta(days=1)).isoformat(),
            (target + timedelta(days=1)).isoformat(),
        ]

        for d in dates_to_try:
            espn_events = await self._get_scoreboard(league, d)
            if not espn_events:
                continue

            matched = _find_best_match(home_team, away_team, espn_events)
            if matched:
                event_id = matched.get("id", "")
                logger.debug(
                    "ESPNClient: matched %s vs %s on %s → ESPN event %s (date tried: %s)",
                    home_team, away_team, kickoff_date, event_id, d,
                )
                key_events = await self._get_summary(league, event_id)
                return _parse_key_events(key_events)

        logger.warning(
            "ESPNClient: no ESPN match found for %s vs %s on %s",
            home_team, away_team, kickoff_date,
        )
        return []


async def fetch_match_events_espn(
    league: str,
    home_team: str,
    away_team: str,
    kickoff_date: str,
) -> list[dict]:
    """Convenience wrapper: fetch match events using ESPN.

    Args:
        league: "ligue_1" or "premier_league"
        home_team: Home team name
        away_team: Away team name
        kickoff_date: "YYYY-MM-DD"

    Returns:
        List of event dicts with player_name, event_type, minute.
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as http:
        client = ESPNClient(http)
        return await client.get_match_events(league, home_team, away_team, kickoff_date)


# Quick self-test
if __name__ == "__main__":
    async def _test() -> None:
        print("=== Testing ESPN client ===")

        # Ligue 1: AJ Auxerre vs Stade Rennais (2026-02-22)
        events = await fetch_match_events_espn(
            "ligue_1", "AJ Auxerre", "Stade Rennais", "2026-02-22"
        )
        print(f"\nLigue 1 (Auxerre vs Rennes): {len(events)} events")
        for ev in events:
            print(f"  {ev}")

        # Premier League: Liverpool vs Nottingham Forest (2026-02-22)
        events = await fetch_match_events_espn(
            "premier_league", "Liverpool", "Nottingham Forest", "2026-02-22"
        )
        print(f"\nPremier League (Liverpool vs Forest): {len(events)} events")
        for ev in events:
            print(f"  {ev}")

    asyncio.run(_test())
