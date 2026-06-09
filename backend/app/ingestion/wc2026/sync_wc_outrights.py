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


# ── Unibet (LVS) ──────────────────────────────────────────────────────────────


def _parse_lvs_price(value: Any) -> float | None:
    """Convertit une cote LVS en float. None si invalide ou suspendue."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("null", "", "none"):
        return None
    try:
        f = float(s.replace(",", "."))
    except ValueError:
        return None
    if f < 1.01 or f > 1000.0:
        return None
    return round(f, 2)


async def scrape_unibet_wc_outrights() -> list[dict]:
    """Scrape les outrights CDM depuis Unibet (LVS).

    Returns list of dicts: {nation, player_name, market_type, bookmaker, odds}.

    Strategy: récupère le token anonyme LVS, liste les événements outright
    du noeud WC2026, récupère les marchés de chaque événement via /ff/.
    """
    try:
        async with httpx.AsyncClient(headers=_LVS_HEADERS, timeout=20.0) as client:
            # 1. Token anonyme
            token_r = await client.get(f"{LVS_BASE}/lvs-api/acc/token")
            token_r.raise_for_status()
            token = token_r.json().get("hsToken", "")
            auth_headers = {**_LVS_HEADERS, "X-LVS-HSToken": token}

            # 2. Liste des événements outright du noeud WC2026
            events_r = await client.get(
                f"{LVS_BASE}/lvs-api/next/200/p{_LVS_WC2026_NODE}",
                params={"lineId": "1", "originId": "3", "ext": "1"},
                headers=auth_headers,
            )
            events_r.raise_for_status()
            items = events_r.json().get("items", {})

            # Identifier les event IDs (outright events ont b="" ou pas d'adversaire)
            outright_event_ids = []
            for key, val in items.items():
                if not key.startswith("e"):
                    continue
                # Outright event: home/away vides ou absents
                if not val.get("b") and val.get("a"):
                    try:
                        outright_event_ids.append(int(key[1:]))
                    except ValueError:
                        continue

            if not outright_event_ids:
                logger.info("Unibet outrights WC2026: aucun événement outright trouvé dans noeud %d", _LVS_WC2026_NODE)
                return []

            # 3. Fetch marchés de chaque événement
            results: list[dict] = []
            for event_id in outright_event_ids:
                try:
                    ff_r = await client.get(
                        f"{LVS_BASE}/lvs-api/ff/e{event_id}",
                        params={"lineId": "1", "originId": "3", "ext": "1"},
                        headers=auth_headers,
                    )
                    ff_r.raise_for_status()
                    ff_items = ff_r.json().get("items", {})
                except Exception as exc:
                    logger.warning("Unibet outrights: erreur event %d: %s", event_id, exc)
                    continue

                # Indexer les marchés par id
                markets: dict[str, dict] = {}
                outcomes: list[dict] = []
                for k, v in ff_items.items():
                    if k.startswith("m"):
                        markets[k] = v
                    elif k.startswith("o"):
                        outcomes.append({**v, "_key": k})

                for mkey, market in markets.items():
                    mtype_id = market.get("markettypeId")
                    market_type = _LVS_OUTRIGHT_MARKET_TYPES.get(mtype_id)
                    if not market_type:
                        continue
                    is_player_market = market_type in ("top_scorer", "top_assister")
                    for o in outcomes:
                        if o.get("marketId") != mkey and o.get("m") != mkey:
                            continue
                        name = o.get("a") or o.get("n", "")
                        odds = _parse_lvs_price(o.get("pr") or o.get("p"))
                        if not name or odds is None:
                            continue
                        results.append({
                            "nation": None if is_player_market else name,
                            "player_name": name if is_player_market else None,
                            "market_type": market_type,
                            "bookmaker": "unibet",
                            "odds": odds,
                        })

    except Exception as exc:
        logger.error("Unibet outrights WC2026: erreur globale: %s", exc)
        return []

    logger.info("Unibet outrights WC2026: %d cotes scrappées", len(results))
    return results


# ── Betclic ───────────────────────────────────────────────────────────────────

# Betclic outright competition ID pour WC 2026 spéciaux
# (distinct de competition_id=1 qui est pour les matchs)
_BETCLIC_OUTRIGHT_URL = (
    "https://www.betclic.fr/api/v2/outrights"
    "?competition_id=1&lang=fr&market=FR"
)

_BETCLIC_MARKET_TYPE_MAP: dict[str, str] = {
    "gagnant": "winner",
    "vainqueur": "winner",
    "winner": "winner",
    "finaliste": "top2",
    "demi-finaliste": "top4",
    "semi": "top4",
    "quart": "top8",
    "top 8": "top8",
    "phase de groupes": "group_stage",
    "buteur": "top_scorer",
    "goalscorer": "top_scorer",
    "passeur": "top_assister",
    "assister": "top_assister",
}

_BETCLIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.betclic.fr/",
    "x-bg-regulation": "FR",
    "x-bg-ref-brand": "BETCLIC",
}


def _classify_betclic_outright(name: str) -> str | None:
    """Classifie un marché outright Betclic en market_type Ev0."""
    lower = name.lower()
    for keyword, market_type in _BETCLIC_MARKET_TYPE_MAP.items():
        if keyword in lower:
            return market_type
    return None


async def scrape_betclic_wc_outrights() -> list[dict]:
    """Scrape les outrights CDM depuis Betclic via REST API.

    Returns list of dicts: {nation, player_name, market_type, bookmaker, odds}.
    """
    try:
        async with httpx.AsyncClient(headers=_BETCLIC_HEADERS, timeout=20.0) as client:
            r = await client.get(_BETCLIC_OUTRIGHT_URL)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.error("Betclic outrights WC2026: erreur fetch %s: %s", _BETCLIC_OUTRIGHT_URL, exc)
        return []

    results: list[dict] = []

    # Betclic outright response: liste de marchés avec selections
    # Structure attendue: [{"name": "Vainqueur CDM", "selections": [{"name": "France", "odds": 4.5}, ...]}, ...]
    for market in data if isinstance(data, list) else data.get("markets", []):
        market_name = market.get("name", "")
        market_type = _classify_betclic_outright(market_name)
        if not market_type:
            continue
        is_player_market = market_type in ("top_scorer", "top_assister")
        for sel in market.get("selections", []) or market.get("outcomes", []):
            name = sel.get("name", "")
            raw_odds = sel.get("odds") or sel.get("price")
            if not name or not raw_odds:
                continue
            try:
                odds = float(raw_odds)
            except (ValueError, TypeError):
                continue
            if odds < 1.01 or odds > 1000.0:
                continue
            results.append({
                "nation": None if is_player_market else name,
                "player_name": name if is_player_market else None,
                "market_type": market_type,
                "bookmaker": "betclic",
                "odds": round(odds, 2),
            })

    logger.info("Betclic outrights WC2026: %d cotes scrappées", len(results))
    return results


# ── Storage ───────────────────────────────────────────────────────────────────


async def store_wc_outrights(session: AsyncSession, outrights: list[dict]) -> None:
    """Upsert les outrights dans wc2026_outright_odds.

    Stratégie : INSERT ... ON CONFLICT ... DO UPDATE SET odds = EXCLUDED.odds, scraped_at = now().
    Utilise raw SQL pour l'upsert PostgreSQL sans charger les objets en mémoire.
    """
    if not outrights:
        return

    await session.execute(
        text("""
            INSERT INTO wc2026_outright_odds (nation, player_name, market_type, bookmaker, odds, scraped_at)
            VALUES (:nation, :player_name, :market_type, :bookmaker, :odds, now())
            ON CONFLICT (nation, player_name, market_type, bookmaker)
            DO UPDATE SET odds = EXCLUDED.odds, scraped_at = now()
        """),
        outrights,
    )
    await session.commit()
    logger.info("store_wc_outrights: %d lignes upsertées", len(outrights))


async def sync_all_wc_outrights(session: AsyncSession) -> int:
    """Lance les 3 scrapers en parallèle et stocke les résultats.

    Returns: nombre total de cotes upsertées.
    """
    import asyncio

    pmu_results, unibet_results, betclic_results = await asyncio.gather(
        scrape_pmu_wc_outrights(),
        scrape_unibet_wc_outrights(),
        scrape_betclic_wc_outrights(),
    )

    all_results = pmu_results + unibet_results + betclic_results
    if all_results:
        await store_wc_outrights(session, all_results)

    logger.info(
        "sync_all_wc_outrights: pmu=%d unibet=%d betclic=%d total=%d",
        len(pmu_results), len(unibet_results), len(betclic_results), len(all_results),
    )
    return len(all_results)
