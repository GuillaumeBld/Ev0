"""OddsPortal league listing scraper — auto-discovers upcoming match URLs.

Playwright scrapes the OddsPortal league listing pages (React SPA) to
extract upcoming match metadata (teams, kickoff time, URL).

DOM structure (verified 2026-04-08):
  div[data-testid='date-header']  — date section header ("10 Apr 2026")
  div[data-testid='game-row']     — one match row (appears twice per match:
                                    once with odds/H2H link, once without)

Only rows with a /football/h2h/ link are parsed (deduplicates the double rows).
Kickoff times are in Europe/Paris (CET/CEST) and converted to UTC.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 30_000
_DISCOVERY_WINDOW_DAYS = 7
_PARIS_TZ = ZoneInfo("Europe/Paris")

# --- League listing URLs ---
ODDSPORTAL_LEAGUE_URLS: dict[str, str] = {
    "ligue_1":          "https://www.oddsportal.com/football/france/ligue-1/",
    "premier_league":   "https://www.oddsportal.com/football/england/premier-league/",
    "bundesliga":       "https://www.oddsportal.com/football/germany/bundesliga/",
    "la_liga":          "https://www.oddsportal.com/football/spain/laliga/",
    "serie_a":          "https://www.oddsportal.com/football/italy/serie-a/",
    "champions_league": "https://www.oddsportal.com/football/europe/champions-league/",
}


@dataclass
class OddsPortalMatchItem:
    home_raw: str          # nom brut affiché sur OddsPortal (title attribute)
    away_raw: str
    kickoff_utc: datetime  # converti en UTC depuis Europe/Paris
    match_url: str         # URL complète du match
    league: str            # clé interne (ex. "ligue_1")


async def discover_league(
    league: str,
    page: Page,
) -> list[OddsPortalMatchItem]:
    """Scrape the OddsPortal listing page for one league.

    Returns upcoming matches within the next DISCOVERY_WINDOW_DAYS days.
    Returns [] on any error — caller continues with other leagues.
    """
    url = ODDSPORTAL_LEAGUE_URLS[league]
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=_DISCOVERY_WINDOW_DAYS)

    try:
        await page.goto(url, wait_until="networkidle", timeout=_NAV_TIMEOUT_MS)
        await page.wait_for_timeout(2000)  # let React finish rendering
    except PlaywrightTimeout:
        logger.warning("discoverer: navigation timeout for league=%s url=%s", league, url)
        return []
    except Exception as exc:
        logger.warning("discoverer: failed to load league=%s: %s", league, exc)
        return []

    # Extract all date-headers and game-rows in DOM order via JS
    try:
        raw_elements: list[dict] = await page.evaluate("""
            () => {
                const els = document.querySelectorAll(
                    "div[data-testid='date-header'], div[data-testid='game-row']"
                );
                return Array.from(els).map(el => {
                    const testid = el.dataset.testid;
                    if (testid === 'date-header') {
                        return { type: 'date', text: el.textContent.trim() };
                    }
                    // game-row: only keep rows with a H2H match link
                    const link = el.querySelector("a[href*='/football/h2h/']");
                    if (!link) return null;
                    const teamLinks = Array.from(
                        el.querySelectorAll("div[data-testid='event-participants'] a")
                    ).map(a => a.title || '').filter(t => t);
                    const timeEl = el.querySelector("div[data-testid='time-item'] p");
                    return {
                        type: 'match',
                        link: link.getAttribute('href'),
                        teams: teamLinks,
                        time: timeEl ? timeEl.textContent.trim() : null,
                    };
                }).filter(x => x !== null);
            }
        """)
    except Exception as exc:
        logger.warning("discoverer: JS extraction failed for league=%s: %s", league, exc)
        return []

    items: list[OddsPortalMatchItem] = []
    current_date_str: str | None = None

    for el in raw_elements:
        if el["type"] == "date":
            current_date_str = el["text"]
            continue

        if current_date_str is None:
            continue

        teams: list[str] = el.get("teams") or []
        if len(teams) < 2:
            continue

        home_raw = teams[0]
        away_raw = teams[-1]
        time_str: str | None = el.get("time")
        href: str | None = el.get("link")

        if not (home_raw and away_raw and time_str and href):
            continue

        kickoff_utc = _parse_kickoff(current_date_str, time_str)
        if kickoff_utc is None:
            logger.debug("discoverer: unparseable kickoff %s %s (%s vs %s)", current_date_str, time_str, home_raw, away_raw)
            continue

        if kickoff_utc < now or kickoff_utc > cutoff:
            continue

        match_url = f"https://www.oddsportal.com{href}" if href.startswith("/") else href

        items.append(OddsPortalMatchItem(
            home_raw=home_raw,
            away_raw=away_raw,
            kickoff_utc=kickoff_utc,
            match_url=match_url,
            league=league,
        ))

    logger.info("discoverer: league=%s found %d rows discovered %d items", league, len(raw_elements), len(items))
    return items


def _parse_kickoff(date_str: str, time_str: str) -> datetime | None:
    """Parse OddsPortal date + time strings into UTC.

    date_str: "10 Apr 2026"
    time_str: "19:00"
    Times are displayed in Europe/Paris (CET/CEST).
    """
    try:
        dt_local = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%d %b %Y %H:%M")
        dt_paris = dt_local.replace(tzinfo=_PARIS_TZ)
        return dt_paris.astimezone(timezone.utc)
    except ValueError:
        return None


async def discover_all_leagues(browser: Browser) -> list[OddsPortalMatchItem]:
    """Scrape all configured leagues. Each league gets its own page.

    Leagues that fail are skipped — the rest are returned.
    """
    all_items: list[OddsPortalMatchItem] = []

    for league in ODDSPORTAL_LEAGUE_URLS:
        page = await browser.new_page()
        try:
            items = await discover_league(league, page)
            all_items.extend(items)
        finally:
            await page.close()

    logger.info("discoverer: total=%d items across all leagues", len(all_items))
    return all_items
