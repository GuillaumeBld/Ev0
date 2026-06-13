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
    "bundesliga": "https://www.betclic.fr/football-s1/bundesliga-s14/",
    "la_liga": "https://www.betclic.fr/football-s1/liga-s7/",
    "serie_a": "https://www.betclic.fr/football-s1/serie-a-s17/",
}

UNIBET_URLS: dict[str, str] = {
    "ligue_1": "https://www.unibet.fr/sport/football/ligue-1",
    "premier_league": "https://www.unibet.fr/sport/football/premier-league",
    "champions_league": "https://www.unibet.fr/sport/football/champions-league",
    "bundesliga": "https://www.unibet.fr/sport/football/bundesliga",
    "la_liga": "https://www.unibet.fr/sport/football/la-liga",
    "serie_a": "https://www.unibet.fr/sport/football/serie-a",
}

PARIONSSPORT_BASE = "https://www.parionssport.fdj.fr"
PARIONSSPORT_API = f"{PARIONSSPORT_BASE}/services-api/sportsbookdata/current"

# Competition codes used by ParionsSport's REST API
PARIONSSPORT_COMP: dict[str, str] = {
    "ligue_1": "FOOT_FR_L1",
    "premier_league": "FOOT_AN_PL",
    "champions_league": "FOOT_EU_CL",
    "bundesliga": "FOOT_AL_BL1",
    "la_liga": "FOOT_ES_D1",
    "serie_a": "FOOT_IT_D1",
}

