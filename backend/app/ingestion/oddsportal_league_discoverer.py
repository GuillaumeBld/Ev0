"""OddsPortal league listing scraper — auto-discovers upcoming match URLs.

Playwright scrapes the OddsPortal league listing pages (React SPA) to
extract upcoming match metadata (teams, kickoff time, URL).

SELECTORS: Placeholders — must be verified against live OddsPortal DOM.
To inspect: playwright open https://www.oddsportal.com/football/france/ligue-1/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 30_000
_LOAD_TIMEOUT_MS = 10_000
_DISCOVERY_WINDOW_DAYS = 7

# --- League listing URLs ---
ODDSPORTAL_LEAGUE_URLS: dict[str, str] = {
    "ligue_1":          "https://www.oddsportal.com/football/france/ligue-1/",
    "premier_league":   "https://www.oddsportal.com/football/england/premier-league/",
    "bundesliga":       "https://www.oddsportal.com/football/germany/bundesliga/",
    "la_liga":          "https://www.oddsportal.com/football/spain/laliga/",
    "serie_a":          "https://www.oddsportal.com/football/italy/serie-a/",
    "champions_league": "https://www.oddsportal.com/football/europe/champions-league/",
}

# --- Selectors (MUST be verified against live OddsPortal DOM) ---
# OddsPortal is a React SPA — class names may be hashed/unstable.
# Preferred: use data-testid or aria attributes where available.
_SEL_MATCH_ROW = "div[data-testid='match-row']"          # each upcoming match
_SEL_HOME_TEAM = "[data-testid='home-team-name']"         # home team within row
_SEL_AWAY_TEAM = "[data-testid='away-team-name']"         # away team within row
_SEL_MATCH_LINK = "a[href*='/football/']"                 # link to match page
_SEL_KICKOFF_TIME = "p[class*='date']"                   # kickoff datetime text


@dataclass
class OddsPortalMatchItem:
    home_raw: str          # nom brut affiché sur OddsPortal
    away_raw: str
    kickoff_utc: datetime  # converti en UTC
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
        await page.wait_for_load_state("domcontentloaded", timeout=_LOAD_TIMEOUT_MS)
    except PlaywrightTimeout:
        logger.warning("discoverer: navigation timeout for league=%s url=%s", league, url)
        return []
    except Exception as exc:
        logger.warning("discoverer: failed to load league=%s: %s", league, exc)
        return []

    items: list[OddsPortalMatchItem] = []

    try:
        rows = await page.query_selector_all(_SEL_MATCH_ROW)
        logger.info("discoverer: league=%s found %d rows", league, len(rows))

        for row in rows:
            try:
                home_el = await row.query_selector(_SEL_HOME_TEAM)
                away_el = await row.query_selector(_SEL_AWAY_TEAM)
                link_el = await row.query_selector(_SEL_MATCH_LINK)
                time_el = await row.query_selector(_SEL_KICKOFF_TIME)

                if not (home_el and away_el and link_el):
                    continue

                home_raw = (await home_el.text_content() or "").strip()
                away_raw = (await away_el.text_content() or "").strip()
                href = await link_el.get_attribute("href") or ""
                match_url = f"https://www.oddsportal.com{href}" if href.startswith("/") else href

                if not home_raw or not away_raw or not match_url:
                    continue

                kickoff_utc = await _parse_kickoff(time_el)
                if kickoff_utc is None:
                    logger.debug("discoverer: no kickoff time for %s vs %s", home_raw, away_raw)
                    continue

                if kickoff_utc < now or kickoff_utc > cutoff:
                    continue

                items.append(OddsPortalMatchItem(
                    home_raw=home_raw,
                    away_raw=away_raw,
                    kickoff_utc=kickoff_utc,
                    match_url=match_url,
                    league=league,
                ))

            except Exception as exc:
                logger.debug("discoverer: error parsing row: %s", exc)
                continue

    except Exception as exc:
        logger.warning("discoverer: error iterating rows for league=%s: %s", league, exc)

    logger.info("discoverer: league=%s discovered %d items", league, len(items))
    return items


async def _parse_kickoff(el: object | None) -> datetime | None:
    """Extract kickoff UTC from a page element.

    Tries: 1) datetime attribute (ISO), 2) data-kickoff attribute (Unix timestamp).
    Returns None if unparseable.
    """
    if el is None:
        return None
    try:
        dt_attr = await el.get_attribute("datetime")
        if dt_attr:
            dt = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)

        ts_attr = await el.get_attribute("data-kickoff")
        if ts_attr and ts_attr.isdigit():
            return datetime.fromtimestamp(int(ts_attr), tz=timezone.utc)

    except Exception:
        pass
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
