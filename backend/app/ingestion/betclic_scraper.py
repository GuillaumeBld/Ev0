"""Betclic direct HTTP scraper via SSR HTML parsing.

Betclic's CDN API (offer.cdn.betclic.com) is DNS-blocked from non-FR IPs.
However, www.betclic.fr serves its Angular app with server-side rendered data
embedded in a ``<script id="ng-state" type="application/json">`` tag.

That SSR payload (a gRPC response cached under a hash key) contains:
- ``matches``: list of upcoming events (team names, kickoff times, match IDs)
- ``topMultiples``: accumulator selections — each selection has 1X2 odds and
  a ``competitionId`` we can use to filter by league.
- ``topPlayersCarousels``: featured goalscorer picks (up to 10 per request).

This is less comprehensive than the full CDN API (no complete per-match
goalscorer lists), but it works from the VPS without requiring Playwright.

Markets extracted:
- ``1x2``        — from topMultiples, filtered to the target competition.
- ``goalscorer`` — from topPlayersCarousels (Top des Buteurs carousel).

Competition IDs (Betclic numeric IDs):
- Ligue 1 (France) : 36
- Premier League    : 3
- Champions League  : 2

CLI usage (dry-run):
    python -m app.ingestion.betclic_scraper --dry-run
    python -m app.ingestion.betclic_scraper --league ligue_1 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx

from app.ingestion.direct_scrapers import MatchOdds, SelectionOdds

logger = logging.getLogger(__name__)

BOOKMAKER = "betclic"

# Betclic competition page URLs
LEAGUE_URLS: dict[str, str] = {
    "ligue_1": "https://www.betclic.fr/football-s1/ligue-1-s4/",
    "premier_league": "https://www.betclic.fr/football-s1/premier-league-s3/",
    "champions_league": "https://www.betclic.fr/football-s1/champions-league-s15/",
}

# Betclic numeric competition IDs (from SSR data)
COMPETITION_IDS: dict[str, str] = {
    "ligue_1": "36",
    "premier_league": "3",
    "champions_league": "2",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

# Rate-limit between page requests
_PAGE_SLEEP = 1.5


def _parse_kickoff(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # ISO format with trailing zeros: "2026-03-03T20:00:00.0000000Z"
        cleaned = re.sub(r"\.0+Z$", "+00:00", raw).replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _get_grpc_payload(ng_data: dict[str, Any]) -> dict[str, Any]:
    """Extract the gRPC response payload that contains 'matches'."""
    for _key, val in ng_data.items():
        if not isinstance(val, dict):
            continue
        response = val.get("response", {})
        if not isinstance(response, dict):
            continue
        payload = response.get("payload", {})
        if isinstance(payload, dict) and "matches" in payload:
            return payload
    return {}


def _parse_ng_state(html: str) -> dict[str, Any]:
    """Extract and parse the Angular ng-state JSON from page HTML."""
    match = re.search(
        r'<script[^>]*id="ng-state"[^>]*type="application/json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    # Also try reversed attribute order
    if not match:
        match = re.search(
            r'<script[^>]*type="application/json"[^>]*id="ng-state"[^>]*>(.*?)</script>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return {}


class BetclicHttpScraper:
    """Scrape Betclic odds via SSR HTML (no Playwright required).

    Flow per league:
    1. GET the competition page (e.g. /football-s1/ligue-1-s4/).
    2. Parse the Angular ``ng-state`` JSON embedded in the HTML.
    3. Find the gRPC payload (keyed by a hash, contains 'matches').
    4. Extract 1X2 odds from ``topMultiples`` (filter by competition_id).
    5. Extract goalscorer odds from ``topPlayersCarousels``.
    6. Merge into MatchOdds objects keyed by (home_team, away_team).
    """

    BOOKMAKER = "betclic"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _fetch_page(self, url: str) -> str:
        """Fetch a Betclic page and return raw HTML."""
        try:
            r = await self._client.get(url, timeout=20)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            logger.warning("BetclicHttpScraper: page fetch failed (%s): %s", url, exc)
            return ""

    def _extract_1x2_odds(
        self, payload: dict[str, Any], competition_id: str
    ) -> dict[str, list[SelectionOdds]]:
        """Extract 1X2 odds from topMultiples for the given competition.

        Returns a dict keyed by eventId → list of SelectionOdds.
        """
        result: dict[str, list[SelectionOdds]] = {}

        for multiple in payload.get("topMultiples", []):
            for sel in multiple.get("selections", []):
                if str(sel.get("competitionId", "")) != competition_id:
                    continue

                event_id = str(sel.get("eventId", ""))
                if not event_id:
                    continue

                selection = sel.get("selection", {})
                sel_name = selection.get("name", "")
                odds_raw = selection.get("odds")
                if not sel_name or odds_raw is None:
                    continue

                try:
                    odds_f = float(odds_raw)
                except (ValueError, TypeError):
                    continue

                if odds_f <= 1.0:
                    continue

                # Map selection name to canonical outcome
                # Betclic uses team names for 1X2, not "home/draw/away"
                # We record raw team name as player_name — the worker
                # will handle normalization downstream.
                sel_obj = SelectionOdds(
                    market_type="1x2",
                    player_name=sel_name,
                    odds=odds_f,
                    bookmaker=self.BOOKMAKER,
                    raw_data={
                        "betclic_event_id": event_id,
                        "event_name": sel.get("eventName", ""),
                        "competition_id": competition_id,
                    },
                )
                result.setdefault(event_id, []).append(sel_obj)

        return result

    def _extract_goalscorer_odds(
        self, payload: dict[str, Any], competition_id: str
    ) -> dict[str, list[SelectionOdds]]:
        """Extract goalscorer odds from topPlayersCarousels.

        Returns a dict keyed by matchId → list of SelectionOdds.
        Filters to the given competition_id when possible.
        """
        result: dict[str, list[SelectionOdds]] = {}

        for carousel in payload.get("topPlayersCarousels", []):
            for player in carousel.get("topPlayers", []):
                # Filter by competition when we can
                player_comp = str(player.get("competitionId", ""))
                if competition_id and player_comp and player_comp != competition_id:
                    continue

                match_id = str(player.get("matchId", ""))
                selection = player.get("selection", {})
                player_name = selection.get("name", "")
                odds_raw = selection.get("odds")
                market_name = player.get("marketName", "")

                if not player_name or odds_raw is None or not match_id:
                    continue

                try:
                    odds_f = float(odds_raw)
                except (ValueError, TypeError):
                    continue

                if odds_f <= 1.0:
                    continue

                # Market normalisation
                market_lower = market_name.lower()
                if any(x in market_lower for x in ("buteur", "scorer", "marque", "anytime")):
                    market_type = "goalscorer"
                elif any(x in market_lower for x in ("passeur", "assist")):
                    market_type = "assist"
                else:
                    market_type = "goalscorer"  # default for player carousels

                sel_obj = SelectionOdds(
                    market_type=market_type,
                    player_name=player_name,
                    odds=odds_f,
                    bookmaker=self.BOOKMAKER,
                    raw_data={
                        "betclic_match_id": match_id,
                        "match_name": player.get("matchName", ""),
                        "market_name": market_name,
                        "competition_id": player_comp,
                    },
                )
                result.setdefault(match_id, []).append(sel_obj)

        return result

    def _build_match_index(
        self, payload: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Build a lookup from matchId → match metadata."""
        index: dict[str, dict[str, Any]] = {}
        for m in payload.get("matches", []):
            match_id = str(m.get("matchId", ""))
            if match_id:
                index[match_id] = m
        # Also index from topMultiples eventId → event metadata
        for multiple in payload.get("topMultiples", []):
            for sel in multiple.get("selections", []):
                eid = str(sel.get("eventId", ""))
                if eid and eid not in index:
                    index[eid] = {
                        "matchId": eid,
                        "name": sel.get("eventName", ""),
                        "matchDateUtc": sel.get("eventDate", ""),
                        "contestants": [],  # not available here
                    }
        return index

    def _match_name_to_teams(
        self, name: str, contestants: list[dict]
    ) -> tuple[str, str]:
        """Return (home_team, away_team) from match data."""
        if len(contestants) >= 2:
            home = contestants[0].get("name", "")
            away = contestants[1].get("name", "")
            if home and away:
                return home, away

        # Fall back to parsing "Team A - Team B"
        for sep in (" - ", " vs ", " / ", " VS "):
            if sep in name:
                parts = name.split(sep, 1)
                return parts[0].strip(), parts[1].strip()
        return "", ""

    async def scrape_league(self, league: str) -> list[MatchOdds]:
        """Scrape one league's 1X2 and goalscorer odds via SSR HTML."""
        url = LEAGUE_URLS.get(league)
        comp_id = COMPETITION_IDS.get(league, "")
        if not url:
            logger.warning("BetclicHttpScraper: unknown league %s", league)
            return []

        html = await self._fetch_page(url)
        if not html:
            return []

        ng_data = _parse_ng_state(html)
        if not ng_data:
            logger.warning("BetclicHttpScraper: no ng-state found for %s", league)
            return []

        payload = _get_grpc_payload(ng_data)
        if not payload:
            logger.warning("BetclicHttpScraper: no gRPC payload found for %s", league)
            return []

        # Build match index and extract odds
        match_index = self._build_match_index(payload)
        odds_1x2 = self._extract_1x2_odds(payload, comp_id)
        odds_gs = self._extract_goalscorer_odds(payload, comp_id)

        # Merge all odds into MatchOdds objects
        # Key: (home_team, away_team) → MatchOdds
        matches: dict[str, MatchOdds] = {}

        for event_id, selections in odds_1x2.items():
            meta = match_index.get(event_id, {})
            name = meta.get("name", "")
            contestants = meta.get("contestants", [])
            kickoff_raw = meta.get("matchDateUtc", "")

            home_team, away_team = self._match_name_to_teams(name, contestants)
            if not home_team or not away_team:
                # Try from the selection's raw_data event_name
                event_name = selections[0].raw_data.get("event_name", "") if selections else ""
                home_team, away_team = self._match_name_to_teams(event_name, [])
            if not home_team or not away_team:
                continue

            key = f"{home_team}|{away_team}"
            if key not in matches:
                matches[key] = MatchOdds(
                    home_team=home_team,
                    away_team=away_team,
                    kickoff_utc=_parse_kickoff(kickoff_raw),
                    league=league,
                )

            # Deduplicate selections by player_name (same odds may appear multiple
            # times across different accumulator multiples)
            existing_names = {s.player_name for s in matches[key].selections}
            for sel in selections:
                if sel.player_name not in existing_names:
                    matches[key].selections.append(sel)
                    existing_names.add(sel.player_name)

        # Add goalscorer odds
        for match_id, gs_selections in odds_gs.items():
            meta = match_index.get(match_id, {})
            name = meta.get("name", "")
            contestants = meta.get("contestants", [])
            kickoff_raw = meta.get("matchDateUtc", "")

            # Use match_name from raw_data if meta is sparse
            if not name and gs_selections:
                name = gs_selections[0].raw_data.get("match_name", "")

            home_team, away_team = self._match_name_to_teams(name, contestants)
            if not home_team or not away_team:
                continue

            key = f"{home_team}|{away_team}"
            if key not in matches:
                matches[key] = MatchOdds(
                    home_team=home_team,
                    away_team=away_team,
                    kickoff_utc=_parse_kickoff(kickoff_raw),
                    league=league,
                )

            existing_names = {s.player_name for s in matches[key].selections}
            for sel in gs_selections:
                if sel.player_name not in existing_names:
                    matches[key].selections.append(sel)
                    existing_names.add(sel.player_name)

        result = list(matches.values())
        total_sel = sum(len(m.selections) for m in result)
        logger.info(
            "BetclicHttpScraper %s: %d matches, %d selections",
            league,
            len(result),
            total_sel,
        )
        return result


