"""Kambi direct HTTP scraper for player goalscorer odds.

Unibet and many other bookmakers are powered by Kambi Group. Their offering
API is publicly accessible with no authentication required.

Client used: ``ub`` (Unibet global / EU)

Markets extracted:
- ``To Score`` (betOfferType 127) — anytime goalscorer. One offer per player;
  player name is in ``outcomes[0].participant``.
- ``First Goal Scorer``  — first scorer market. One offer per match; player
  names are in ``outcomes[x].label``.

Both markets are stored as ``market_type="goalscorer"`` and
``bookmaker="unibet"``.

CLI usage (dry-run):
    python -m app.ingestion.kambi_scraper --dry-run
    python -m app.ingestion.kambi_scraper --league ligue_1 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.ingestion.direct_scrapers import MatchOdds, SelectionOdds

logger = logging.getLogger(__name__)

KAMBI_BASE = "https://eu-offering-api.kambicdn.com/offering/v2018"
KAMBI_CLIENT = "ub"  # Unibet global

LEAGUE_PATHS: dict[str, str] = {
    "ligue_1": "football/france/ligue_1",
    "premier_league": "football/england/premier_league",
    "champions_league": "football/champions_league",
}

LEAGUE_PARAMS: dict[str, dict[str, str]] = {
    "ligue_1": {"lang": "fr_FR", "market": "FR"},
    "premier_league": {"lang": "en_GB", "market": "GB"},
    "champions_league": {"lang": "en_GB", "market": "GB"},
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Rate-limit: pause between event-level requests
_EVENT_SLEEP = 0.3


def _to_decimal(kambi_odds: int | float | None) -> float | None:
    """Convert Kambi milliunits (e.g. 1500) to decimal (e.g. 1.50)."""
    if kambi_odds is None:
        return None
    try:
        v = float(kambi_odds)
        return round(v / 1000.0, 4) if v > 100 else v
    except (ValueError, TypeError):
        return None


def _parse_kickoff(start: str | None) -> datetime | None:
    if not start:
        return None
    try:
        return datetime.fromisoformat(start.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


class KambiScraper:
    """Scrape player goalscorer odds from Kambi's public offering API."""

    BOOKMAKER = "unibet"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def fetch_events(self, league: str) -> list[dict[str, Any]]:
        """Return upcoming events for a league."""
        path = LEAGUE_PATHS.get(league)
        if not path:
            logger.warning("KambiScraper: unknown league %s", league)
            return []

        url = f"{KAMBI_BASE}/{KAMBI_CLIENT}/listView/{path}.json"
        params = LEAGUE_PARAMS.get(league, {"lang": "en_GB", "market": "GB"})

        try:
            r = await self._client.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json().get("events", [])
        except Exception as exc:
            logger.warning("KambiScraper: events fetch failed for %s: %s", league, exc)
            return []

    async def fetch_bet_offers(self, event_id: int, league: str) -> list[dict[str, Any]]:
        """Return all betOffers for a single event."""
        url = f"{KAMBI_BASE}/{KAMBI_CLIENT}/betoffer/event/{event_id}.json"
        params = {**LEAGUE_PARAMS.get(league, {}), "includeParticipants": "true"}

        try:
            r = await self._client.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json().get("betOffers", [])
        except Exception as exc:
            logger.debug("KambiScraper: betOffers failed for event %d: %s", event_id, exc)
            return []

    def _parse_goalscorer_offers(self, offers: list[dict]) -> list[SelectionOdds]:
        """Extract goalscorer selections from a list of betOffers."""
        selections: list[SelectionOdds] = []

        for offer in offers:
            label = offer.get("criterion", {}).get("label", "")

            # "To Score" (EN) / "Marque" (FR) — anytime goalscorer, one offer per player
            if label in ("To Score", "Marque"):
                outcomes = offer.get("outcomes", [])
                if not outcomes:
                    continue
                out = outcomes[0]
                if out.get("status") not in ("OPEN", None):
                    continue
                # participant holds the player name; label may be "Yes"/"Oui"
                player_name = out.get("participant") or ""
                if not player_name or player_name.lower() in ("yes", "no", "oui", "non"):
                    continue
                odds = _to_decimal(out.get("odds"))
                if not odds or odds <= 1.0:
                    continue
                selections.append(
                    SelectionOdds(
                        market_type="goalscorer",
                        player_name=player_name,
                        odds=odds,
                        bookmaker=self.BOOKMAKER,
                        raw_data={"kambi_market": "To Score", "offer_id": offer.get("id")},
                    )
                )

            # "First Goal Scorer" (EN) / "Premier buteur" (FR) — one offer, multiple outcomes
            elif label in ("First Goal Scorer", "Premier buteur"):
                for out in offer.get("outcomes", []):
                    if out.get("status") not in ("OPEN", None):
                        continue
                    # participant or label holds the player name
                    player_name = out.get("participant") or out.get("label", "")
                    if not player_name or player_name.lower() in (
                        "no goal", "none", "aucun but", "no scorer",
                    ):
                        continue
                    odds = _to_decimal(out.get("odds"))
                    if not odds or odds <= 1.0:
                        continue
                    selections.append(
                        SelectionOdds(
                            market_type="goalscorer",
                            player_name=player_name,
                            odds=odds,
                            bookmaker=self.BOOKMAKER,
                            raw_data={
                                "kambi_market": "First Goal Scorer",
                                "participant_id": out.get("participantId"),
                            },
                        )
                    )

        # Deduplicate: keep anytime goalscorer ("To Score") over first scorer per player
        seen: dict[str, SelectionOdds] = {}
        for sel in selections:
            key = sel.player_name.lower()
            existing = seen.get(key)
            if existing is None or (
                sel.raw_data.get("kambi_market") == "To Score"
                and existing.raw_data.get("kambi_market") != "To Score"
            ):
                seen[key] = sel

        return list(seen.values())

    async def scrape_league(self, league: str) -> list[MatchOdds]:
        """Scrape one league: events → betOffers → MatchOdds."""
        events = await self.fetch_events(league)
        if not events:
            logger.info("KambiScraper %s: 0 events found", league)
            return []

        logger.info("KambiScraper %s: %d events to process", league, len(events))
        results: list[MatchOdds] = []

        for ev_wrapper in events:
            ev = ev_wrapper.get("event", ev_wrapper)
            event_id = ev.get("id")
            if not event_id:
                continue

            # Extract team names
            participants = ev.get("participants", [])
            if len(participants) >= 2:
                home_team = participants[0].get("name", "")
                away_team = participants[1].get("name", "")
            else:
                name = ev.get("name", "") or ev.get("englishName", "")
                parts = name.split(" - ", 1) if " - " in name else name.split(" vs ", 1)
                if len(parts) == 2:
                    home_team, away_team = parts[0].strip(), parts[1].strip()
                else:
                    logger.debug("KambiScraper: cannot parse teams from '%s'", name)
                    continue

            if not home_team or not away_team:
                continue

            kickoff = _parse_kickoff(ev.get("start"))
            mo = MatchOdds(
                home_team=home_team,
                away_team=away_team,
                kickoff_utc=kickoff,
                league=league,
            )

            # Only fetch betOffers for events with a kickoff in the future
            if kickoff and kickoff < datetime.now(UTC):
                continue

            offers = await self.fetch_bet_offers(event_id, league)
            mo.selections = self._parse_goalscorer_offers(offers)

            if mo.selections:
                results.append(mo)

            await asyncio.sleep(_EVENT_SLEEP)

        total_sel = sum(len(m.selections) for m in results)
        logger.info(
            "KambiScraper %s: %d matches with odds, %d total selections",
            league,
            len(results),
            total_sel,
        )
        return results


