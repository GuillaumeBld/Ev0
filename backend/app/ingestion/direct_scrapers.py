"""Direct scrapers for French bookmakers: Betclic, Unibet, ParionsSport FDJ.

Uses Playwright to intercept network API responses from competition pages,
capturing odds for 1X2, goalscorer (buteur à tout moment), and assist
(passeur décisif) markets.

Strategy:
- Load competition page (not individual match page) per bookmaker
- Intercept all JSON API responses triggered by the page load
- Normalize selections into SelectionOdds / MatchOdds structures
- 3 bookmakers × 2 leagues = 6 page loads max per run

CLI usage (dry-run, no DB writes):
    python -m app.ingestion.direct_scrapers --league ligue_1 --dry-run
    python -m app.ingestion.direct_scrapers --bookmaker parionssport --dry-run
    python -m app.ingestion.direct_scrapers --headed  # show browser window
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class SelectionOdds:
    """One betting selection: a player (or outcome) with its decimal odds."""

    market_type: str  # "1x2" | "goalscorer" | "assist"
    player_name: str  # player name, or "home_win" / "draw" / "away_win" for 1x2
    odds: float
    bookmaker: str
    raw_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class MatchOdds:
    """A fixture with all collected betting selections across markets."""

    home_team: str
    away_team: str
    kickoff_utc: datetime | None
    league: str
    selections: list[SelectionOdds] = field(default_factory=list)


# ── URL / competition configuration ───────────────────────────────────────────

BETCLIC_URLS: dict[str, str] = {
    "ligue_1": "https://www.betclic.fr/football-s1/ligue-1-s4/",
    "premier_league": "https://www.betclic.fr/football-s1/premier-league-s3/",
    "champions_league": "https://www.betclic.fr/football-s1/ligue-des-champions-s5/",
}

UNIBET_URLS: dict[str, str] = {
    "ligue_1": "https://www.unibet.fr/sport/football/ligue-1",
    "premier_league": "https://www.unibet.fr/sport/football/premier-league",
    "champions_league": "https://www.unibet.fr/sport/football/champions-league",
}

PARIONSSPORT_BASE = "https://www.enligne.parionssport.fdj.fr"
PARIONSSPORT_API = f"{PARIONSSPORT_BASE}/services-api/sportsbookdata/current"

# Competition codes used by ParionsSport's REST API
PARIONSSPORT_COMP: dict[str, str] = {
    "ligue_1": "FOOT_FR_L1",
    "premier_league": "FOOT_AN_PL",
    "champions_league": "FOOT_EU_CL",
}

PARIONSSPORT_URLS: dict[str, str] = {
    "ligue_1": f"{PARIONSSPORT_BASE}/paris-football/ligue-1",
    "premier_league": f"{PARIONSSPORT_BASE}/paris-football/premier-league-anglaise",
    "champions_league": f"{PARIONSSPORT_BASE}/paris-football/ligue-des-champions",
}

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _normalize_market(label: str) -> str | None:
    """Map a raw market label to our canonical market_type, or None if unknown."""
    s = label.lower().strip()
    if any(x in s for x in ("résultat", "resultat", "1x2", "match result", "1 x 2", "vainqueur du match")):
        return "1x2"
    if any(x in s for x in ("buteur", "goalscorer", "marquer un but", "scorer", "anytime scorer")):
        return "goalscorer"
    if any(x in s for x in ("passeur", "assist", "passe décisive", "passe decisive")):
        return "assist"
    return None


def _parse_datetime(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        s = str(raw)
        # Handle epoch milliseconds
        if s.isdigit() and len(s) == 13:
            return datetime.fromtimestamp(int(s) / 1000, tz=UTC)
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError, OSError):
        return None


def _deep_get(d: Any, *keys: Any) -> Any:
    """Safe nested dict/list access. Integer keys index into lists."""
    for k in keys:
        if d is None:
            return None
        if isinstance(k, int) and isinstance(d, list):
            d = d[k] if 0 <= k < len(d) else None
        elif isinstance(d, dict):
            d = d.get(k)
        else:
            return None
    return d


def _extract_list(body: Any, *candidate_keys: str) -> list:
    """Find the first non-empty list under any of the candidate_keys in body."""
    if isinstance(body, list) and body:
        return body
    if isinstance(body, dict):
        for key in candidate_keys:
            val = body.get(key)
            if isinstance(val, list) and val:
                return val
            # One level deeper
            if isinstance(val, dict):
                for k2 in candidate_keys:
                    v2 = val.get(k2)
                    if isinstance(v2, list) and v2:
                        return v2
    return []


# ── BetclicScraper ─────────────────────────────────────────────────────────────


class BetclicScraper:
    """Scrape Betclic competition pages via Playwright network interception.

    Betclic uses a CDN API (offer.cdn.betclic.fr) that the page fetches
    automatically on load. We capture all JSON responses from Betclic domains
    and parse events + markets from them.
    """

    BOOKMAKER = "betclic"

    # URL patterns whose JSON responses we want to capture
    # Capture JSON from betclic.fr or begmedia CDN
    _CAPTURE_RE = re.compile(
        r"(betclic\.fr|begmedia\.com|cdn\.betclic|offer\.cdn)",
        re.IGNORECASE,
    )
    PAGE_TIMEOUT_MS = 35_000

    def __init__(self) -> None:
        self._captured: list[dict[str, Any]] = []

    async def scrape_league(self, league: str, page: Any) -> list[MatchOdds]:
        url = BETCLIC_URLS.get(league)
        if not url:
            logger.warning("BetclicScraper: unknown league %s", league)
            return []

        self._captured = []

        async def _on_response(response: Any) -> None:
            try:
                if "json" not in response.headers.get("content-type", ""):
                    return
                if not self._CAPTURE_RE.search(response.url):
                    return
                body = await response.json()
                self._captured.append({"url": response.url, "body": body})
                logger.info("Betclic captured: %s", response.url)
            except Exception:
                pass

        page.on("response", _on_response)
        try:
            # Use domcontentloaded to avoid waiting for infinite background XHRs
            await page.goto(url, wait_until="domcontentloaded", timeout=self.PAGE_TIMEOUT_MS)
            await page.wait_for_timeout(8_000)  # let lazy API calls fire
        except Exception as exc:
            logger.warning("BetclicScraper page load error (%s): %s", league, exc)

        matches = self._parse_captured(league)
        logger.info(
            "BetclicScraper %s: %d matches, %d selections",
            league,
            len(matches),
            sum(len(m.selections) for m in matches),
        )
        return matches

    # ── Parsing ──

    def _parse_captured(self, league: str) -> list[MatchOdds]:
        matches: dict[str, MatchOdds] = {}
        for item in self._captured:
            events = _extract_list(item["body"], "events", "data", "items", "results", "competitions")
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                mo = self._parse_event(ev, league)
                if mo is None:
                    continue
                key = f"{mo.home_team}|{mo.away_team}"
                if key not in matches:
                    matches[key] = mo
                else:
                    matches[key].selections.extend(mo.selections)
        return list(matches.values())

    def _parse_event(self, event: dict, league: str) -> MatchOdds | None:
        # Extract team names — Betclic uses multiple shapes
        home_team = (
            _deep_get(event, "homeTeam", "name")
            or _deep_get(event, "home_team")
            or _deep_get(event, "competitors", 0, "name")
            or event.get("home")
        )
        away_team = (
            _deep_get(event, "awayTeam", "name")
            or _deep_get(event, "away_team")
            or _deep_get(event, "competitors", 1, "name")
            or event.get("away")
        )
        if not home_team or not away_team:
            # Try splitting "TeamA / TeamB" style names
            name_field = event.get("name") or event.get("label") or ""
            for sep in (" / ", " - ", " vs ", " VS "):
                if sep in name_field:
                    parts = name_field.split(sep, 1)
                    home_team, away_team = parts[0].strip(), parts[1].strip()
                    break
        if not home_team or not away_team:
            return None

        kickoff = _parse_datetime(
            event.get("startDate") or event.get("start_date") or event.get("date") or event.get("startTime")
        )

        mo = MatchOdds(
            home_team=str(home_team),
            away_team=str(away_team),
            kickoff_utc=kickoff,
            league=league,
        )

        # Parse markets array
        markets = event.get("markets") or event.get("prices") or []
        if isinstance(markets, dict):
            markets = list(markets.values())

        for market in markets:
            if not isinstance(market, dict):
                continue
            label = market.get("label") or market.get("name") or market.get("type") or ""
            market_type = _normalize_market(str(label))

            # Shortcut: detect 1X2 from direct price keys
            if not market_type and all(k in market for k in ("homeOdds", "drawOdds", "awayOdds")):
                market_type = "1x2"
            if not market_type:
                continue

            if market_type == "1x2" and not market.get("selections") and not market.get("outcomes"):
                for odds_key, pname in [("homeOdds", "home_win"), ("drawOdds", "draw"), ("awayOdds", "away_win")]:
                    val = market.get(odds_key)
                    if val:
                        try:
                            mo.selections.append(
                                SelectionOdds("1x2", pname, float(val), self.BOOKMAKER, {"label": label})
                            )
                        except (ValueError, TypeError):
                            pass
            else:
                outcomes = market.get("selections") or market.get("outcomes") or market.get("choices") or []
                for sel in outcomes:
                    if not isinstance(sel, dict):
                        continue
                    name = str(sel.get("name") or sel.get("label") or "")
                    odds_raw = sel.get("odds") or sel.get("price") or sel.get("value")
                    if not name or not odds_raw:
                        continue
                    if market_type == "1x2":
                        nl = name.lower()
                        if "home" in nl or str(home_team).lower() in nl:
                            name = "home_win"
                        elif "away" in nl or str(away_team).lower() in nl:
                            name = "away_win"
                        elif "draw" in nl or "nul" in nl or "x" == nl:
                            name = "draw"
                    try:
                        mo.selections.append(
                            SelectionOdds(market_type, name, float(odds_raw), self.BOOKMAKER, {"label": label})
                        )
                    except (ValueError, TypeError):
                        pass

        # Fallback: look for 1X2 directly on event-level keys
        if not any(s.market_type == "1x2" for s in mo.selections):
            for h_key, d_key, a_key in [
                ("odds1", "oddsX", "odds2"),
                ("homePrice", "drawPrice", "awayPrice"),
                ("cote1", "coteN", "cote2"),
                ("priceHome", "priceDraw", "priceAway"),
            ]:
                h = event.get(h_key)
                d = event.get(d_key)
                a = event.get(a_key)
                if h and d and a:
                    try:
                        mo.selections.extend([
                            SelectionOdds("1x2", "home_win", float(h), self.BOOKMAKER),
                            SelectionOdds("1x2", "draw", float(d), self.BOOKMAKER),
                            SelectionOdds("1x2", "away_win", float(a), self.BOOKMAKER),
                        ])
                        break
                    except (ValueError, TypeError):
                        pass

        return mo if mo.selections else None


# ── UnibetScraper ──────────────────────────────────────────────────────────────


class UnibetScraper:
    """Scrape Unibet competition pages via Playwright, intercepting Kambi API calls.

    Unibet is powered by Kambi Group. Their API returns events with betOffers
    (markets) and outcomes. Kambi decimal odds are expressed as integers × 1000
    (e.g. 1500 = 1.50).
    """

    BOOKMAKER = "unibet"

    PAGE_TIMEOUT_MS = 35_000

    def __init__(self) -> None:
        self._captured: list[dict[str, Any]] = []

    async def scrape_league(self, league: str, page: Any) -> list[MatchOdds]:
        url = UNIBET_URLS.get(league)
        if not url:
            logger.warning("UnibetScraper: unknown league %s", league)
            return []

        self._captured = []

        async def _on_response(response: Any) -> None:
            try:
                ct = response.headers.get("content-type", "")
                if "json" not in ct:
                    return
                body = await response.json()
                # Log all captured URLs at INFO so we can identify the real API
                logger.info("Unibet captured JSON: %s", response.url)
                self._captured.append({"url": response.url, "body": body})
            except Exception:
                pass

        page.on("response", _on_response)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.PAGE_TIMEOUT_MS)
            await page.wait_for_timeout(8_000)
        except Exception as exc:
            logger.warning("UnibetScraper page load error (%s): %s", league, exc)

        matches = self._parse_captured(league)
        logger.info(
            "UnibetScraper %s: %d matches, %d selections",
            league,
            len(matches),
            sum(len(m.selections) for m in matches),
        )
        return matches

    # ── Parsing ──

    def _parse_captured(self, league: str) -> list[MatchOdds]:
        matches: dict[str, MatchOdds] = {}

        for item in self._captured:
            body = item["body"]
            if not isinstance(body, dict):
                continue

            # Kambi response shapes:
            # { events: [...] }  or  { liveEvents: [...], upcomingEvents: [...] }
            all_events: list[dict] = []
            for key in ("events", "liveEvents", "upcomingEvents", "data"):
                val = body.get(key)
                if isinstance(val, list):
                    all_events.extend(val)

            for ev in all_events:
                if not isinstance(ev, dict):
                    continue
                mo = self._parse_kambi_event(ev, league)
                if mo is None:
                    continue
                key = f"{mo.home_team}|{mo.away_team}"
                if key not in matches:
                    matches[key] = mo
                else:
                    matches[key].selections.extend(mo.selections)

        return list(matches.values())

    def _parse_kambi_event(self, ev: dict, league: str) -> MatchOdds | None:
        # Kambi wraps event data under "event" key, or directly
        ev_data = ev.get("event", ev)

        # Extract teams from participants or direct fields
        participants = ev_data.get("participants", [])
        if len(participants) >= 2:
            home_team = participants[0].get("name", "")
            away_team = participants[1].get("name", "")
        else:
            home_team = ev_data.get("homeName") or ev_data.get("home") or ""
            away_team = ev_data.get("awayName") or ev_data.get("away") or ""

        if not home_team or not away_team:
            name_field = ev_data.get("name") or ev_data.get("englishName") or ""
            for sep in (" vs ", " - ", " / "):
                if sep in name_field:
                    parts = name_field.split(sep, 1)
                    home_team, away_team = parts[0].strip(), parts[1].strip()
                    break

        if not home_team or not away_team:
            return None

        kickoff = _parse_datetime(ev_data.get("start") or ev_data.get("startTime") or ev_data.get("date"))

        mo = MatchOdds(
            home_team=str(home_team),
            away_team=str(away_team),
            kickoff_utc=kickoff,
            league=league,
        )

        # betOffers = markets in Kambi terminology
        bet_offers = ev.get("betOffers") or ev_data.get("betOffers") or []
        for offer in bet_offers:
            if not isinstance(offer, dict):
                continue
            criterion = offer.get("criterion") or {}
            label = criterion.get("label") or offer.get("type") or offer.get("name") or ""
            market_type = _normalize_market(str(label))
            if not market_type:
                continue

            outcomes = offer.get("outcomes", [])
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                name = str(outcome.get("label") or outcome.get("participant") or "")
                odds_raw = outcome.get("odds")
                if odds_raw is None:
                    continue
                try:
                    odds_f = float(odds_raw)
                    # Kambi uses milliunits (e.g. 1500 = 1.500)
                    if odds_f > 100:
                        odds_f = odds_f / 1000.0
                except (ValueError, TypeError):
                    continue

                if market_type == "1x2":
                    outcome_type = outcome.get("type", "")
                    nl = name.lower()
                    if outcome_type == "OT_ONE" or "home" in nl or str(home_team).lower() in nl:
                        name = "home_win"
                    elif outcome_type == "OT_CROSS" or "draw" in nl or "nul" in nl:
                        name = "draw"
                    elif outcome_type == "OT_TWO" or "away" in nl or str(away_team).lower() in nl:
                        name = "away_win"

                if name and odds_f > 1.0:
                    mo.selections.append(
                        SelectionOdds(market_type, name, odds_f, self.BOOKMAKER, {"offer_label": label})
                    )

        return mo if mo.selections else None


# ── ParionsSportScraper ────────────────────────────────────────────────────────


class ParionsSportScraper:
    """Scrape ParionsSport FDJ via their REST API, with Playwright fallback.

    ParionsSport exposes a JSON API at /services-api/sportsbookdata/current/.
    HTTP is tried first (fast, no browser needed). If it fails (e.g. geo-block
    or structural change), Playwright intercepts the same calls made by the page.
    """

    BOOKMAKER = "parionssport"
    PAGE_TIMEOUT_MS = 45_000

    _HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
        "User-Agent": _BROWSER_UA,
        "Referer": PARIONSSPORT_BASE + "/",
    }

    def __init__(self) -> None:
        self._captured: list[dict[str, Any]] = []

    async def scrape_league(self, league: str, page: Any | None = None) -> list[MatchOdds]:
        # HTTP first
        try:
            matches = await self._scrape_http(league)
            if matches:
                logger.info("ParionsSport %s: %d matches via HTTP", league, len(matches))
                return matches
            logger.warning("ParionsSport HTTP returned 0 matches for %s — trying Playwright", league)
        except Exception as exc:
            logger.warning("ParionsSport HTTP error (%s): %s — trying Playwright", league, exc)

        # Playwright fallback
        if page is not None:
            matches = await self._scrape_playwright(league, page)
            logger.info("ParionsSport %s: %d matches via Playwright", league, len(matches))
            return matches

        logger.error("ParionsSport: HTTP failed and no Playwright page available for %s", league)
        return []

    # ── HTTP path ──

    async def _scrape_http(self, league: str) -> list[MatchOdds]:
        comp_code = PARIONSSPORT_COMP.get(league)
        if not comp_code:
            return []

        async with httpx.AsyncClient(headers=self._HEADERS, follow_redirects=True) as client:
            resp = await client.get(
                f"{PARIONSSPORT_API}/events/sport/FOOT",
                params={"competitionCode": comp_code},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

        events = _extract_list(data, "events", "data", "items", "results")
        if not events:
            logger.warning("ParionsSport HTTP: no events in response for %s", league)
            return []

        matches: list[MatchOdds] = []
        async with httpx.AsyncClient(headers=self._HEADERS, follow_redirects=True) as client:
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                mo = await self._parse_http_event(ev, league, client)
                if mo:
                    matches.append(mo)
        return matches

    async def _parse_http_event(
        self, event: dict, league: str, client: httpx.AsyncClient
    ) -> MatchOdds | None:
        home_team, away_team = self._extract_teams(event)
        if not home_team or not away_team:
            return None

        kickoff = _parse_datetime(
            event.get("startDate") or event.get("date") or event.get("scheduledStartDate")
        )
        mo = MatchOdds(
            home_team=home_team,
            away_team=away_team,
            kickoff_utc=kickoff,
            league=league,
        )

        # Parse inline markets
        for market in _extract_list(event, "markets", "bets", "betTypes"):
            if isinstance(market, dict):
                self._parse_market(market, mo)

        # Fetch markets from dedicated endpoint if we didn't find any
        if not mo.selections:
            event_id = event.get("id") or event.get("eventId") or event.get("code")
            if event_id:
                try:
                    r = await client.get(f"{PARIONSSPORT_API}/events/{event_id}/markets", timeout=10)
                    if r.status_code == 200:
                        mkt_data = r.json()
                        for m in _extract_list(mkt_data, "markets", "bets", "data"):
                            if isinstance(m, dict):
                                self._parse_market(m, mo)
                except Exception as exc:
                    logger.debug("ParionsSport markets fetch error (event %s): %s", event_id, exc)

        return mo if mo.selections else None

    # ── Playwright fallback ──

    async def _scrape_playwright(self, league: str, page: Any) -> list[MatchOdds]:
        url = PARIONSSPORT_URLS.get(league, "")
        if not url:
            return []

        self._captured = []

        async def _on_response(response: Any) -> None:
            try:
                if "json" not in response.headers.get("content-type", ""):
                    return
                if "parionssport" not in response.url and "fdj.fr" not in response.url:
                    return
                body = await response.json()
                self._captured.append({"url": response.url, "body": body})
                logger.debug("ParionsSport captured %s", response.url)
            except Exception:
                pass

        page.on("response", _on_response)
        try:
            await page.goto(url, wait_until="networkidle", timeout=self.PAGE_TIMEOUT_MS)
            await page.wait_for_timeout(3_000)
        except Exception as exc:
            logger.warning("ParionsSport Playwright error (%s): %s", league, exc)

        matches: dict[str, MatchOdds] = {}
        for item in self._captured:
            events = _extract_list(item["body"], "events", "data", "items", "results")
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                home_team, away_team = self._extract_teams(ev)
                if not home_team or not away_team:
                    continue
                mo = MatchOdds(
                    home_team=home_team,
                    away_team=away_team,
                    kickoff_utc=_parse_datetime(ev.get("startDate") or ev.get("date")),
                    league=league,
                )
                for market in _extract_list(ev, "markets", "bets", "betTypes"):
                    if isinstance(market, dict):
                        self._parse_market(market, mo)
                if mo.selections:
                    key = f"{mo.home_team}|{mo.away_team}"
                    if key not in matches:
                        matches[key] = mo
                    else:
                        matches[key].selections.extend(mo.selections)

        return list(matches.values())

    # ── Shared parsing helpers ──

    def _extract_teams(self, event: dict) -> tuple[str, str]:
        """Return (home_team, away_team) from an event dict, or ("", "")."""
        participants = event.get("participants") or event.get("competitors") or []
        home = away = ""
        if len(participants) >= 2:
            home = str(participants[0].get("name") or participants[0].get("label") or "")
            away = str(participants[1].get("name") or participants[1].get("label") or "")
        else:
            home = str(event.get("homeTeam") or event.get("homeName") or "")
            away = str(event.get("awayTeam") or event.get("awayName") or "")

        if not home or not away:
            name_field = event.get("name") or event.get("label") or ""
            for sep in (" / ", " - ", " vs ", " VS "):
                if sep in str(name_field):
                    parts = str(name_field).split(sep, 1)
                    home, away = parts[0].strip(), parts[1].strip()
                    break

        return home, away

    def _parse_market(self, market: dict, mo: MatchOdds) -> None:
        label = market.get("label") or market.get("name") or market.get("type") or ""
        market_type = _normalize_market(str(label))
        if not market_type:
            return

        outcomes = _extract_list(market, "outcomes", "selections", "bets", "choices")
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            name = str(outcome.get("label") or outcome.get("name") or outcome.get("participant") or "")
            odds_raw = outcome.get("odds") or outcome.get("price") or outcome.get("value")
            if not name or odds_raw is None:
                continue
            try:
                odds_f = float(odds_raw)
            except (ValueError, TypeError):
                continue

            if market_type == "1x2":
                nl = name.lower()
                if any(x in nl for x in ("domicile", "home", "1", mo.home_team.lower())):
                    name = "home_win"
                elif any(x in nl for x in ("extérieur", "exterieur", "away", "2", mo.away_team.lower())):
                    name = "away_win"
                elif any(x in nl for x in ("nul", "draw", "x")):
                    name = "draw"

            if name and odds_f > 1.0:
                mo.selections.append(
                    SelectionOdds(
                        market_type=market_type,
                        player_name=name,
                        odds=odds_f,
                        bookmaker=self.BOOKMAKER,
                        raw_data={"market_label": label},
                    )
                )


# ── Orchestrator ───────────────────────────────────────────────────────────────


async def scrape_all_direct(
    leagues: list[str],
    browser: Any,  # playwright Browser
) -> list[MatchOdds]:
    """Scrape Betclic, Unibet, and ParionsSport for the given leagues.

    ParionsSport is attempted via HTTP first (no browser page needed).
    Betclic and Unibet use Playwright with a shared browser context
    (one page navigated sequentially to avoid fingerprinting).

    Args:
        leagues: e.g. ["ligue_1", "premier_league"]
        browser: An already-launched Playwright Browser instance.

    Returns:
        All MatchOdds collected across bookmakers and leagues.
    """
    all_matches: list[MatchOdds] = []

    betclic = BetclicScraper()
    unibet = UnibetScraper()
    parions = ParionsSportScraper()

    # Single browser context shared across all scrapers (sequential = less bot detection)
    context = await browser.new_context(
        user_agent=_BROWSER_UA,
        locale="fr-FR",
        viewport={"width": 1280, "height": 800},
        extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"},
    )
    try:
        page = await context.new_page()

        # Spoof navigator.webdriver to reduce bot detection
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        for league in leagues:
            # ParionsSport: HTTP first, Playwright fallback (page now available)
            try:
                matches = await parions.scrape_league(league, page)
                all_matches.extend(matches)
                await asyncio.sleep(2)
            except Exception as exc:
                logger.error("ParionsSport failed for %s: %s", league, exc, exc_info=True)

            try:
                matches = await betclic.scrape_league(league, page)
                all_matches.extend(matches)
                await asyncio.sleep(2)
            except Exception as exc:
                logger.error("Betclic failed for %s: %s", league, exc, exc_info=True)

            try:
                matches = await unibet.scrape_league(league, page)
                all_matches.extend(matches)
                await asyncio.sleep(2)
            except Exception as exc:
                logger.error("Unibet failed for %s: %s", league, exc, exc_info=True)

        await page.close()
    finally:
        await context.close()

    logger.info(
        "scrape_all_direct: %d total match-odds objects (%d leagues × 3 bookmakers)",
        len(all_matches),
        len(leagues),
    )
    return all_matches


# ── CLI entry point ────────────────────────────────────────────────────────────


async def _cli_main(args: argparse.Namespace) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error(
            "Playwright not installed. Run:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        sys.exit(1)

    leagues: list[str] = [args.league] if args.league else ["ligue_1", "premier_league", "champions_league"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=not args.headed,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            # If a specific bookmaker is requested, only run that one
            if args.bookmaker == "parionssport":
                scraper = ParionsSportScraper()
                all_matches: list[MatchOdds] = []
                for league in leagues:
                    context = await browser.new_context(user_agent=_BROWSER_UA, locale="fr-FR")
                    page = await context.new_page()
                    try:
                        matches = await scraper.scrape_league(league, page)
                        all_matches.extend(matches)
                    finally:
                        await page.close()
                        await context.close()
            elif args.bookmaker == "betclic":
                scraper = BetclicScraper()  # type: ignore[assignment]
                all_matches = []
                context = await browser.new_context(user_agent=_BROWSER_UA, locale="fr-FR")
                page = await context.new_page()
                await page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                try:
                    for league in leagues:
                        matches = await scraper.scrape_league(league, page)
                        all_matches.extend(matches)
                finally:
                    await page.close()
                    await context.close()
            elif args.bookmaker == "unibet":
                scraper = UnibetScraper()  # type: ignore[assignment]
                all_matches = []
                context = await browser.new_context(user_agent=_BROWSER_UA, locale="fr-FR")
                page = await context.new_page()
                await page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                try:
                    for league in leagues:
                        matches = await scraper.scrape_league(league, page)
                        all_matches.extend(matches)
                finally:
                    await page.close()
                    await context.close()
            else:
                all_matches = await scrape_all_direct(leagues, browser)
        finally:
            await browser.close()

    if args.dry_run:
        total_sel = sum(len(m.selections) for m in all_matches)
        print(f"\n{'=' * 60}")
        print(f"DRY RUN — {len(all_matches)} matches, {total_sel} selections")
        print(f"{'=' * 60}")
        for m in all_matches:
            bk = {s.bookmaker for s in m.selections}
            print(f"\n  {m.home_team} vs {m.away_team}  [{m.league}]  bk={bk}")
            if m.kickoff_utc:
                print(f"  Kickoff: {m.kickoff_utc.isoformat()}")
            by_market: dict[str, list[SelectionOdds]] = {}
            for s in m.selections:
                by_market.setdefault(s.market_type, []).append(s)
            for mtype, sels in by_market.items():
                print(f"  [{mtype}]")
                for s in sels[:8]:
                    print(f"    {s.player_name}: {s.odds:.2f}  ({s.bookmaker})")
                if len(sels) > 8:
                    print(f"    ... +{len(sels) - 8} more")
    else:
        print(f"Collected {sum(len(m.selections) for m in all_matches)} selections from {len(all_matches)} matches.")
        print("Use --dry-run to inspect without storing.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Scrape French bookmaker odds (Betclic / Unibet / ParionsSport)")
    parser.add_argument("--league", choices=["ligue_1", "premier_league", "champions_league"], default=None, help="Single league")
    parser.add_argument(
        "--bookmaker", choices=["betclic", "unibet", "parionssport"], default=None, help="Single bookmaker"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print results, do not store to DB")
    parser.add_argument("--headed", action="store_true", help="Show browser window (useful for debugging)")
    asyncio.run(_cli_main(parser.parse_args()))