async def scrape_all_betclic(leagues: list[str]) -> list[MatchOdds]:
    """Scrape Betclic 1X2 and goalscorer odds for all leagues via HTTP SSR.

    No Playwright required — pure HTTP GET + HTML parsing.
    """
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
        scraper = BetclicHttpScraper(client)
        all_matches: list[MatchOdds] = []

        for league in leagues:
            try:
                matches = await scraper.scrape_league(league)
                all_matches.extend(matches)
                if len(leagues) > 1:
                    await asyncio.sleep(_PAGE_SLEEP)
            except Exception as exc:
                logger.error(
                    "BetclicHttpScraper failed for %s: %s", league, exc, exc_info=True
                )

    total_sel = sum(len(m.selections) for m in all_matches)
    logger.info(
        "scrape_all_betclic: %d total match-odds objects, %d total selections, %d leagues",
        len(all_matches),
        total_sel,
        len(leagues),
    )
    return all_matches


# ── CLI entry point ──────────────────────────────────────────────────────────


async def _cli_main(args: argparse.Namespace) -> None:
    leagues = [args.league] if args.league else ["ligue_1", "premier_league"]
    all_matches = await scrape_all_betclic(leagues)

    total_sel = sum(len(m.selections) for m in all_matches)
    print(f"\n{'=' * 60}")
    print(f"Betclic scrape: {len(all_matches)} matches, {total_sel} selections")
    print(f"{'=' * 60}")

    if not args.dry_run:
        print("Use --dry-run to inspect without storing to DB.")
        return

    for m in all_matches:
        print(f"\n  {m.home_team} vs {m.away_team}  [{m.league}]")
        if m.kickoff_utc:
            print(f"  Kickoff: {m.kickoff_utc.strftime('%Y-%m-%d %H:%M UTC')}")
        by_market: dict[str, list[SelectionOdds]] = {}
        for s in m.selections:
            by_market.setdefault(s.market_type, []).append(s)
        for mtype, sels in by_market.items():
            print(f"  [{mtype}]")
            for s in sorted(sels, key=lambda x: x.odds)[:10]:
                print(f"    {s.player_name}: {s.odds:.2f}")
            if len(sels) > 10:
                print(f"    ... +{len(sels) - 10} more")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Scrape Betclic odds via SSR HTML")
    parser.add_argument(
        "--league",
        choices=["ligue_1", "premier_league", "champions_league"],
        default=None,
    )
    parser.add_argument("--dry-run", action="store_true", help="Print without storing to DB")
    asyncio.run(_cli_main(parser.parse_args()))