async def scrape_all_kambi(leagues: list[str]) -> list[MatchOdds]:
    """Scrape goalscorer odds for all leagues via Kambi HTTP API.

    No Playwright required — pure HTTP.
    """
    async with httpx.AsyncClient(headers=_HEADERS, follow_redirects=True) as client:
        scraper = KambiScraper(client)
        all_matches: list[MatchOdds] = []

        for league in leagues:
            try:
                matches = await scraper.scrape_league(league)
                all_matches.extend(matches)
            except Exception as exc:
                logger.error("KambiScraper failed for %s: %s", league, exc, exc_info=True)

    logger.info(
        "scrape_all_kambi: %d total match-odds objects across %d leagues",
        len(all_matches),
        len(leagues),
    )
    return all_matches


# ── CLI entry point ─────────────────────────────────────────────────────────


async def _cli_main(args: argparse.Namespace) -> None:

    leagues = [args.league] if args.league else ["ligue_1", "premier_league", "champions_league"]
    all_matches = await scrape_all_kambi(leagues)

    total_sel = sum(len(m.selections) for m in all_matches)
    print(f"\n{'=' * 60}")
    print(f"Kambi scrape: {len(all_matches)} matches, {total_sel} selections")
    print(f"{'=' * 60}")

    if not args.dry_run:
        print("Use --dry-run to inspect without storing to DB.")
        return

    for m in all_matches:
        print(f"\n  {m.home_team} vs {m.away_team}  [{m.league}]")
        if m.kickoff_utc:
            print(f"  Kickoff: {m.kickoff_utc.strftime('%Y-%m-%d %H:%M UTC')}")
        goal_sels = [s for s in m.selections if s.market_type == "goalscorer"]
        print(f"  Goalscorer selections: {len(goal_sels)}")
        for s in sorted(goal_sels, key=lambda x: x.odds)[:10]:
            mkt = s.raw_data.get("kambi_market", "")
            print(f"    {s.player_name}: {s.odds:.2f}  [{mkt}]")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Scrape Kambi (Unibet) goalscorer odds")
    parser.add_argument("--league", choices=["ligue_1", "premier_league", "champions_league"], default=None)
    parser.add_argument("--dry-run", action="store_true", help="Print without storing to DB")
    asyncio.run(_cli_main(parser.parse_args()))
