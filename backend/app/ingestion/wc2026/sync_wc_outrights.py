# backend/app/ingestion/wc2026/sync_wc_outrights.py
"""Scrape WC2026 outright odds from Unibet (LVS), Betclic and PMU (Playwright).

Outrights = marchés de tournoi : vainqueur CDM, top4, top8, buteur, passeur.
Stockés dans wc2026_outright_odds avec upsert sur (nation, player_name, market_type, bookmaker).

NOTE : ce scraper doit tourner en LOCAL (IP résidentielle FR).
       Ne pas appeler depuis le worker VPS.
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

# Titre de marché (lowercase) → market_type interne
# Classer du plus spécifique au plus générique pour éviter les faux-positifs.
_LVS_MARKET_TITLE_MAP: list[tuple[str, str]] = [
    # Joueurs
    ("meilleur buteur",              "top_scorer"),
    ("top scorer",                   "top_scorer"),
    ("meilleur passeur",             "top_assister"),
    ("top assister",                 "top_assister"),
    # Nations — stades atteints (ex : "Quelle équipe atteindra les demi-finales ?")
    ("atteindra les demi-finales",   "top4"),
    ("reach the semi-final",         "top4"),
    ("atteindra les quarts de finale", "top8"),
    ("reach the quarter-final",      "top8"),
    ("quarts de finale",             "top8"),
    ("atteindra les 1/8",            "group_stage"),
    ("reach the round of 16",        "group_stage"),
    # Générique
    ("vainqueur",                    "winner"),
    ("winner",                       "winner"),
    ("top 4",                        "top4"),
    ("top4",                         "top4"),
    ("top 8",                        "top8"),
    ("top8",                         "top8"),
    ("phase de groupe",              "group_stage"),
    ("group stage",                  "group_stage"),
]

# Params requis par l'API LVS Unibet (découverts par analyse du trafic navigateur)
_LVS_FF_PARAMS = {
    "lineId":                  "1",
    "originId":                "3",
    "breakdownEventsIntoDays": "true",
    "showPromotions":          "true",
    "pageIndex":               "0",
    "ext":                     "1",
}

_LVS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR",
    "Referer": "https://www.unibet.fr/paris-football",
    "sec-ch-ua": '"Chromium";v="125", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

# Event ID de l'outright CDM 2026 sur Unibet (confirmé via analyse du trafic)
_LVS_WC2026_OUTRIGHT_EVENT = 3217261


def _lvs_detect_market_type(title: str) -> str | None:
    t = title.lower()
    for keyword, mtype in _LVS_MARKET_TITLE_MAP:
        if keyword in t:
            return mtype
    return None


async def _lvs_get_token(client: httpx.AsyncClient) -> str | None:
    try:
        r = await client.get(f"{LVS_BASE}/lvs-api/acc/token", timeout=10.0)
        r.raise_for_status()
        return r.json().get("hsToken", "")
    except Exception as exc:
        logger.error("Unibet: échec token LVS: %s", exc)
        return None


async def scrape_unibet_wc_outrights() -> list[dict]:
    """Scrape les outrights CDM depuis Unibet (LVS).

    Stratégie :
      1. Obtient le token via /lvs-api/acc/token.
      2. Appelle ff/e{event_id}?ext=1 sur l'événement outright WC2026 connu.
      3. Parse les marchés (clé "m*") et outcomes (clé "o*") avec les champs
         réels : desc (nom), price (cote comma-decimal), parent (marché), flags.

    NOTE : l'API LVS fonctionne uniquement depuis des IP non-datacenter.
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        token = await _lvs_get_token(client)
        if not token:
            return []

        headers = {**_LVS_HEADERS, "X-LVS-HSToken": token}
        try:
            r = await client.get(
                f"{LVS_BASE}/lvs-api/ff/e{_LVS_WC2026_OUTRIGHT_EVENT}",
                params=_LVS_FF_PARAMS,
                headers=headers,
                timeout=20.0,
            )
            if r.status_code != 200:
                logger.warning("Unibet ff/e%d: HTTP %d", _LVS_WC2026_OUTRIGHT_EVENT, r.status_code)
                return []
            ff_items = r.json().get("items", {})
        except Exception as exc:
            logger.error("Unibet ff/e%d: %s", _LVS_WC2026_OUTRIGHT_EVENT, exc)
            return []

        # Sépare marchés et outcomes
        markets: dict[str, dict] = {}
        outcomes: list[dict] = []
        for k, v in ff_items.items():
            if k.startswith("m"):
                markets[k] = v
            elif k.startswith("o"):
                outcomes.append({**v, "_key": k})

        results: list[dict] = []
        for mkey, market in markets.items():
            market_type = _lvs_detect_market_type(market.get("desc", ""))
            if not market_type:
                continue
            is_player = market_type in ("top_scorer", "top_assister")

            for o in outcomes:
                # field "parent" lie l'outcome à son marché
                if o.get("parent") != mkey:
                    continue
                # skip les outcomes suspendus
                if "hidden" in (o.get("flags") or []):
                    continue
                name = o.get("desc", "")
                odds = _parse_lvs_price(o.get("price"))
                if not name or odds is None:
                    continue
                results.append({
                    "nation":      None if is_player else name,
                    "player_name": name if is_player else None,
                    "market_type": market_type,
                    "bookmaker":   "unibet",
                    "odds":        odds,
                })

    logger.info("Unibet outrights WC2026: %d cotes", len(results))
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


