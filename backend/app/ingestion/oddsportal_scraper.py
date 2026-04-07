"""OddsPortal Playwright scraper — extracts H2H, O/U 2.5, BTTS match odds.

OddsPortal is a React SPA. Requires Playwright to execute JavaScript.

SELECTORS: These must be verified against the live OddsPortal DOM during
integration testing. The selectors below are scaffolded and will need
updating based on live site inspection.

To inspect: playwright open https://www.oddsportal.com/football/france/ligue-1/<slug>/
"""

import logging
from datetime import datetime, timezone

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from app.ingestion.market_scrape_chain import ScrapeResult
from app.ingestion.odds_sanity import validate_market

logger = logging.getLogger(__name__)

PARSE_VERSION = "op-v1"
_NAV_TIMEOUT_MS = 25_000
_ELEMENT_TIMEOUT_MS = 8_000

# --- Selectors (MUST be verified against live OddsPortal DOM) ---
# These are placeholder selectors — update after live site inspection.
# OddsPortal uses React and class names may be hashed/unstable.
# Recommended approach: use data-* attributes or aria labels where available.
_SEL_H2H_HOME = "p[class*='height-content'] span[class*='oddsCell']:nth-child(1)"
_SEL_H2H_DRAW = "p[class*='height-content'] span[class*='oddsCell']:nth-child(2)"
_SEL_H2H_AWAY = "p[class*='height-content'] span[class*='oddsCell']:nth-child(3)"
_SEL_TAB_OU = "a[href*='over-under']"
_SEL_OU_OVER_25 = "div[data-testid='ou-2.5-over']"
_SEL_OU_UNDER_25 = "div[data-testid='ou-2.5-under']"
_SEL_TAB_BTTS = "a[href*='both-teams-to-score']"
_SEL_BTTS_YES = "div[data-testid='btts-yes']"
_SEL_BTTS_NO = "div[data-testid='btts-no']"


async def _parse_float_from_selector(page: Page, selector: str) -> float | None:
    """Extract numeric text from a page element. Returns None on any failure."""
    try:
        el = await page.wait_for_selector(selector, timeout=_ELEMENT_TIMEOUT_MS)
        if el is None:
            return None
        text = (await el.text_content() or "").strip().replace(",", ".")
        return float(text)
    except (PlaywrightTimeout, ValueError, TypeError):
        return None


async def scrape_match_markets(url: str, page: Page) -> ScrapeResult:
    """
    Navigate to OddsPortal match page and extract H2H, O/U 2.5, BTTS.

    Returns ScrapeResult. is_complete=True only if all 3 markets extracted
    and pass sanity checks.

    NOTE: Selectors are scaffolded. Integration test against live site required
    before this scraper is production-ready.
    """
    ingested_at = datetime.now(timezone.utc)

    try:
        await page.goto(url, wait_until="networkidle", timeout=_NAV_TIMEOUT_MS)
    except PlaywrightTimeout:
        logger.warning("oddsportal: navigation timeout for %s", url)
        return ScrapeResult(
            source="oddsportal",
            source_url=url,
            parse_version=PARSE_VERSION,
            h2h=None,
            totals=None,
            btts=None,
            ingested_at_utc=ingested_at,
            error="navigation_timeout",
        )

    # --- H2H ---
    home = await _parse_float_from_selector(page, _SEL_H2H_HOME)
    draw = await _parse_float_from_selector(page, _SEL_H2H_DRAW)
    away = await _parse_float_from_selector(page, _SEL_H2H_AWAY)
    h2h_raw: dict[str, float | None] = {"home": home, "draw": draw, "away": away}
    h2h = (
        {k: v for k, v in h2h_raw.items() if v is not None}  # type: ignore[misc]
        if (home and draw and away and validate_market("h2h", h2h_raw))
        else None
    )

    # --- O/U 2.5 ---
    totals = None
    try:
        tab = await page.wait_for_selector(_SEL_TAB_OU, timeout=_ELEMENT_TIMEOUT_MS)
        if tab:
            await tab.click()
            await page.wait_for_load_state("networkidle", timeout=5_000)
        over = await _parse_float_from_selector(page, _SEL_OU_OVER_25)
        under = await _parse_float_from_selector(page, _SEL_OU_UNDER_25)
        totals_raw: dict[str, float | None] = {"over_2.5": over, "under_2.5": under}
        if over and under and validate_market("totals", totals_raw):
            totals = {"over_2.5": over, "under_2.5": under}
    except (PlaywrightTimeout, Exception) as exc:
        logger.debug("oddsportal: O/U tab failed for %s: %s", url, exc)

    # --- BTTS ---
    btts = None
    try:
        tab = await page.wait_for_selector(_SEL_TAB_BTTS, timeout=_ELEMENT_TIMEOUT_MS)
        if tab:
            await tab.click()
            await page.wait_for_load_state("networkidle", timeout=5_000)
        yes = await _parse_float_from_selector(page, _SEL_BTTS_YES)
        no = await _parse_float_from_selector(page, _SEL_BTTS_NO)
        btts_raw: dict[str, float | None] = {"yes": yes, "no": no}
        if yes and no and validate_market("btts", btts_raw):
            btts = {"yes": yes, "no": no}
    except (PlaywrightTimeout, Exception) as exc:
        logger.debug("oddsportal: BTTS tab failed for %s: %s", url, exc)

    return ScrapeResult(
        source="oddsportal",
        source_url=url,
        parse_version=PARSE_VERSION,
        h2h=h2h,
        totals=totals,
        btts=btts,
        ingested_at_utc=ingested_at,
    )
