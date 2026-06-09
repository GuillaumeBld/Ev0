# backend/app/ingestion/wc2026/sync_wc_outrights.py
"""Scrape WC2026 outright odds from PMU (Kambi), Unibet (LVS), and Betclic.

Outrights = marchés de tournoi : vainqueur CDM, top4, top8, buteur, passeur.
Stockés dans wc2026_outright_odds avec upsert sur (nation, player_name, market_type, bookmaker).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

KAMBI_BASE = "https://eu1.offering-api.kambicdn.com/offering/v2018/pmusportsfr"
LVS_BASE = "https://www.unibet.fr"

# LVS node id WC2026 (node match — le même noeud expose outrights via markettypeId spécifique)
_LVS_WC2026_NODE = 59096156

# LVS markettypeId pour les marchés de tournoi
_LVS_OUTRIGHT_MARKET_TYPES: dict[int, str] = {
    14:        "winner",       # Gagnant du tournoi
    62:        "top2",         # Finaliste (atteindre la finale)
    63:        "top4",         # Demi-finaliste
    64:        "top8",         # Quart-de-finaliste
    65:        "group_stage",  # Passer la phase de groupes
    8:         "top_scorer",   # Meilleur buteur
    100001899: "top_assister", # Meilleur passeur
}

_KAMBI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Origin": "https://www.pmu.fr",
    "Referer": "https://www.pmu.fr/",
}

_LVS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.unibet.fr/",
}

# ── Helpers Kambi ─────────────────────────────────────────────────────────────


def _kambi_odds(raw: int | None) -> float | None:
    """Convertit les cotes Kambi (entier×1000) en décimal. None si invalide."""
    if not raw or raw <= 1000:
        return None
    return round(raw / 1000, 2)


def _classify_kambi_outright(bet_offer_type: str, criterion: str) -> str | None:
    """Retourne le market_type Ev0 depuis les labels Kambi. None si non reconnu."""
    combined = f"{bet_offer_type} {criterion}".lower()
    if "top scorer" in combined or "top goalscorer" in combined or "goalscorer" in combined:
        return "top_scorer"
    if "assister" in combined or "assist" in combined or "most assists" in combined:
        return "top_assister"
    if "semi final" in combined or "top 4" in combined:
        return "top4"
    if "quarter final" in combined or "top 8" in combined:
        return "top8"
    if "final" in combined and "semi" not in combined and "quarter" not in combined:
        return "top2"
    if "top 2" in combined:
        return "top2"
    if "winner" in combined and "top" not in combined:
        return "winner"
    if "group stage" in combined or "to qualify" in combined:
        return "group_stage"
    return None


def _is_wc2026_event(event: dict[str, Any]) -> bool:
    """Retourne True si l'événement est dans le path World Cup 2026."""
    for part in event.get("path", []):
        eng = part.get("englishName", "").lower()
        if "world cup 2026" in eng or "coupe du monde 2026" in eng:
            return True
    return False


# ── PMU (Kambi) ───────────────────────────────────────────────────────────────


async def scrape_pmu_wc_outrights() -> list[dict]:
    """Scrape les outrights CDM depuis PMU (Kambi).

    Returns list of dicts: {nation, player_name, market_type, bookmaker, odds}.
    """
    url = f"{KAMBI_BASE}/listView/football/outright.json"
    params = {"lang": "fr_FR", "market": "FR", "useCombined": "true", "limit": "500"}

    try:
        async with httpx.AsyncClient(headers=_KAMBI_HEADERS, timeout=20.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.error("PMU outrights: erreur fetch: %s", exc)
        return []

    results: list[dict] = []
    for entry in data.get("events", []):
        ev = entry.get("event", {})
        if not _is_wc2026_event(ev):
            continue
        for bo in ev.get("betOffers", []):
            bet_type = bo.get("betOfferType", {}).get("englishName", "")
            criterion = bo.get("criterion", {}).get("englishLabel", "")
            market_type = _classify_kambi_outright(bet_type, criterion)
            if not market_type:
                continue
            for outcome in bo.get("outcomes", []):
                odds = _kambi_odds(outcome.get("odds"))
                if odds is None:
                    continue
                label = outcome.get("englishLabel") or outcome.get("label") or ""
                participant = outcome.get("participant") or label
                if not participant:
                    continue
                is_player_market = market_type in ("top_scorer", "top_assister")
                results.append({
                    "nation": None if is_player_market else participant,
                    "player_name": participant if is_player_market else None,
                    "market_type": market_type,
                    "bookmaker": "pmu",
                    "odds": odds,
                })

    logger.info("PMU outrights WC2026: %d cotes scrappées", len(results))
    return results