# ── PMU (Kambi API) ───────────────────────────────────────────────────────────

_PMU_KAMBI_BASE = "https://eu.offering-api.kambicdn.com/offering/v2018/pmusportsfr"
_PMU_COMPETITIONS_URL = (
    f"{_PMU_KAMBI_BASE}/listView/football/world_cup_2026/all/all/competitions.json"
)
_PMU_KAMBI_PARAMS = {
    "channel_id":     "1",
    "client_id":      "200",
    "lang":           "fr_MC",
    "market":         "FR",
    "currency":       "EUR",
    "include":        "betOffers",
    "betOfferStates": "OPEN",
    "rangeSize":      "200",
}

# Mots-clés dans englishName de l'event Kambi → market_type interne (marchés globaux)
_PMU_EVENT_MARKET_MAP: list[tuple[str, str]] = [
    ("top goalscorer", "top_scorer"),
    ("top assists",    "top_assister"),
    ("top assister",   "top_assister"),
]

# Préfixes de groupes de poule : "group a" … "group l" → group_stage
_PMU_GROUP_PREFIX = "group "

# Label d'outcome dans les events "{Nation} Markets 2026" → market_type
# Classer du plus spécifique au plus générique.
_PMU_NATION_OUTCOME_MAP: list[tuple[str, str]] = [
    ("demi-finale",      "top4"),
    ("semi-final",       "top4"),
    ("quart de finale",  "top8"),
    ("quarter-final",    "top8"),
    ("1/8",              "group_stage"),
    ("huitième",         "group_stage"),
    ("round of 16",      "group_stage"),
    ("round of 32",      "group_stage"),
]


def _pmu_detect_market_type(event_name: str, bet_offer_type: str) -> str | None:
    """Mappe un event Kambi WC2026 vers un market_type interne (marchés globaux)."""
    en = event_name.lower()
    for kw, mtype in _PMU_EVENT_MARKET_MAP:
        if kw in en:
            return mtype
    if en.startswith(_PMU_GROUP_PREFIX) and len(en) == len(_PMU_GROUP_PREFIX) + 1:
        return "group_stage"
    if "world cup" in en and "market" not in en and "special" not in en:
        bt = bet_offer_type.lower()
        if "classement" in bt or "winner" in bt or "ranking" in bt:
            return "winner"
    return None


def _pmu_detect_nation_outcome(label: str) -> str | None:
    """Mappe le label d'un outcome dans un event '{Nation} Markets 2026'."""
    lbl = label.lower()
    for kw, mtype in _PMU_NATION_OUTCOME_MAP:
        if kw in lbl:
            return mtype
    return None


def _pmu_extract_nation_from_markets_event(event_name: str) -> str | None:
    """Extrait le nom de la nation depuis un event '{Nation} Markets 2026'.

    Ex: "France Markets 2026" → "France"
        "Brazil World Cup Markets" → "Brazil"
    """
    for suffix in (" Markets 2026", " World Cup 2026 Markets", " World Cup Markets", " markets 2026"):
        if event_name.lower().endswith(suffix.lower()):
            return event_name[: len(event_name) - len(suffix)].strip()
    return None


