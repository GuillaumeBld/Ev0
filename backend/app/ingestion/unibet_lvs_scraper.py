"""Unibet LVS scraper — nouveau site post-fusion PSEL (mars 2026).

La plateforme Kambi (eu-offering-api.kambicdn.com) a été abandonnée.
Le nouveau www.unibet.fr tourne sur la plateforme LVS (Lineup7/SportEase).

API publique, token anonyme sans compte requis.

Marchés extraits :
- markettypeId=1         : 1X2 (résultat du match)   → h2h
- markettypeId=994001025 : Plus / Moins X.5 But(s)   → totals (un marché par seuil)
- markettypeId=350       : Les 2 équipes marqueront?  → btts
- markettypeId=31        : Buteur anytime             → goalscorer
- markettypeId=4         : 1er Buteur                 → goalscorer (dédupliqué)
- markettypeId=100001899 : Nbre de passes décisives   → assist (disponible sur tous les Big 5)

CLI usage (dry-run):
    python -m app.ingestion.unibet_lvs_scraper --dry-run
    python -m app.ingestion.unibet_lvs_scraper --league ligue_1 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.ingestion.scrape_result import MatchScrapeResult, PlayerOdds

logger = logging.getLogger(__name__)

LVS_BASE = "https://www.unibet.fr"
BOOKMAKER = "unibet"

LVS_NODE_IDS: dict[str, int] = {
    "ligue_1":          58531576,
    "premier_league":   58532401,
    "bundesliga":       58529612,
    "la_liga":          58532237,
    "serie_a":          58529754,
    "champions_league": 58532497,
}

# markettypeId → market_type Ev0
_MARKET_TYPES: dict[int, str] = {
    # Match-level (confirmed by audit)
    1:          "h2h",        # 1 N 2 — résultat du match (draw outcome = "N")
    994001025:  "totals",     # Plus / Moins X.5 But(s) — over/under per threshold
    350:        "btts",       # Les 2 équipes marqueront-elles?
    # Player props
    31:        "goalscorer",   # Buteur anytime (priorité haute)
    4:         "goalscorer",   # 1er Buteur (priorité basse — dédupliqué)
    100001899: "assist",       # Nbre de passes décisives (outcomes: "Nom 1+", "Nom 2+")
}

# markettypeId=31 est l'anytime scorer, prioritaire sur le 1er buteur (4)
_ANYTIME_SCORER_ID = 31

_EVENT_SLEEP = 0.3

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.unibet.fr/",
}

_LIST_PARAMS = "lineId=1&originId=3&ext=1&showPromotions=true&showMarketTypeGroups=true"


def _parse_price(value: Any) -> float | None:
    """Convertit une cote LVS en float décimal.

    LVS utilise la virgule française : "3,05" → 3.05.
    Retourne None si suspendu (null), invalide, ou hors bornes [1.01, 1000].
    """
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
    return f


def _parse_start(value: Any) -> datetime | None:
    """Parse le timestamp LVS format YYMMDDHHMM en datetime UTC.

    Exemple : "2604051930" → 2026-04-05 19:30 UTC
    """
    if not value:
        return None
    s = str(value).strip()
    if len(s) != 10:
        return None
    try:
        year   = 2000 + int(s[0:2])
        month  = int(s[2:4])
        day    = int(s[4:6])
        hour   = int(s[6:8])
        minute = int(s[8:10])
        return datetime(year, month, day, hour, minute, tzinfo=UTC)
    except (ValueError, IndexError):
        return None


class UnibetLVSScraper:
    """Scrape les cotes buteur/passeur depuis l'API LVS de www.unibet.fr."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._token: str | None = None

    async def _get_token(self) -> str:
        """Obtenir ou renouveler le token anonyme LVS."""
        url = f"{LVS_BASE}/lvs-api/acc/token"
        r = await self._client.get(url, headers=_HEADERS, timeout=10)
        r.raise_for_status()
        self._token = r.json()["hsToken"]
        return self._token

    def _auth_headers(self) -> dict[str, str]:
        return {**_HEADERS, "X-LVS-HSToken": self._token or ""}

    async def fetch_event_ids(self, league: str) -> list[tuple[int, str, str, datetime | None]]:
        """Retourne (event_id, home, away, kickoff) pour les matchs à venir d'une ligue."""
        node_id = LVS_NODE_IDS.get(league)
        if not node_id:
            logger.warning("UnibetLVSScraper: ligue inconnue %s", league)
            return []

        url = f"{LVS_BASE}/lvs-api/next/50/p{node_id}?{_LIST_PARAMS}"
        try:
            r = await self._client.get(url, headers=self._auth_headers(), timeout=15)
            r.raise_for_status()
        except Exception as exc:
            logger.warning("UnibetLVSScraper: fetch_event_ids %s: %s", league, exc)
            return []

        items = r.json().get("items", {})
        results = []
        now = datetime.now(UTC)

        for key, val in items.items():
            if not re.match(r"^e\d+$", key):
                continue
            event_id = int(key[1:])
            home = val.get("a", "")
            away = val.get("b", "")
            kickoff = _parse_start(val.get("start"))
            if not home or not away:
                continue
            if kickoff and kickoff < now:
                continue
            results.append((event_id, home, away, kickoff))

        return results

    def _parse_match_items(
        self,
        items: dict[str, Any],
        league: str,
        fixture_id: int = 0,
    ) -> MatchScrapeResult | None:
        """Parse les items /ff/ d'un match en MatchScrapeResult.

        La réponse LVS mélange événements (e...), marchés (m...) et
        outcomes (o...) dans un dict plat. On reconstruit la hiérarchie
        via les clés "parent".
        """
        # Trouver l'événement
        event_key = next((k for k in items if re.match(r'^e\d+$', k)), None)
        if not event_key:
            return None

        event = items[event_key]
        home = event.get("a", "")
        away = event.get("b", "")
        kickoff = _parse_start(event.get("start"))

        if not home or not away:
            return None

        # Indexer les marchés ciblés : market_key → (markettypeId, market_type)
        target_markets: dict[str, tuple[int, str]] = {}
        for key, val in items.items():
            if not key.startswith("m"):
                continue
            mtype_id = val.get("markettypeId")
            if mtype_id not in _MARKET_TYPES:
                continue
            if val.get("parent") != event_key:
                continue
            target_markets[key] = (mtype_id, _MARKET_TYPES[mtype_id])

        if not target_markets:
            return None

        # Collect outcomes grouped by market key
        market_outcomes: dict[str, list[tuple[str, float]]] = {}  # market_key → [(desc, odds)]
        # For goalscorer dedup
        selections_anytime: dict[str, PlayerOdds] = {}
        selections_first: dict[str, PlayerOdds] = {}
        selections_assist: dict[str, PlayerOdds] = {}

        for key, val in items.items():
            if not key.startswith("o"):
                continue
            parent = val.get("parent", "")
            if parent not in target_markets:
                continue

            mtype_id, market_type = target_markets[parent]
            desc = (val.get("desc") or "").strip()
            if not desc:
                continue
            odds = _parse_price(val.get("price"))
            if odds is None:
                continue

            if market_type in ("h2h", "totals", "btts"):
                market_outcomes.setdefault(parent, []).append((desc, odds))
            elif market_type == "goalscorer":
                name_lower = desc.lower()
                if mtype_id == _ANYTIME_SCORER_ID:
                    selections_anytime.setdefault(name_lower, PlayerOdds(desc, odds))
                else:
                    selections_first.setdefault(name_lower, PlayerOdds(desc, odds))
            elif market_type == "assist":
                # Unibet format: "Sotoca, Florian 1+" / "Sotoca, Florian 2+"
                # Strip trailing threshold suffix — keep "1+" (anytime assist, lowest odds)
                import re as _re
                player_name = _re.sub(r"\s+\d+\+\s*$", "", desc).strip()
                if not player_name:
                    player_name = desc
                name_lower = player_name.lower()
                # Keep lowest odds (1+ threshold = anytime assist)
                existing = selections_assist.get(name_lower)
                if existing is None or odds < existing.odds:
                    selections_assist[name_lower] = PlayerOdds(player_name, odds)

        # Build match-level odds
        h2h: dict | None = None
        totals_dict: dict = {}  # accumulated across all threshold markets
        btts: dict | None = None

        for mkey, sels in market_outcomes.items():
            mtype_id, market_type = target_markets[mkey]

            if market_type == "h2h" and len(sels) == 3 and h2h is None:
                # Draw outcome in Unibet is "N" (not "Nul") — match single char or full word
                draw_odds = None
                non_draw: list[tuple[str, float]] = []
                for desc, odds in sels:
                    d = desc.strip().lower()
                    if d == "n" or "nul" in d:
                        draw_odds = odds
                    else:
                        non_draw.append((desc, odds))
                if draw_odds is not None and len(non_draw) == 2:
                    h2h = {"home": non_draw[0][1], "draw": draw_odds, "away": non_draw[1][1]}

            elif market_type == "totals" and sels:
                # 994001025 creates one market per threshold (0.5, 1.5, 2.5, 3.5, 4.5…)
                # Accumulate all thresholds into a single dict
                for desc, odds in sels:
                    d = desc.replace(",", ".").lower()
                    plus = "plus" in d or d.strip().startswith("+")
                    if "4.5" in d:
                        totals_dict.setdefault("over_4.5" if plus else "under_4.5", odds)
                    elif "3.5" in d:
                        totals_dict.setdefault("over_3.5" if plus else "under_3.5", odds)
                    elif "2.5" in d:
                        totals_dict.setdefault("over_2.5" if plus else "under_2.5", odds)
                    elif "1.5" in d:
                        totals_dict.setdefault("over_1.5" if plus else "under_1.5", odds)

            elif market_type == "btts" and len(sels) == 2:
                # Outcomes: "Oui" / "Non"
                yes_odds = next((o for d, o in sels if "oui" in d.lower()), None)
                no_odds = next((o for d, o in sels if "non" in d.lower()), None)
                if yes_odds and no_odds:
                    btts = {"yes": yes_odds, "no": no_odds}

        # Fusionner goalscorer : anytime > first
        final_goalscorer = list({**selections_first, **selections_anytime}.values())
        totals: dict | None = totals_dict if totals_dict else None

        return MatchScrapeResult(
            fixture_id=fixture_id,
            home_team=home,
            away_team=away,
            kickoff_utc=kickoff,
            league=league,
            bookmaker=BOOKMAKER,
            scraped_at=datetime.now(UTC),
            h2h=h2h,
            totals=totals,
            btts=btts,
            goalscorer=final_goalscorer,
            assist=list(selections_assist.values()),
        )

    async def scrape_league(self, league: str) -> list[MatchScrapeResult]:
        """Scrape une ligue : liste événements → marchés → MatchScrapeResult."""
        events = await self.fetch_event_ids(league)
        if not events:
            logger.info("UnibetLVSScraper %s: 0 événements", league)
            return []

        logger.info("UnibetLVSScraper %s: %d événements à traiter", league, len(events))
        results: list[MatchScrapeResult] = []

        for event_id, home, away, kickoff in events:
            url = f"{LVS_BASE}/lvs-api/ff/e{event_id}?{_LIST_PARAMS}"
            try:
                r = await self._client.get(url, headers=self._auth_headers(), timeout=15)
                r.raise_for_status()
                items = r.json().get("items", {})
            except Exception as exc:
                logger.warning("UnibetLVSScraper: ff/e%d échoué (%s vs %s): %s", event_id, home, away, exc)
                await asyncio.sleep(_EVENT_SLEEP)
                continue

            result = self._parse_match_items(items, league)
            if result and (result.h2h or result.totals or result.btts or result.goalscorer or result.assist):
                if not result.assist:
                    logger.warning(
                        "UnibetLVSScraper: 0 assist odds for %s vs %s [%s] — "
                        "markettypeId=100001899 absent from LVS response",
                        result.home_team, result.away_team, league,
                    )
                results.append(result)

            await asyncio.sleep(_EVENT_SLEEP)

        total_sel = sum(len(r.goalscorer) for r in results)
        logger.info(
            "UnibetLVSScraper %s: %d matchs, %d sélections buteur",
            league, len(results), total_sel,
        )
        return results


