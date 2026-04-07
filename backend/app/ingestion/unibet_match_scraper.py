"""Unibet match-level odds scraper — H2H, O/U 2.5, BTTS via LVS API.

Extends the pattern from unibet_lvs_scraper.py (LVS bet offers API).
Does NOT require Playwright — pure HTTP + JSON.

Market type IDs (verify against live LVS API):
    _MT_H2H    = 2    — Match Winner 1X2
    _MT_OU_25  = 18   — Over/Under Goals (check line field for 2.5)
    _MT_BTTS   = 6    — Both Teams to Score

To discover actual IDs: fetch /betoffers.json for a live match and inspect
the marketTypeId field in the response.
"""

import logging
from datetime import datetime, timezone

import httpx

from app.ingestion.market_scrape_chain import ScrapeResult
from app.ingestion.odds_sanity import validate_market

logger = logging.getLogger(__name__)

PARSE_VERSION = "ub-v1"

# Market type IDs — MUST be verified against live Unibet LVS API
_MT_H2H: int = 2
_MT_OU: int = 18       # Over/Under; filter for 2.5 line
_MT_BTTS: int = 6

_BASE_URL = "https://eu-offering-api.kambicdn.com/offering/v2018/ub"
_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "fr-FR",
    "User-Agent": "Mozilla/5.0",
}


async def _fetch_betoffers(event_id: int, client: httpx.AsyncClient) -> list[dict]:
    url = f"{_BASE_URL}/event/{event_id}/betoffers.json"
    params = {"lang": "fr_FR", "market": "FR", "includeParticipants": "true"}
    resp = await client.get(url, params=params, headers=_HEADERS, timeout=10.0)
    resp.raise_for_status()
    return resp.json().get("betOffers", [])


def _extract_market_outcomes(betoffers: list[dict], market_type_id: int) -> list[dict]:
    for bo in betoffers:
        if bo.get("marketTypeId") == market_type_id:
            closed = bo.get("closed", False) or bo.get("suspended", False)
            if not closed:
                return bo.get("outcomes", [])
    return []


def _parse_odds_lvs(outcome: dict) -> float | None:
    """LVS API stores odds as integer × 1000 (e.g., 2050 → 2.050)."""
    for key in ("odds", "oddsAmerican", "oddsFractional"):
        val = outcome.get(key)
        if val is not None and isinstance(val, (int, float)):
            # Detect integer-scaled (> 100 means likely ×1000)
            if isinstance(val, int) and val > 100:
                return val / 1000.0
            return float(val)
    return None


def _parse_h2h(betoffers: list[dict]) -> dict[str, float] | None:
    outcomes = _extract_market_outcomes(betoffers, _MT_H2H)
    if not outcomes:
        return None
    result: dict[str, float] = {}
    for o in outcomes:
        label = str(o.get("label") or o.get("englishLabel") or "").lower()
        odds = _parse_odds_lvs(o)
        if odds is None:
            continue
        if label in ("1", "home", "domicile") or "home" in label:
            result["home"] = odds
        elif label in ("x", "draw", "nul"):
            result["draw"] = odds
        elif label in ("2", "away", "extérieur") or "away" in label:
            result["away"] = odds
    return result if validate_market("h2h", result) else None


def _parse_totals(betoffers: list[dict]) -> dict[str, float] | None:
    outcomes = _extract_market_outcomes(betoffers, _MT_OU)
    if not outcomes:
        return None
    result: dict[str, float] = {}
    for o in outcomes:
        # Only process the 2.5 line
        line = str(o.get("line") or o.get("handicap") or "")
        if "2.5" not in line and "25" not in line:
            continue
        label = str(o.get("label") or o.get("englishLabel") or "").lower()
        odds = _parse_odds_lvs(o)
        if odds is None:
            continue
        if "over" in label or "+" in label or label == "o":
            result["over_2.5"] = odds
        elif "under" in label or "-" in label or label == "u":
            result["under_2.5"] = odds
    return result if validate_market("totals", result) else None


def _parse_btts(betoffers: list[dict]) -> dict[str, float] | None:
    outcomes = _extract_market_outcomes(betoffers, _MT_BTTS)
    if not outcomes:
        return None
    result: dict[str, float] = {}
    for o in outcomes:
        label = str(o.get("label") or o.get("englishLabel") or "").lower()
        odds = _parse_odds_lvs(o)
        if odds is None:
            continue
        if label in ("yes", "oui", "1"):
            result["yes"] = odds
        elif label in ("no", "non", "0"):
            result["no"] = odds
    return result if validate_market("btts", result) else None


async def scrape_match_markets_by_event_id(
    event_id: int,
    url: str,
    client: httpx.AsyncClient,
) -> ScrapeResult:
    """Fetch Unibet LVS bet offers for event_id and extract H2H, O/U 2.5, BTTS."""
    ingested_at = datetime.now(timezone.utc)

    try:
        betoffers = await _fetch_betoffers(event_id, client)
    except httpx.HTTPError as exc:
        logger.warning("unibet_match: HTTP error for event %s: %s", event_id, exc)
        return ScrapeResult(
            source="unibet",
            source_url=url,
            parse_version=PARSE_VERSION,
            h2h=None, totals=None, btts=None,
            ingested_at_utc=ingested_at,
            fallback_used=True,
            error=f"http_error:{type(exc).__name__}",
        )

    return ScrapeResult(
        source="unibet",
        source_url=url,
        parse_version=PARSE_VERSION,
        h2h=_parse_h2h(betoffers),
        totals=_parse_totals(betoffers),
        btts=_parse_btts(betoffers),
        ingested_at_utc=ingested_at,
        fallback_used=True,
    )