async def scrape_pmu_wc_outrights() -> list[dict]:
    """Scrape les outrights CDM depuis PMU via l'API Kambi.

    Endpoint : eu.offering-api.kambicdn.com/.../competitions.json
    Cotes Kambi : stockées en millièmes (ex: 10000 → 10.0 décimal).
    """
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.get(_PMU_COMPETITIONS_URL, params=_PMU_KAMBI_PARAMS, timeout=15.0)
            if r.status_code != 200:
                logger.warning("PMU Kambi competitions: HTTP %d", r.status_code)
                return []
            events = r.json().get("events", [])
        except Exception as exc:
            logger.error("PMU Kambi competitions: %s", exc)
            return []

    results: list[dict] = []
    for ev in events:
        event = ev.get("event", {})
        # Skip les matches (ont une équipe away)
        if event.get("awayName") or event.get("awayTeam"):
            continue

        event_name = event.get("englishName", event.get("name", ""))

        for bo in ev.get("betOffers", []):
            bet_offer_type = bo.get("betOfferType", {}).get("name", "")

            # ── Cas spécial : events "{Nation} Markets 2026" ──────────────────
            # Chaque outcome représente un market_type différent pour cette nation.
            # Ex: "France Markets 2026" → outcomes "Dernier stade atteint : Demi-finale",
            #     "Dernier stade atteint : Quart de finale", etc.
            if "market" in event_name.lower() and "compétition" in bet_offer_type.lower():
                nation = _pmu_extract_nation_from_markets_event(event_name)
                if nation:
                    for o in bo.get("outcomes", []):
                        if o.get("status") not in (None, "OPEN"):
                            continue
                        label = o.get("label", o.get("englishLabel", ""))
                        raw_odds = o.get("odds", 0)
                        if not label or not raw_odds:
                            continue
                        mtype = _pmu_detect_nation_outcome(label)
                        if not mtype:
                            continue
                        odds = round(raw_odds / 1000, 2)
                        if odds < 1.01 or odds > 1000.0:
                            continue
                        results.append({
                            "nation":      nation,
                            "player_name": None,
                            "market_type": mtype,
                            "bookmaker":   "pmu",
                            "odds":        odds,
                        })
                continue  # ne pas repasser dans la détection standard

            # ── Marchés globaux standard ──────────────────────────────────────
            market_type = _pmu_detect_market_type(event_name, bet_offer_type)
            if not market_type:
                continue
            is_player = market_type in ("top_scorer", "top_assister")

            for o in bo.get("outcomes", []):
                if o.get("status") not in (None, "OPEN"):
                    continue
                label = o.get("label", o.get("englishLabel", ""))
                raw_odds = o.get("odds", 0)
                if not label or not raw_odds:
                    continue
                odds = round(raw_odds / 1000, 2)
                if odds < 1.01 or odds > 1000.0:
                    continue
                results.append({
                    "nation":      None if is_player else label,
                    "player_name": label if is_player else None,
                    "market_type": market_type,
                    "bookmaker":   "pmu",
                    "odds":        odds,
                })

    logger.info("PMU outrights WC2026: %d cotes", len(results))
    return results


# ── Betclic (Playwright) ───────────────────────────────────────────────────────

_BETCLIC_COMPETITION_URL = "https://www.betclic.fr/football-sfootball/coupe-du-monde-2026-c1"

# Mots-clés dans marketBox_headTitle → market_type interne
# Les plus spécifiques en premier pour éviter les faux-positifs.
# "meilleur buteur - " (avec " - ") cible le marché global et pas "Meilleur Buteur France".
_BETCLIC_MARKET_MAP: list[tuple[str, str]] = [
    # Nations — stades atteints (titres Betclic réels observés)
    ("stade de la compétition atteint - demi-finale",     "top4"),
    ("stade de la compétition atteint - quart de finale", "top8"),
    ("stade de la compétition atteint - huitième",        "group_stage"),
    # Joueurs — " - " distingue le marché global des marchés par équipe
    ("meilleur buteur - ",  "top_scorer"),
    ("meilleur passeur - ", "top_assister"),
    # Vainqueur global
    ("vainqueur compétition", "winner"),
]

# Titres à exclure même s'ils contiennent un mot-clé
_BETCLIC_MARKET_EXCLUDE = [
    "double chance",
    "vainqueur groupe",
    "gagnant /",
    "gagnant/",
    "qualification -",   # marchés par groupe (4 équipes seulement)
    "duo ",              # Duo gagnant / Duo placé
    "nombre de buts",    # marchés individuels (ex : Nombre de buts de Mbappé)
    "nombre de passes",
    "joueur inscivant",
    "les bons plans",
    "les finalistes",    # pari combiné deux finalistes
    "meilleure équipe",
]