async def scrape_all_unibet(leagues: list[str]) -> list[MatchScrapeResult]:
    """Scrape les cotes Unibet pour toutes les ligues via l'API LVS."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        scraper = UnibetLVSScraper(client)

        try:
            await scraper._get_token()
        except Exception as exc:
            logger.error("UnibetLVSScraper: impossible d'obtenir le token LVS: %s", exc)
            return []

        all_results: list[MatchScrapeResult] = []
        for league in leagues:
            try:
                results = await scraper.scrape_league(league)
                all_results.extend(results)
            except Exception as exc:
                logger.error(
                    "UnibetLVSScraper: erreur sur %s: %s", league, exc, exc_info=True
                )

    logger.info(
        "scrape_all_unibet: %d matchs sur %d ligues",
        len(all_results), len(leagues),
    )
    return all_results


# ── CLI ─────────────────────────────────────────────────────────────────────


async def _cli_main(args: argparse.Namespace) -> None:
    leagues = [args.league] if args.league else list(LVS_NODE_IDS.keys())
    all_results = await scrape_all_unibet(leagues)

    total_sel = sum(len(r.goalscorer) for r in all_results)
    print(f"\n{'=' * 60}")
    print(f"Unibet LVS: {len(all_results)} matchs, {total_sel} sélections buteur")
    print(f"{'=' * 60}")

    if args.dry_run:
        for r in all_results:
            print(f"\n  {r.home_team} vs {r.away_team}  [{r.league}]")
            if r.kickoff_utc:
                print(f"  Kickoff: {r.kickoff_utc.strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"  h2h:   {r.h2h}")
            print(f"  totals: {r.totals}")
            print(f"  btts:   {r.btts}")
            print(f"  Buteurs: {len(r.goalscorer)}  Passeurs: {len(r.assist)}")
            for p in sorted(r.goalscorer, key=lambda x: x.odds)[:5]:
                print(f"    [G] {p.player_name}: {p.odds:.2f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Scrape Unibet LVS odds")
    parser.add_argument("--league", choices=list(LVS_NODE_IDS.keys()), default=None)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(_cli_main(parser.parse_args()))
