"""Unibet LVS scraper — nouveau site post-fusion PSEL (mars 2026).

La plateforme Kambi (eu-offering-api.kambicdn.com) a été abandonnée.
Le nouveau www.unibet.fr tourne sur la plateforme LVS (Lineup7/SportEase).

API publique, token anonyme sans compte requis.

Marchés extraits :
- markettypeId=31       : Buteur anytime     → market_type="goalscorer"
- markettypeId=4        : 1er Buteur         → market_type="goalscorer" (dédupliqué)
- markettypeId=100002524: Passeur décisif    → market_type="assist"

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

from app.ingestion.direct_scrapers import MatchOdds, SelectionOdds

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
    31:        "goalscorer",   # Buteur anytime (priorité haute)
    4:         "goalscorer",   # 1er Buteur (priorité basse — dédupliqué)
    100002524: "assist",       # Passeur décisif
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
            if not key.startswith("e"):
                continue
            try:
                event_id = int(key[1:])
            except ValueError:
                continue
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
    ) -> MatchOdds | None:
        """Parse les items /ff/ d'un match en MatchOdds.

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

        # Collecter les selections en trois dicts séparés
        # Pour goalscorer : anytime (31) a priorité absolue sur first scorer (4)
        # Merge final : {**selections_first, **selections_anytime} → anytime écrase first
        selections_anytime: dict[str, SelectionOdds] = {}   # player_lower → SelectionOdds
        selections_first:   dict[str, SelectionOdds] = {}
        selections_assist:  dict[str, SelectionOdds] = {}

        for key, val in items.items():
            if not key.startswith("o"):
                continue
            parent = val.get("parent", "")
            if parent not in target_markets:
                continue

            mtype_id, market_type = target_markets[parent]
            player_name = (val.get("desc") or "").strip()
            if not player_name:
                continue

            odds = _parse_price(val.get("price"))
            if odds is None:
                continue

            sel = SelectionOdds(
                market_type=market_type,
                player_name=player_name,
                odds=odds,
                bookmaker=BOOKMAKER,
                raw_data={"markettypeId": mtype_id, "outcome_key": key},
            )
            name_lower = player_name.lower()

            if market_type == "assist":
                selections_assist.setdefault(name_lower, sel)
            elif mtype_id == _ANYTIME_SCORER_ID:
                selections_anytime.setdefault(name_lower, sel)
            else:
                selections_first.setdefault(name_lower, sel)

        # Fusionner : anytime prend priorité sur first scorer
        final_goalscorer: dict[str, SelectionOdds] = {**selections_first, **selections_anytime}

        mo = MatchOdds(
            home_team=home,
            away_team=away,
            kickoff_utc=kickoff,
            league=league,
        )
        mo.selections = list(final_goalscorer.values()) + list(selections_assist.values())
        return mo

    async def scrape_league(self, league: str) -> list[MatchOdds]:
        """Scrape une ligue : liste événements → marchés → MatchOdds."""
        events = await self.fetch_event_ids(league)
        if not events:
            logger.info("UnibetLVSScraper %s: 0 événements", league)
            return []

        logger.info("UnibetLVSScraper %s: %d événements à traiter", league, len(events))
        results: list[MatchOdds] = []

        for event_id, home, away, kickoff in events:
            url = f"{LVS_BASE}/lvs-api/ff/e{event_id}?{_LIST_PARAMS}"
            try:
                r = await self._client.get(url, headers=self._auth_headers(), timeout=15)
                r.raise_for_status()
                items = r.json().get("items", {})
            except Exception as exc:
                logger.debug("UnibetLVSScraper: ff/e%d échoué: %s", event_id, exc)
                await asyncio.sleep(_EVENT_SLEEP)
                continue

            mo = self._parse_match_items(items, league)
            if mo and mo.selections:
                results.append(mo)

            await asyncio.sleep(_EVENT_SLEEP)

        total_sel = sum(len(m.selections) for m in results)
        logger.info(
            "UnibetLVSScraper %s: %d matchs, %d sélections",
            league, len(results), total_sel,
        )
        return results


async def scrape_all_unibet(leagues: list[str]) -> list[MatchOdds]:
    """Scrape les cotes Unibet pour toutes les ligues via l'API LVS.

    Pure HTTP — pas de Playwright requis.
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:
        scraper = UnibetLVSScraper(client)

        try:
            await scraper._get_token()
        except Exception as exc:
            logger.error("UnibetLVSScraper: impossible d'obtenir le token LVS: %s", exc)
            return []

        all_matches: list[MatchOdds] = []
        for league in leagues:
            try:
                matches = await scraper.scrape_league(league)
                all_matches.extend(matches)
            except Exception as exc:
                logger.error(
                    "UnibetLVSScraper: erreur sur %s: %s", league, exc, exc_info=True
                )

    logger.info(
        "scrape_all_unibet: %d match-odds sur %d ligues",
        len(all_matches), len(leagues),
    )
    return all_matches


# ── CLI ─────────────────────────────────────────────────────────────────────


async def _cli_main(args: argparse.Namespace) -> None:
    leagues = [args.league] if args.league else list(LVS_NODE_IDS.keys())
    all_matches = await scrape_all_unibet(leagues)

    total_sel = sum(len(m.selections) for m in all_matches)
    print(f"\n{'=' * 60}")
    print(f"Unibet LVS: {len(all_matches)} matchs, {total_sel} sélections")
    print(f"{'=' * 60}")

    if args.dry_run:
        for m in all_matches:
            print(f"\n  {m.home_team} vs {m.away_team}  [{m.league}]")
            if m.kickoff_utc:
                print(f"  Kickoff: {m.kickoff_utc.strftime('%Y-%m-%d %H:%M UTC')}")
            goal = [s for s in m.selections if s.market_type == "goalscorer"]
            assist = [s for s in m.selections if s.market_type == "assist"]
            print(f"  Buteurs: {len(goal)}  Passeurs: {len(assist)}")
            for s in sorted(goal, key=lambda x: x.odds)[:5]:
                print(f"    [G] {s.player_name}: {s.odds:.2f}")
            for s in sorted(assist, key=lambda x: x.odds)[:5]:
                print(f"    [A] {s.player_name}: {s.odds:.2f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Scrape Unibet LVS odds")
    parser.add_argument("--league", choices=list(LVS_NODE_IDS.keys()), default=None)
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(_cli_main(parser.parse_args()))
