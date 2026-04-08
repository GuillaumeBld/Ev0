"""Betclic match-level odds scraper — H2H, O/U 2.5, BTTS via SSR JSON.

Betclic serves match data in a Next.js __NEXT_DATA__ JSON blob embedded in HTML.
The existing betclic_scraper.py uses a similar approach for player props.

MARKET KEYS: Must be verified against live Betclic HTML during integration.
The keys below are based on observed Betclic market naming conventions.

Does NOT require Playwright — pure HTTP + JSON parsing.
"""

import json
import logging
import re
from datetime import datetime, timezone

import httpx

from app.ingestion.market_scrape_chain import ScrapeResult
from app.ingestion.odds_sanity import validate_market

logger = logging.getLogger(__name__)

PARSE_VERSION = "bc-v1"

# Try both Next.js __NEXT_DATA__ and Angular ng-state patterns
_NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
_NG_STATE_RE = re.compile(r'<script id="ng-state"[^>]*>(.*?)</script>', re.DOTALL)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Market label patterns to search for (case-insensitive)
# These must be verified against live Betclic market data
_H2H_LABELS = {"1x2", "résultat du match", "match winner", "1 x 2"}
_OU_25_LABELS = {"plus/moins 2.5", "over/under 2.5", "total buts 2.5", "+/- 2.5"}
_BTTS_LABELS = {"les deux équipes marquent", "both teams to score", "btts"}


def _extract_json_payload(html: str) -> dict | None:
    """Try to extract JSON data from SSR payload (Next.js or Angular)."""
    for pattern in (_NEXT_DATA_RE, _NG_STATE_RE):
        m = pattern.search(html)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


def _find_markets_recursive(obj: object, depth: int = 0) -> list[dict]:
    """Recursively search for dicts that look like betting markets."""
    if depth > 10:
        return []
    markets = []
    if isinstance(obj, dict):
        # A market typically has 'selections' or 'outcomes' and a label/name
        if ("selections" in obj or "outcomes" in obj) and (
            "label" in obj or "name" in obj or "title" in obj
        ):
            markets.append(obj)
        for v in obj.values():
            markets.extend(_find_markets_recursive(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            markets.extend(_find_markets_recursive(item, depth + 1))
    return markets


def _get_market_label(market: dict) -> str:
    return str(
        market.get("label") or market.get("name") or market.get("title") or ""
    ).lower().strip()


def _get_selections(market: dict) -> list[dict]:
    return market.get("selections") or market.get("outcomes") or []


def _odds_from_selection(sel: dict) -> float | None:
    """Extract decimal odds from a selection dict."""
    # Try common field names
    for key in ("odds", "price", "odd", "cote", "decimal"):
        val = sel.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return None


def _parse_h2h(markets: list[dict]) -> dict[str, float] | None:
    """Find and parse 1X2 market."""
    for market in markets:
        label = _get_market_label(market)
        if not any(lbl in label for lbl in _H2H_LABELS):
            continue
        selections = _get_selections(market)
        if len(selections) != 3:
            continue
        result: dict[str, float] = {}
        for sel in selections:
            sel_label = str(sel.get("label") or sel.get("name") or "").lower()
            odds = _odds_from_selection(sel)
            if odds is None:
                break
            if "1" in sel_label or sel_label in ("home", "domicile", "équipe 1"):
                result["home"] = odds
            elif sel_label in ("x", "nul", "draw", "n"):
                result["draw"] = odds
            elif "2" in sel_label or sel_label in ("away", "extérieur", "équipe 2"):
                result["away"] = odds
        if validate_market("h2h", result):
            return result
    return None


def _parse_totals(markets: list[dict]) -> dict[str, float] | None:
    """Find and parse Over/Under 2.5 market."""
    for market in markets:
        label = _get_market_label(market)
        if not any(lbl in label for lbl in _OU_25_LABELS):
            continue
        selections = _get_selections(market)
        result: dict[str, float] = {}
        for sel in selections:
            sel_label = str(sel.get("label") or sel.get("name") or "").lower()
            odds = _odds_from_selection(sel)
            if odds is None:
                continue
            if "over" in sel_label or "plus" in sel_label or "+" in sel_label:
                result["over_2.5"] = odds
            elif "under" in sel_label or "moins" in sel_label or "-" in sel_label:
                result["under_2.5"] = odds
        if validate_market("totals", result):
            return result
    return None


def _parse_btts(markets: list[dict]) -> dict[str, float] | None:
    """Find and parse BTTS market."""
    for market in markets:
        label = _get_market_label(market)
        if not any(lbl in label for lbl in _BTTS_LABELS):
            continue
        selections = _get_selections(market)
        result: dict[str, float] = {}
        for sel in selections:
            sel_label = str(sel.get("label") or sel.get("name") or "").lower()
            odds = _odds_from_selection(sel)
            if odds is None:
                continue
            if sel_label in ("oui", "yes", "1"):
                result["yes"] = odds
            elif sel_label in ("non", "no", "0"):
                result["no"] = odds
        if validate_market("btts", result):
            return result
    return None


async def scrape_match_markets(url: str, client: httpx.AsyncClient) -> ScrapeResult:
    """Fetch Betclic match page and extract H2H, O/U 2.5, BTTS from SSR JSON."""
    ingested_at = datetime.now(timezone.utc)

    try:
        resp = await client.get(url, headers=_HEADERS, follow_redirects=True, timeout=15.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("betclic_match: HTTP error for %s: %s", url, exc)
        return ScrapeResult(
            source="betclic",
            source_url=url,
            parse_version=PARSE_VERSION,
            h2h=None, totals=None, btts=None,
            ingested_at_utc=ingested_at,
            fallback_used=True,
            error=f"http_error:{type(exc).__name__}",
        )

    payload = _extract_json_payload(resp.text)
    if payload is None:
        logger.warning("betclic_match: no SSR payload found at %s", url)
        return ScrapeResult(
            source="betclic",
            source_url=url,
            parse_version=PARSE_VERSION,
            h2h=None, totals=None, btts=None,
            ingested_at_utc=ingested_at,
            fallback_used=True,
            error="no_ssr_payload",
        )

    markets = _find_markets_recursive(payload)
    return ScrapeResult(
        source="betclic",
        source_url=url,
        parse_version=PARSE_VERSION,
        h2h=_parse_h2h(markets),
        totals=_parse_totals(markets),
        btts=_parse_btts(markets),
        ingested_at_utc=ingested_at,
        fallback_used=True,
    )