PARIONSSPORT_URLS: dict[str, str] = {
    "ligue_1": f"{PARIONSSPORT_BASE}/paris-football/ligue-1",
    "premier_league": f"{PARIONSSPORT_BASE}/paris-football/premier-league-anglaise",
    "champions_league": f"{PARIONSSPORT_BASE}/paris-football/ligue-des-champions",
    "bundesliga": f"{PARIONSSPORT_BASE}/paris-football/bundesliga",
    "la_liga": f"{PARIONSSPORT_BASE}/paris-football/la-liga-espagnole",
    "serie_a": f"{PARIONSSPORT_BASE}/paris-football/serie-a-italienne",
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
    """Scrape Betclic match pages via Playwright DOM extraction.

    Betclic uses gRPC-web (not JSON REST) for its odds API, making network
    interception ineffective. Instead we navigate each match page individually,
    click the "Stats équipes et joueurs" tab, expand passeur market rows, and
    parse the page innerText.

    A dedicated browser context with a passthrough context.route handler is
    created per scrape_league call — this is required for Betclic pages to
    render full content (likely related to Angular service worker handling).
    """

    BOOKMAKER = "betclic"
    PAGE_TIMEOUT_MS = 35_000
    MAX_MATCHES_PER_LEAGUE = 8

    _JS_DISMISS_COOKIES = "document.querySelector('#popin_tc_privacy_button_2')?.click()"
    _JS_REMOVE_COOKIE_BANNER = "document.querySelector('#tc-privacy-wrapper')?.remove()"

    # Click the "Stats équipes et joueurs" tab
    _JS_CLICK_STATS_TAB = """
        () => {
            var t = [...document.querySelectorAll("a, li, [class*='tab']")]
                .find(function(el) {
                    return el.textContent.includes("Stats") &&
                           el.textContent.includes("quipes") &&
                           el.textContent.trim().length < 60;
                });
            if (t) { t.click(); return true; }
            return false;
        }
    """

    # Click every "voir plus" triangle inside passeur market containers only
    _JS_CLICK_SEE_MORE = """
        () => {
            var allEls = [...document.querySelectorAll("*")];
            var passeurTitles = allEls.filter(function(el) {
                var t = (el.textContent || "").trim();
                return t.includes("Joueur passeur d") && !t.includes("buteur") && t.length < 80;
            });
            var clicked = 0;
            for (var pi = 0; pi < passeurTitles.length; pi++) {
                var titleEl = passeurTitles[pi];
                var box = titleEl.parentElement;
                for (var d = 0; d < 25 && box; d++) {
                    var cls = box.className || "";
                    if (cls.includes("marketBox") || box.tagName.toLowerCase().startsWith("sports-market")) break;
                    box = box.parentElement;
                }
                if (!box) continue;
                var seeMores = [...box.querySelectorAll(".is-seeMore, [class*='seeMore']")];
                seeMores.forEach(function(btn) { try { btn.click(); clicked++; } catch(e) {} });
            }
            return clicked;
        }
    """

    async def scrape_league(self, league: str, page: Any) -> list[MatchOdds]:
        url = BETCLIC_URLS.get(league)
        if not url:
            logger.warning("BetclicScraper: unknown league %s", league)
            return []

        # Dedicated context required: context.route passthrough is what makes
        # Betclic pages render full content instead of only ~47 sidebar lines.
        ctx = await page.context.browser.new_context(
            locale="fr-FR",
            viewport={"width": 1280, "height": 900},
            user_agent=_BROWSER_UA,
            extra_http_headers={"Accept-Language": "fr-FR,fr;q=0.9"},
        )
        try:
            p = await ctx.new_page()
            await p.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            async def _passthrough(route: Any) -> None:
                await route.continue_()

            await ctx.route("**/*", _passthrough)

            match_links = await self._get_match_links(p, url)
            logger.info("BetclicScraper %s: %d match links found", league, len(match_links))

            all_matches: list[MatchOdds] = []
            for match_url in match_links[: self.MAX_MATCHES_PER_LEAGUE]:
                try:
                    mo = await self._scrape_match_page(p, match_url, league)
                    if mo:
                        all_matches.append(mo)
                except Exception as exc:
                    logger.warning("BetclicScraper: error on %s: %s", match_url, exc)
                await asyncio.sleep(1)

            logger.info(
                "BetclicScraper %s: %d matches, %d selections",
                league,
                len(all_matches),
                sum(len(m.selections) for m in all_matches),
            )
            return all_matches
        finally:
            await ctx.close()

    async def _get_match_links(self, page: Any, comp_url: str) -> list[str]:
        """Extract individual match URLs from a Betclic competition list page."""
        try:
            await page.goto(comp_url, wait_until="domcontentloaded", timeout=self.PAGE_TIMEOUT_MS)
        except Exception as exc:
            logger.warning("BetclicScraper: comp page load error: %s", exc)
            return []
        await page.wait_for_timeout(2000)
        await page.evaluate(self._JS_DISMISS_COOKIES)
        await page.evaluate(self._JS_REMOVE_COOKIE_BANNER)
        await page.wait_for_timeout(8000)

        links: list[str] = await page.evaluate("""
            () => {
                var seen = new Set();
                var results = [];
                [...document.querySelectorAll("a[href]")].forEach(function(a) {
                    var href = a.href;
                    if (seen.has(href)) return;
                    if (/betclic\\.fr.*\\/football[^/]*\\/[^/]+-m\\d{8,}/.test(href)) {
                        seen.add(href);
                        results.push(href);
                    }
                });
                return results;
            }
        """)
        return links

    async def _scrape_match_page(self, page: Any, url: str, league: str) -> MatchOdds | None:
        """Navigate to a match page and extract passeur (assist) odds from the DOM."""
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.PAGE_TIMEOUT_MS)
        except Exception as exc:
            logger.warning("BetclicScraper: match page load error (%s): %s", url, exc)
            return None

        await page.wait_for_timeout(2000)
        await page.evaluate(self._JS_DISMISS_COOKIES)
        await page.evaluate(self._JS_REMOVE_COOKIE_BANNER)
        await page.wait_for_timeout(12000)

        await page.evaluate(self._JS_CLICK_STATS_TAB)
        await page.wait_for_timeout(6000)

        # First scroll: load lazy content
        for y in range(0, 8000, 200):
            await page.evaluate(f"window.scrollTo(0, {y})")
            await page.wait_for_timeout(50)
        await page.wait_for_timeout(1000)

        # Expand hidden player rows in passeur market containers
        n = await page.evaluate(self._JS_CLICK_SEE_MORE)
        logger.info("BetclicScraper: %d see-more buttons clicked for %s", n, url)
        await page.wait_for_timeout(4000)

        # Re-scroll after expansion to load newly revealed rows
        for y in range(0, 12000, 200):
            await page.evaluate(f"window.scrollTo(0, {y})")
            await page.wait_for_timeout(50)
        await page.wait_for_timeout(1000)

        text: str = await page.evaluate(
            "document.body.innerText || document.body.textContent"
        )
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

        if len(lines) < 50:
            logger.warning(
                "BetclicScraper: only %d lines from %s — page may not have rendered",
                len(lines),
                url,
            )
            return None

        home_team = away_team = ""
        selections: list[SelectionOdds] = []
        seen_markets: set[str] = set()

        for i, line in enumerate(lines):
            if (
                "Joueur passeur décisif" in line
                and "buteur ou" not in line
                and line not in seen_markets
            ):
                seen_markets.add(line)
                teams_data = self._parse_passeur_section(lines, i, line)
                team_names = list(teams_data.keys())
                if not home_team and team_names:
                    home_team = team_names[0]
                    away_team = team_names[1] if len(team_names) > 1 else ""
                for tname in team_names:
                    for entry in teams_data[tname]:
                        selections.append(
                            SelectionOdds(
                                market_type="assist",
                                player_name=entry["player"],
                                odds=entry["odds_1"],
                                bookmaker=self.BOOKMAKER,
                                raw_data={
                                    "market": line,
                                    "team": tname,
                                    "is_home": tname == home_team,
                                },
                            )
                        )

        if not home_team:
            home_team, away_team = self._detect_teams_from_url(url)

        if not selections:
            logger.info("BetclicScraper: no passeur selections found for %s", url)
            return None

        mo = MatchOdds(
            home_team=home_team,
            away_team=away_team,
            kickoff_utc=None,
            league=league,
        )
        mo.selections = selections
        return mo

    def _detect_teams_from_url(self, url: str) -> tuple[str, str]:
        """Best-effort team name extraction from the match URL slug."""
        slug = url.rstrip("/").split("/")[-1]
        m = re.match(r"^(.+?)-m\d+$", slug)
        if not m:
            return "", ""
        teams_part = m.group(1)
        if "-vs-" in teams_part:
            parts = teams_part.split("-vs-", 1)
        else:
            words = teams_part.split("-")
            mid = max(1, len(words) // 2)
            parts = ["-".join(words[:mid]), "-".join(words[mid:])]
        home = " ".join(w.capitalize() for w in parts[0].split("-"))
        away = " ".join(w.capitalize() for w in parts[1].split("-")) if len(parts) > 1 else ""
        return home, away

    def _parse_passeur_section(
        self, lines: list[str], start_idx: int, market_name: str
    ) -> dict[str, list[dict]]:
        """Parse the innerText lines of a passeur market section.

        Returns {team_name: [{"player": str, "odds_1": float, "odds_2": float|None}]}.
        The first team key is home, the second is away (Betclic DOM order).
        """
        teams: dict[str, list[dict]] = {}
        cur_team: str | None = None
        cur_player: str | None = None
        players_seen: set[str] = set()
        i = start_idx + 1
        skip_set = {"1 fois ou +", "2 fois ou +", "SuperSub 90'", "1", "2", market_name}
        end_keywords = [
            "Buteur et passeur",
            "Double chance",
            "Triple chance",
            "Score exact",
            "Joueur passeur décisif",
            "Joueur décisif",
        ]

        while i < len(lines):
            line = lines[i]
            if any(line.startswith(k) for k in end_keywords) and i > start_idx + 3:
                break
            if line in skip_set:
                i += 1
                continue
            try:
                val = float(line.replace(",", "."))
                if 1.01 <= val <= 500 and cur_team and cur_player:
                    entry = next(
                        (e for e in teams[cur_team] if e["player"] == cur_player), None
                    )
                    if not entry:
                        teams[cur_team].append(
                            {"player": cur_player, "odds_1": val, "odds_2": None}
                        )
                    elif entry["odds_2"] is None and val > entry["odds_1"]:
                        entry["odds_2"] = val
                i += 1
                continue
            except ValueError:
                pass
            bad_words = [
                "décisif", "fois", "équipe", "passeur",
                "voir plus", "toute", "viennent",
            ]
            if 2 < len(line) < 50 and not any(kw in line.lower() for kw in bad_words):
                if cur_team is None:
                    # First name after market header = team 1 (home)
                    cur_team = line
                    teams[cur_team] = []
                    cur_player = None
                elif (
                    line not in players_seen
                    and len(teams) < 2
                    and len(teams.get(cur_team, [])) >= 1
                ):
                    # Current team has at least one player; unseen name = team 2 (away)
                    cur_team = line
                    teams[cur_team] = []
                    cur_player = None
                else:
                    cur_player = line
                    players_seen.add(line)
            i += 1

        return teams


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
            logger.info("ParionsSport HTTP returned 0 matches for %s — trying Playwright", league)
        except Exception as exc:
            # Expected when the API endpoint is geo-blocked or has changed structure
            logger.info("ParionsSport HTTP unavailable for %s (%s) — trying Playwright", league, exc)

        # Playwright fallback
        if page is not None:
            matches = await self._scrape_playwright(league, page)
            logger.info("ParionsSport %s: %d matches via Playwright", league, len(matches))
            return matches

        logger.info("ParionsSport: HTTP unavailable and no Playwright page for %s — skipping", league)
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
            logger.info("ParionsSport HTTP: no events in response for %s", league)
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
    Unibet uses JSON interception on a shared browser context.
    Betclic uses DOM scraping (gRPC-web prevents JSON interception) and
    creates its own dedicated context per scrape_league call via the shared
    page's browser reference.

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

    leagues: list[str] = [args.league] if args.league else ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "world_cup_2026"]

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
    parser.add_argument("--league", choices=["ligue_1", "premier_league", "champions_league", "bundesliga", "la_liga", "serie_a", "world_cup_2026"], default=None, help="Single league")
    parser.add_argument(
        "--bookmaker", choices=["betclic", "unibet", "parionssport"], default=None, help="Single bookmaker"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print results, do not store to DB")
    parser.add_argument("--headed", action="store_true", help="Show browser window (useful for debugging)")
    asyncio.run(_cli_main(parser.parse_args()))
