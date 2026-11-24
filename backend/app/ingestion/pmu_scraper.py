"""PMU paris sportifs scraper — plateforme Kambi.

PMU utilise l'API publique Kambi (même plateforme que l'ancien Unibet avant mars 2026).
Aucun token requis, réponses JSON directes.

Offering base: https://eu1.offering-api.kambicdn.com/offering/v2018/pmusportsfr

Endpoints:
  /listView/football.json?lang=fr_FR&market=FR&useCombined=true&limit=500
      → liste des matchs à venir avec betoffers résumés
  /betoffer/event/{eventId}.json?lang=fr_FR&market=FR
      → tous les marchés d'un match

Format des cotes Kambi : entier × 1000 (ex: 2330 = 2.33 décimal)

Marchés extraits :
  betOfferType "Match"              + criterion "Full Time"          → h2h
  betOfferType "Plus de/Moins de"   + criterion "Total Goals" (FT)   → totals
  betOfferType "Oui/Non"            + criterion "Both Teams To Score" → btts
  betOfferType "Player Occurrence Line" + criterion "To Score" (FT)  → goalscorer
  betOfferType "Player Occurrence Line" + "assist" dans englishLabel → assist

CLI usage (dry-run):
    python -m app.ingestion.pmu_scraper --dry-run
    python -m app.ingestion.pmu_scraper --league ligue_1 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.ingestion.book_competitions import PMU_DOMESTIC, PMU_NO_COUNTRY
from app.ingestion.scrape_result import AVEC_SUB, SANS_SUB, MatchScrapeResult, PlayerOdds

logger = logging.getLogger(__name__)

KAMBI_BASE = "https://eu1.offering-api.kambicdn.com/offering/v2018/pmusportsfr"
BOOKMAKER = "pmu"

# Nombre de requêtes betoffer en parallèle (API publique, pas de rate limit strict)
_CONCURRENCY = 8

# Championnats nationaux : (pays, nom) → notre league key. Le pays est
# OBLIGATOIRE : le flux PMU contient aussi « Ukraine > Premier League »,
# « Wales > Premier League » et « Austria > Bundesliga », que le seul nom
# rangeait en Premier League anglaise et en Bundesliga allemande (22 matchs
# lus en « Premier League » au lieu de 10, le 24/11/2026).
_KAMBI_DOMESTIC_MAP: dict[tuple[str, str], str] = {
    ("France", "Ligue 1"):          "ligue_1",
    ("England", "Premier League"):  "premier_league",
    ("Germany", "Bundesliga"):      "bundesliga",
    ("Germany", "1. Bundesliga"):   "bundesliga",
    ("Spain", "La Liga"):           "la_liga",
    ("Italy", "Serie A"):           "serie_a",
    ("Italy", "Serie A TIM"):       "serie_a",
    **PMU_DOMESTIC,   # table commune (clé « comp_<id> »)
}

# Noms de compétitions de sélections, reconnus même sous un groupe parent.
_KAMBI_INTERNATIONAL_NAMES: frozenset[str] = frozenset({
    "International Friendly Matches", "World Cup 2026", "UEFA Super Cup",
})

# Compétitions sans pays (rangées directement sous « Football ») :
# englishName → notre league key.
_KAMBI_LEAGUE_MAP: dict[str, str] = {
    **PMU_NO_COUNTRY,  # table commune (clé « comp_<id> »)
    "Champions League":              "champions_league",
    "UEFA Champions League":         "champions_league",
    "International Friendly Matches": "friendly_international",
    "World Cup 2026":                "world_cup_2026",
    "UEFA Super Cup":                "uefa_super_cup",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.pmu.fr",
    "Referer": "https://www.pmu.fr/",
}


# « Garantie Coaching » de PMU : le pari « Marque » / « Effectue une passe
# décisive » reste valable sur le remplaçant. Ce n'est pas un marché à part
# mais une promotion posée sur le marché normal, et le flux ne la signale pas
# (seul le site affiche l'icône). Compétitions couvertes confirmées par
# l'utilisateur le 24/11/2026 : les cinq grands championnats et la Ligue des
# champions. Ligue des nations : supposée, à vérifier en mars 2027. Partout
# ailleurs, la cote PMU est une cote sans sub.
PMU_GARANTIE_COACHING_LEAGUES: frozenset[str] = frozenset({
    "ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a",
    "champions_league",
})


def player_variant(league: str) -> str:
    """Variante des cotes joueur PMU pour une compétition."""
    return AVEC_SUB if league in PMU_GARANTIE_COACHING_LEAGUES else SANS_SUB


# Un match de la liste est lu s'il démarre à moins de ça d'un match attendu.
_KICKOFF_WINDOW = timedelta(hours=3)


def _parse_start(raw: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
    except ValueError:
        return None


def _kambi_odds(raw: int | None) -> float | None:
    """Convertit les cotes Kambi (entier×1000) en décimal. Retourne None si invalide."""
    if not raw or raw <= 1000:
        return None
    return raw / 1000


def _league_from_event(event: dict[str, Any]) -> str | None:
    """Déduit la league key depuis le path hiérarchique de l'événement Kambi."""
    names = [part.get("englishName", "") for part in event.get("path", [])]
    if names and names[0] == "Football":
        names = names[1:]
    if len(names) == 1:
        return _KAMBI_LEAGUE_MAP.get(names[0])
    if len(names) >= 2:
        league = _KAMBI_DOMESTIC_MAP.get((names[-2], names[-1]))
        if league:
            return league
        if names[-1] in _KAMBI_INTERNATIONAL_NAMES:
            return _KAMBI_LEAGUE_MAP[names[-1]]
    return None


