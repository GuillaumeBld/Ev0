# backend/app/ingestion/wc2026/sync_wc_outrights.py
"""Scrape WC2026 outright odds from Unibet (LVS) and Betclic (Playwright).

Outrights = marchés de tournoi : vainqueur CDM, top4, top8, buteur, passeur.
Stockés dans wc2026_outright_odds avec upsert sur (nation, player_name, market_type, bookmaker).

NOTE : ce scraper doit tourner en LOCAL (IP résidentielle FR).
       Ne pas appeler depuis le worker VPS.

PMU : désactivé — rebranding en cours, pas d'outrights WC2026 via Kambi.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

LVS_BASE = "https://www.unibet.fr"
BETCLIC_BASE = "https://www.betclic.fr"

# ── Unibet (LVS) ──────────────────────────────────────────────────────────────

# markettypeId → market_type interne
_LVS_OUTRIGHT_MARKET_TYPES: dict[int, str] = {
    14:        "winner",
    62:        "top2",
    63:        "top4",
    64:        "top8",
    65:        "group_stage",
    8:         "top_scorer",
    100001899: "top_assister",
}

_LVS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.unibet.fr/",
}

# Nœuds WC2026 connus (peut évoluer en cours de tournoi — discovery en fallback)
_LVS_WC2026_CANDIDATE_NODES = [59096156, 58529550]


async def _lvs_find_wc2026_node(client: httpx.AsyncClient, auth_headers: dict) -> int | None:
    """Discover WC2026 outright node dynamically via LVS tree traversal."""
    # Start from the football root candidates
    for root in ["p1000093190", "p4", "p1"]:
        try:
            r = await client.get(
                f"{LVS_BASE}/lvs-api/next/500/{root}",
                params={"lang": "fr", "lineId": "1", "originId": "3"},
                headers=auth_headers,
                timeout=15.0,
            )
            if r.status_code != 200:
                continue
            items = r.json().get("items", {})
            for key, val in items.items():
                desc = str(val.get("desc", "") or val.get("n", "") or "").lower()
                if "world cup 2026" in desc or "coupe du monde 2026" in desc:
                    node_id = int(key.lstrip("p"))
                    logger.info("Unibet: discovered WC2026 node %d via %s", node_id, root)
                    return node_id
        except Exception:
            continue
    return None


async def scrape_unibet_wc_outrights() -> list[dict]:
    """Scrape les outrights CDM depuis Unibet (LVS).

    Returns list of dicts: {nation, player_name, market_type, bookmaker, odds}.
    """
    async with httpx.AsyncClient(headers=_LVS_HEADERS, timeout=20.0) as client:
        try:
            token_r = await client.get(f"{LVS_BASE}/lvs-api/acc/token")
            token_r.raise_for_status()
            token = token_r.json().get("hsToken", "")
        except Exception as exc:
            logger.error("Unibet outrights: impossible d'obtenir le token: %s", exc)
            return []

        auth_headers = {**_LVS_HEADERS, "X-LVS-HSToken": token}

        # Try known candidate nodes first, fallback to dynamic discovery
        wc2026_node: int | None = None
        for node_id in _LVS_WC2026_CANDIDATE_NODES:
            try:
                r = await client.get(
                    f"{LVS_BASE}/lvs-api/next/200/p{node_id}",
                    params={"lang": "fr", "lineId": "1", "originId": "3"},
                    headers=auth_headers,
                    timeout=15.0,
                )
                if r.status_code == 200 and r.json().get("items"):
                    wc2026_node = node_id
                    break
            except Exception:
                continue

        if wc2026_node is None:
            wc2026_node = await _lvs_find_wc2026_node(client, auth_headers)

        if wc2026_node is None:
            logger.warning("Unibet outrights WC2026: nœud introuvable")
            return []

        try:
            events_r = await client.get(
                f"{LVS_BASE}/lvs-api/next/200/p{wc2026_node}",
                params={"lang": "fr", "lineId": "1", "originId": "3"},
                headers=auth_headers,
                timeout=15.0,
            )
            events_r.raise_for_status()
            items = events_r.json().get("items", {})
        except Exception as exc:
            logger.error("Unibet outrights: erreur fetch nœud %d: %s", wc2026_node, exc)
            return []

        outright_event_ids: list[int] = []
        for key, val in items.items():
            if not key.startswith("e"):
                continue
            # Outright events have no away team
            if not val.get("b") and val.get("a"):
                try:
                    outright_event_ids.append(int(key[1:]))
                except ValueError:
                    continue

        if not outright_event_ids:
            logger.info("Unibet outrights WC2026: aucun événement outright dans nœud %d", wc2026_node)
            return []

        results: list[dict] = []
        for event_id in outright_event_ids:
            try:
                ff_r = await client.get(
                    f"{LVS_BASE}/lvs-api/ff/e{event_id}",
                    params={"lang": "fr", "lineId": "1", "originId": "3", "ext": "1"},
                    headers=auth_headers,
                    timeout=15.0,
                )
                ff_r.raise_for_status()
                ff_items = ff_r.json().get("items", {})
            except Exception as exc:
                logger.warning("Unibet outrights: erreur event %d: %s", event_id, exc)
                continue

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

    logger.info("Unibet outrights WC2026: %d cotes scrappées", len(results))
    return results


def _parse_lvs_price(value: Any) -> float | None:
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


# ── Betclic (Playwright) ───────────────────────────────────────────────────────

_BETCLIC_COMPETITION_URL = f"https://www.betclic.fr/football-sfootball/coupe-du-monde-2026-c1"

# Textes des onglets "Spéciaux" / outrights sur Betclic (plusieurs orthographes possibles)
_BETCLIC_SPECIALS_LABELS = [
    "Spéciaux", "Speciaux", "Paris spéciaux", "Outrights", "Spécial", "Spéciales",
]

# market_type interne → mots-clés dans le titre du marché Betclic
_BETCLIC_MARKET_KEYWORDS: dict[str, list[str]] = {
    "top_scorer":   ["buteur", "scorer", "meilleur buteur"],
    "top_assister": ["passeur", "assister", "meilleur passeur"],
    "winner":       ["vainqueur", "gagnant", "winner"],
    "top4":         ["top 4", "top4", "demi-final"],
    "top8":         ["top 8", "top8", "quart"],
}


def _betclic_detect_market_type(title: str) -> str | None:
    t = title.lower()
    for mtype, keywords in _BETCLIC_MARKET_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return mtype
    return None


async def scrape_betclic_wc_outrights() -> list[dict]:
    """Scrape les outrights CDM depuis Betclic via Playwright (SPA Cloudflare).

    Stratégie :
      1. Navigue sur la page compétition WC2026.
      2. Cherche un onglet "Spéciaux" / outrights et clique dessus.
      3. Extrait les selections des marchés trouvés.
      4. Si aucun onglet trouvé, retourne [] avec warning.

    Nécessite : pip install playwright && playwright install chromium
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Betclic outrights: playwright non installé — skipped")
        return []

    results: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        try:
            await page.goto(_BETCLIC_COMPETITION_URL, wait_until="load", timeout=30_000)
            # Laisser les requêtes gRPC-Web se terminer
            await page.wait_for_timeout(3_000)

            final_url = page.url
            if "betclic.fr" not in final_url or final_url.rstrip("/") == "https://www.betclic.fr":
                logger.warning("Betclic: redirect vers homepage — page non accessible sans login ?")
                return []

            # Cherche l'onglet "Spéciaux" ou équivalent
            specials_tab = None
            for label in _BETCLIC_SPECIALS_LABELS:
                try:
                    el = page.get_by_role("tab", name=label, exact=False)
                    if await el.count() > 0:
                        specials_tab = el.first
                        break
                    # fallback: cherche dans les liens de navigation
                    el2 = page.get_by_text(label, exact=False)
                    if await el2.count() > 0:
                        specials_tab = el2.first
                        break
                except Exception:
                    continue

            if specials_tab is None:
                logger.info("Betclic WC2026: pas d'onglet Spéciaux/Outrights visible")
                # Tente quand même d'extraire les groupes de marchés présents
            else:
                await specials_tab.click()
                await page.wait_for_timeout(3_000)

            # Parcours les groupes de marchés (accordéons / sections)
            market_groups = await page.query_selector_all(
                "[class*='marketGroup'], [class*='accordionItem'], [class*='market_']"
            )
            logger.info("Betclic WC2026: %d groupes de marchés trouvés", len(market_groups))

            for group in market_groups:
                try:
                    title_el = await group.query_selector(
                        "[class*='groupTitle'], [class*='marketTitle'], [class*='title']"
                    )
                    title = (await title_el.inner_text()).strip() if title_el else ""
                    market_type = _betclic_detect_market_type(title)
                    if not market_type:
                        continue

                    is_player = market_type in ("top_scorer", "top_assister")
                    runners = await group.query_selector_all(
                        "[class*='marketRunner'], [class*='runner'], [class*='outcome']"
                    )
                    for runner in runners:
                        name_el = await runner.query_selector(
                            "[class*='runnerName'], [class*='competitor'], [class*='playerName'], "
                            "[class*='name']"
                        )
                        odds_el = await runner.query_selector(
                            "button[data-odds], [class*='odds'], [class*='price'], [class*='cote']"
                        )
                        if not name_el or not odds_el:
                            continue
                        name = (await name_el.inner_text()).strip()
                        odds_text = (await odds_el.inner_text()).strip().replace(",", ".")
                        try:
                            odds = float(odds_text)
                        except ValueError:
                            continue
                        if odds < 1.01 or odds > 1000.0:
                            continue
                        results.append({
                            "nation":      None if is_player else name,
                            "player_name": name if is_player else None,
                            "market_type": market_type,
                            "bookmaker":   "betclic",
                            "odds":        round(odds, 2),
                        })
                except Exception as exc:
                    logger.debug("Betclic: erreur group parsing: %s", exc)
                    continue

        except Exception as exc:
            logger.error("Betclic outrights: %s", exc)
        finally:
            await browser.close()

    logger.info("Betclic outrights WC2026 total: %d cotes", len(results))
    return results


# ── Storage ───────────────────────────────────────────────────────────────────


async def store_wc_outrights(session: AsyncSession, outrights: list[dict]) -> None:
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
    """Lance les scrapers Unibet + Betclic et stocke les résultats.

    NOTE : appeler depuis un script local (IP résidentielle FR).
    """
    import asyncio

    unibet_results, betclic_results = await asyncio.gather(
        scrape_unibet_wc_outrights(),
        scrape_betclic_wc_outrights(),
    )

    all_results = unibet_results + betclic_results
    if all_results:
        await store_wc_outrights(session, all_results)

    logger.info(
        "sync_all_wc_outrights: unibet=%d betclic=%d total=%d",
        len(unibet_results), len(betclic_results), len(all_results),
    )
    return len(all_results)