def _betclic_detect_market_type(title: str) -> str | None:
    t = title.lower()
    for excl in _BETCLIC_MARKET_EXCLUDE:
        if excl in t:
            return None
    for keyword, mtype in _BETCLIC_MARKET_MAP:
        if keyword in t:
            return mtype
    return None


async def scrape_betclic_wc_outrights() -> list[dict]:
    """Scrape les outrights CDM depuis Betclic via Playwright (Angular SPA).

    Stratégie :
      1. Navigue sur la page compétition WC2026.
      2. Supprime le popup de consentement Trust Commander via JS.
      3. Clique sur l'onglet "Compétition" (dispatch_event pour bypasser overlays).
      4. Trouve les marchés via h2.marketBox_headTitle et extrait les sélections
         via div.marketBox_lineSelection > p.marketBox_label + button.btn.is-odd.

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
            await page.wait_for_timeout(4_000)

            final_url = page.url
            if "betclic.fr" not in final_url or final_url.rstrip("/") == "https://www.betclic.fr":
                logger.warning("Betclic: redirect vers homepage — page non accessible sans login ?")
                return []

            # Supprime le popup Trust Commander s'il est présent
            await page.evaluate(
                "document.querySelector('#tc-privacy-wrapper')?.remove(); "
                "document.querySelectorAll('[class*=\"tc-privacy\"]').forEach(e => e.remove())"
            )

            # Clique sur l'onglet "Compétition" via dispatch pour bypasser tout overlay résiduel
            comp_els = await page.get_by_text("Compétition", exact=True).all()
            if comp_els:
                await comp_els[0].dispatch_event("click")
                await page.wait_for_timeout(4_000)
            else:
                logger.info("Betclic WC2026: pas d'onglet Compétition trouvé")

            # Trouve tous les titres de marchés
            title_els = await page.query_selector_all("h2.marketBox_headTitle")
            logger.info("Betclic WC2026: %d titres de marchés trouvés", len(title_els))

            for title_el in title_els:
                title = ""
                try:
                    title = (await title_el.inner_text()).strip()
                    market_type = _betclic_detect_market_type(title)
                    if not market_type:
                        continue
                    is_player = market_type in ("top_scorer", "top_assister")

                    # Parent div.marketBox contenant les sélections
                    market_box = await title_el.evaluate_handle(
                        "el => el.closest('.marketBox')"
                    )
                    if not market_box:
                        continue

                    # Déplie le marché si fermé (triangle inversé / accordéon)
                    # 1. Cherche un bouton expand avec aria-expanded=false
                    expand_btn = await market_box.query_selector(
                        "[aria-expanded='false'], [aria-expanded='0']"
                    )
                    if not expand_btn:
                        # 2. Cherche les boutons "Voir" / "Voir tout" / "Voir les N+"
                        expand_btn = await market_box.query_selector(
                            "a[class*='see'], button[class*='see'], "
                            "[class*='seeAll'], [class*='showMore'], "
                            "a[class*='more'], button[class*='more']"
                        )
                    if not expand_btn:
                        expand_btn = await market_box.query_selector(
                            "a:has-text('Voir'), button:has-text('Voir')"
                        )
                    if expand_btn:
                        try:
                            await expand_btn.dispatch_event("click")
                            await page.wait_for_timeout(1_500)
                        except Exception:
                            pass

                    selections = await market_box.query_selector_all(".marketBox_lineSelection")
                    for sel in selections:
                        name_el = await sel.query_selector("p.marketBox_label")
                        odds_el = await sel.query_selector("button.btn.is-odd, bcdk-bet-button-odds-animated")
                        if not name_el or not odds_el:
                            continue
                        name = (await name_el.inner_text()).strip()
                        # Filtre les sélections combinées "Équipe / Joueur"
                        if " / " in name:
                            continue
                        odds_text = (await odds_el.inner_text()).strip().replace(",", ".")
                        try:
                            odds = round(float(odds_text), 2)
                        except ValueError:
                            continue
                        if odds < 1.01 or odds > 1000.0:
                            continue
                        results.append({
                            "nation":      None if is_player else name,
                            "player_name": name if is_player else None,
                            "market_type": market_type,
                            "bookmaker":   "betclic",
                            "odds":        odds,
                        })
                except Exception as exc:
                    logger.debug("Betclic: erreur parsing marché '%s': %s", title, exc)
                    continue

        except Exception as exc:
            logger.error("Betclic outrights: %s", exc)
        finally:
            await browser.close()

    logger.info("Betclic outrights WC2026 total: %d cotes", len(results))
    return results


# ── Storage ───────────────────────────────────────────────────────────────────


async def store_wc_outrights_for_bookmaker(
    session: AsyncSession, outrights: list[dict], bookmaker: str
) -> dict:
    """Upsert les cotes d'un bookmaker et désactive celles qui ont disparu.

    Transitions trackées :
    - odds_changed_at  : mis à jour quand la cote change de valeur
    - republished_at   : mis à jour quand une cote inactive redevient active
    - is_active=FALSE  : pour les lignes absentes du batch courant
    """
    if outrights:
        await session.execute(
            text("""
                INSERT INTO wc2026_outright_odds
                  (nation, player_name, market_type, bookmaker, odds,
                   is_active, first_seen_at, last_seen_at, odds_changed_at,
                   republished_at, scraped_at)
                VALUES
                  (:nation, :player_name, :market_type, :bookmaker, :odds,
                   TRUE, now(), now(), now(), NULL, now())
                ON CONFLICT (nation, player_name, market_type, bookmaker)
                DO UPDATE SET
                  odds           = EXCLUDED.odds,
                  is_active      = TRUE,
                  last_seen_at   = now(),
                  scraped_at     = now(),
                  odds_changed_at = CASE
                    WHEN wc2026_outright_odds.odds != EXCLUDED.odds THEN now()
                    ELSE wc2026_outright_odds.odds_changed_at
                  END,
                  republished_at = CASE
                    WHEN NOT wc2026_outright_odds.is_active THEN now()
                    ELSE wc2026_outright_odds.republished_at
                  END
            """),
            outrights,
        )

    # Désactive les lignes qui n'étaient pas dans le batch courant
    # Récupère d'abord les lignes actives pour ce bookmaker
    existing_res = await session.execute(
        text(
            "SELECT id, nation, player_name, market_type "
            "FROM wc2026_outright_odds WHERE bookmaker = :bk AND is_active = TRUE"
        ),
        {"bk": bookmaker},
    )
    scraped_keys = {
        (r.get("nation"), r.get("player_name"), r.get("market_type"))
        for r in outrights
    }
    ids_to_deactivate = [
        row.id
        for row in existing_res.all()
        if (row.nation, row.player_name, row.market_type) not in scraped_keys
    ]
    if ids_to_deactivate:
        await session.execute(
            text("UPDATE wc2026_outright_odds SET is_active = FALSE WHERE id = ANY(:ids)"),
            {"ids": ids_to_deactivate},
        )

    await session.commit()
    logger.info(
        "store_wc_outrights[%s]: %d upsertées, %d désactivées",
        bookmaker, len(outrights), len(ids_to_deactivate),
    )
    return {"scraped": len(outrights), "deactivated": len(ids_to_deactivate)}


async def store_wc_outrights(session: AsyncSession, outrights: list[dict]) -> None:
    """Compatibilité ascendante : appelle store_wc_outrights_for_bookmaker par bookmaker."""
    from collections import defaultdict
    by_bk: dict[str, list[dict]] = defaultdict(list)
    for r in outrights:
        by_bk[r["bookmaker"]].append(r)
    for bk, rows in by_bk.items():
        await store_wc_outrights_for_bookmaker(session, rows, bk)


async def sync_all_wc_outrights(session: AsyncSession) -> int:
    """Lance les scrapers Unibet + PMU + Betclic et stocke les résultats.

    NOTE : appeler depuis un script local (IP résidentielle FR).
    """
    import asyncio

    unibet_results, pmu_results, betclic_results = await asyncio.gather(
        scrape_unibet_wc_outrights(),
        scrape_pmu_wc_outrights(),
        scrape_betclic_wc_outrights(),
    )

    total = 0
    for bk, rows in [("unibet", unibet_results), ("pmu", pmu_results), ("betclic", betclic_results)]:
        await store_wc_outrights_for_bookmaker(session, rows, bk)
        total += len(rows)

    logger.info(
        "sync_all_wc_outrights: unibet=%d pmu=%d betclic=%d total=%d",
        len(unibet_results), len(pmu_results), len(betclic_results), total,
    )
    return total