def _parse_betoffers(
    event: dict[str, Any],
    betoffers: list[dict[str, Any]],
    league: str,
    scraped_at: datetime,
) -> MatchScrapeResult | None:
    """Construit un MatchScrapeResult depuis les betoffers d'un événement Kambi."""
    h2h: dict[str, float] | None = None
    totals: dict[str, float] = {}
    btts: dict[str, float] | None = None
    goalscorer: list[PlayerOdds] = []
    assist: list[PlayerOdds] = []
    variant = player_variant(league)

    for bo in betoffers:
        btype = bo.get("betOfferType", {}).get("name", "")
        criterion = bo.get("criterion", {})
        outcomes = bo.get("outcomes", [])
        eng_label = criterion.get("englishLabel", "")
        lifetime = criterion.get("lifetime", "")

        # ── H2H ───────────────────────────────────────────────────────────────
        if btype == "Match" and eng_label == "Full Time":
            parsed: dict[str, float] = {}
            for o in outcomes:
                odds = _kambi_odds(o.get("odds"))
                if odds is None:
                    continue
                otype = o.get("type")
                if otype == "OT_ONE":
                    parsed["home"] = odds
                elif otype == "OT_CROSS":
                    parsed["draw"] = odds
                elif otype == "OT_TWO":
                    parsed["away"] = odds
            if len(parsed) == 3:
                h2h = parsed

        # ── Totals ────────────────────────────────────────────────────────────
        elif (
            btype == "Plus de/Moins de"
            and eng_label == "Total Goals"
            and lifetime == "FULL_TIME"
        ):
            line = outcomes[0].get("line") if outcomes else None
            if line is None:
                continue
            threshold = line / 1000  # 2500 → 2.5
            key_base = f"{threshold:.1f}"  # "2.5"
            for o in outcomes:
                odds = _kambi_odds(o.get("odds"))
                if odds is None:
                    continue
                otype = o.get("type")
                if otype == "OT_OVER":
                    totals[f"over_{key_base}"] = odds
                elif otype == "OT_UNDER":
                    totals[f"under_{key_base}"] = odds

        # ── BTTS ──────────────────────────────────────────────────────────────
        elif btype == "Oui/Non" and eng_label == "Both Teams To Score":
            parsed_btts: dict[str, float] = {}
            for o in outcomes:
                odds = _kambi_odds(o.get("odds"))
                if odds is None:
                    continue
                otype = o.get("type")
                if otype == "OT_YES":
                    parsed_btts["yes"] = odds
                elif otype == "OT_NO":
                    parsed_btts["no"] = odds
            if len(parsed_btts) == 2:
                btts = parsed_btts

        # ── Buteurs ───────────────────────────────────────────────────────────
        elif (
            btype == "Player Occurrence Line"
            and eng_label == "To Score"
            and lifetime == "FULL_TIME"
        ):
            for o in outcomes:
                player = o.get("participant")
                odds = _kambi_odds(o.get("odds"))
                if player and odds:
                    goalscorer.append(PlayerOdds(player_name=player, odds=odds, variant=variant))

        # ── Passes décisives ──────────────────────────────────────────────────
        elif btype == "Player Occurrence Line" and "assist" in eng_label.lower():
            for o in outcomes:
                player = o.get("participant")
                odds = _kambi_odds(o.get("odds"))
                if player and odds:
                    assist.append(PlayerOdds(player_name=player, odds=odds, variant=variant))

    if not goalscorer and not h2h:
        return None

    return MatchScrapeResult(
        fixture_id=-1,
        home_team=event.get("homeName", ""),
        away_team=event.get("awayName", ""),
        kickoff_utc=datetime.fromisoformat(
            event["start"].replace("Z", "+00:00")
        ) if event.get("start") else None,
        league=league,
        bookmaker=BOOKMAKER,
        scraped_at=scraped_at,
        h2h=h2h if h2h else None,
        totals=totals if totals else None,
        btts=btts,
        goalscorer=goalscorer,
        assist=assist,
    )


async def _fetch_events(
    client: httpx.AsyncClient,
    leagues: set[str],
    wanted_kickoffs: dict[str, list[datetime]] | None = None,
) -> list[tuple[dict[str, Any], str]]:
    """Retourne la liste des événements Kambi filtrés sur les leagues cibles.

    `wanted_kickoffs[ligue]` : ne garde que les matchs qui démarrent à moins
    de 3 h d'un de ces horaires (None = tous).

    Returns: list of (event_dict, league_key)
    """
    params = {
        "lang": "fr_FR",
        "market": "FR",
        "useCombined": "true",
        "limit": "500",
    }
    try:
        r = await client.get(f"{KAMBI_BASE}/listView/football.json", params=params)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.error("PMU Kambi: erreur listView: %s", exc)
        return []

    result: list[tuple[dict[str, Any], str]] = []
    for entry in data.get("events", []):
        ev = entry.get("event", {})
        league = _league_from_event(ev)
        if not league or league not in leagues:
            continue
        if wanted_kickoffs is not None:
            start = _parse_start(ev.get("start"))
            wanted = wanted_kickoffs.get(league, [])
            if start is not None and not any(abs(start - ko) <= _KICKOFF_WINDOW for ko in wanted):
                continue
        result.append((ev, league))

    return result


async def _fetch_betoffers(
    client: httpx.AsyncClient,
    event_id: int,
    sem: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    async with sem:
        try:
            r = await client.get(
                f"{KAMBI_BASE}/betoffer/event/{event_id}.json",
                params={"lang": "fr_FR", "market": "FR"},
            )
            r.raise_for_status()
            return r.json().get("betOffers", [])
        except Exception as exc:
            logger.warning("PMU Kambi: erreur betoffers event %d: %s", event_id, exc)
            return []


async def scrape_all_pmu(
    leagues: list[str],
    wanted_kickoffs: dict[str, list[datetime]] | None = None,
) -> list[MatchScrapeResult]:
    """Scrape les cotes PMU pour les leagues données.

    Retourne une liste de MatchScrapeResult (fixture_id=-1, sera résolu par OddsScheduler).
    """
    target = set(leagues)
    scraped_at = datetime.now(UTC)

    async with httpx.AsyncClient(headers=_HEADERS, timeout=20) as client:
        events = await _fetch_events(client, target, wanted_kickoffs)
        if not events:
            logger.info("PMU: aucun événement trouvé pour les leagues %s", target)
            return []

        logger.info("PMU: %d événements à scraper", len(events))

        sem = asyncio.Semaphore(_CONCURRENCY)
        tasks = [
            _fetch_betoffers(client, ev.get("id"), sem)
            for ev, _ in events
        ]
        all_betoffers = await asyncio.gather(*tasks)

    results: list[MatchScrapeResult] = []
    for (ev, league), betoffers in zip(events, all_betoffers):
        if not betoffers:
            continue
        result = _parse_betoffers(ev, betoffers, league, scraped_at)
        if result:
            results.append(result)

    logger.info(
        "PMU: %d résultats sur %d événements",
        len(results), len(events),
    )
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

async def _main(dry_run: bool, leagues: list[str]) -> None:
    results = await scrape_all_pmu(leagues)
    if not results:
        print("Aucun résultat.")
        return
    for r in results:
        print(f"\n{'='*60}")
        print(f"{r.home_team} vs {r.away_team} ({r.league}) [{r.kickoff_utc}]")
        if r.h2h:
            h = r.h2h
            print(f"  H2H: {h.get('home')} / {h.get('draw')} / {h.get('away')}")
        if r.totals:
            for k, v in sorted(r.totals.items()):
                print(f"  {k}: {v}")
        if r.btts:
            print(f"  BTTS: oui={r.btts.get('yes')} non={r.btts.get('no')}")
        print(f"  Buteurs: {len(r.goalscorer)}")
        for p in r.goalscorer[:5]:
            print(f"    {p.player_name}: {p.odds}")
        print(f"  Passes décisives: {len(r.assist)}")
        for p in r.assist[:5]:
            print(f"    {p.player_name}: {p.odds}")
    print(f"\nTotal: {len(results)} matchs")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--league",
        default=None,
        help="ligue_1 | premier_league | bundesliga | la_liga | serie_a | champions_league",
    )
    args = parser.parse_args()
    target_leagues = (
        [args.league] if args.league
        else ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "champions_league"]
    )
    asyncio.run(_main(args.dry_run, target_leagues))
