"""Unibet LVS scraper — nouveau site post-fusion PSEL (mars 2026).

La plateforme Kambi (eu-offering-api.kambicdn.com) a été abandonnée.
Le nouveau www.unibet.fr tourne sur la plateforme LVS (Lineup7/SportEase).

API publique, token anonyme sans compte requis.

Marchés extraits :
- markettypeId=1         : 1X2 (résultat du match)   → h2h
- markettypeId=994001025 : Plus / Moins X.5 But(s)   → totals (un marché par seuil)
- markettypeId=350       : Les 2 équipes marqueront?  → btts
- markettypeId=100002264 : Buteur ou son remplaçant   → goalscorer AVEC sub (prioritaire)
- markettypeId=31        : Buteur                     → goalscorer sans sub (second recours)
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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.ingestion.scrape_result import (
    AVEC_SUB,
    SANS_SUB,
    MatchScrapeResult,
    PlayerOdds,
    prefer_avec_sub,
)

logger = logging.getLogger(__name__)

LVS_BASE = "https://www.unibet.fr"
BOOKMAKER = "unibet"

LVS_NODE_IDS: dict[str, int] = {
    "ligue_1":               58531576,
    "premier_league":        58532401,
    "bundesliga":            58529612,
    "la_liga":               58532237,
    "serie_a":               58529754,
    "champions_league":      58532497,
    "friendly_international": 58541367,
    "world_cup_2026":        59096156,
    "uefa_super_cup":        58545066,  # Supercoupe d'Europe (Coupes d'Europe) — buteur + passes décisives
}

# markettypeId → market_type Ev0
_MARKET_TYPES: dict[int, str] = {
    # Match-level (confirmed by audit)
    1:          "h2h",        # 1 N 2 — résultat du match (draw outcome = "N")
    994001025:  "totals",     # Plus / Moins X.5 But(s) — over/under per threshold
    350:        "btts",       # Les 2 équipes marqueront-elles?
    # Player props
    100002264: "goalscorer",   # Coaching gagnant : Buteur ou son remplaçant marque (AVEC sub)
    31:        "goalscorer",   # Buteur (sans sub — second recours)
    100001899: "assist",       # Nbre de passes décisives (outcomes: "Nom 1+", "Nom 2+")
}

# Règle du 23/11/2026 : la cote avec sub d'abord, la cote sans sub seulement
# pour un joueur que le marché avec sub ne propose pas. Unibet n'a pas de
# passeur avec sub : ses cotes passeur sont toujours « sans sub ».
#
# Le « 1er Buteur » (markettypeId=4) servait de repli quand le buteur manquait :
# retiré, ce n'est pas le même pari (Cherki 6,10 en 1er buteur, 2,20 en buteur)
# et il fabriquait de fausses values.
_GOALSCORER_VARIANTS: dict[int, str] = {100002264: AVEC_SUB, 31: SANS_SUB}

_EVENT_SLEEP = 0.3

# Unibet refuse par moments la LISTE des matchs d'un championnat (403, environ
# 4 demandes sur 10 le 24/11/2026) alors que la lecture d'un match précis
# passe. Deux reprises espacées avant d'abandonner la liste pour ce passage.
_LIST_RETRY_DELAYS = (3.0, 10.0)

# Un match de la liste est lu s'il démarre à moins de ça d'un match attendu.
_KICKOFF_WINDOW = timedelta(hours=3)


@dataclass
class UnibetReport:
    """Ce qui a raté pendant un passage, pour que le planificateur réessaie
    vite au lieu d'attendre le prochain passage normal (jusqu'à 2 h)."""
    failed_leagues: set[str] = field(default_factory=set)   # liste refusée, reprises comprises
    failed_events: set[int] = field(default_factory=set)    # lecture d'un match en erreur
    gone_events: set[int] = field(default_factory=set)      # match disparu chez Unibet

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
        return await self._fetch_event_list(league) or []

    async def _fetch_event_list(
        self, league: str,
    ) -> list[tuple[int, str, str, datetime | None]] | None:
        """Comme fetch_event_ids, mais None quand Unibet a refusé la liste
        (reprises comprises) : « liste refusée » n'est pas « aucun match »."""
        node_id = LVS_NODE_IDS.get(league)
        if not node_id:
            logger.warning("UnibetLVSScraper: ligue inconnue %s", league)
            return []

        url = f"{LVS_BASE}/lvs-api/next/50/p{node_id}?{_LIST_PARAMS}"
        r = None
        for attempt, delay in enumerate((0.0, *_LIST_RETRY_DELAYS)):
            if delay:
                await asyncio.sleep(delay)
            try:
                r = await self._client.get(url, headers=self._auth_headers(), timeout=15)
                r.raise_for_status()
                break
            except Exception as exc:
                logger.warning(
                    "UnibetLVSScraper: liste %s refusée (essai %d/%d): %s",
                    league, attempt + 1, len(_LIST_RETRY_DELAYS) + 1, exc,
                )
                r = None
        if r is None:
            return None

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
        selections_goalscorer: list[PlayerOdds] = []
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
                selections_goalscorer.append(
                    PlayerOdds(desc, odds, _GOALSCORER_VARIANTS[mtype_id])
                )
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

        # Une cote buteur par joueur : avec sub si elle existe, sinon sans sub.
        final_goalscorer = prefer_avec_sub(selections_goalscorer)
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

    async def _scrape_event(
        self, event_id: int, league: str, label: str, report: UnibetReport | None = None,
    ) -> MatchScrapeResult | None:
        """Lit un match par son identifiant Unibet. None si rien d'exploitable ;
        la cause (erreur passagère ou match disparu) est notée dans `report`."""
        url = f"{LVS_BASE}/lvs-api/ff/e{event_id}?{_LIST_PARAMS}"
        try:
            r = await self._client.get(url, headers=self._auth_headers(), timeout=15)
            if r.status_code == 404:
                if report is not None:
                    report.gone_events.add(event_id)
                logger.warning("UnibetLVSScraper: match e%d introuvable (%s)", event_id, label)
                return None
            r.raise_for_status()
            items = r.json().get("items", {})
        except Exception as exc:
            if report is not None:
                report.failed_events.add(event_id)
            logger.warning("UnibetLVSScraper: ff/e%d échoué (%s): %s", event_id, label, exc)
            return None

        result = self._parse_match_items(items, league)
        if result is None:
            # Réponse sans le match : il n'existe plus sous cet identifiant.
            if report is not None:
                report.gone_events.add(event_id)
            return None
        result.book_event_id = event_id
        if not (result.h2h or result.totals or result.btts or result.goalscorer or result.assist):
            return None
        if not result.assist:
            logger.warning(
                "UnibetLVSScraper: 0 assist odds for %s vs %s [%s] — "
                "markettypeId=100001899 absent from LVS response",
                result.home_team, result.away_team, league,
            )
        return result

    async def scrape_league(
        self,
        league: str,
        wanted_kickoffs: list[datetime] | None = None,
        report: UnibetReport | None = None,
    ) -> list[MatchScrapeResult]:
        """Scrape une ligue : liste événements → marchés → MatchScrapeResult.

        `wanted_kickoffs` : ne lit que les matchs qui démarrent à moins de 3 h
        d'un de ces horaires (None = tous). Lire les 18 matchs d'un championnat
        pour en rafraîchir un seul, c'était 17 demandes inutiles à Unibet.
        """
        events = await self._fetch_event_list(league)
        if events is None:
            if report is not None:
                report.failed_leagues.add(league)
            logger.warning("UnibetLVSScraper %s: liste indisponible ce passage", league)
            return []
        if wanted_kickoffs is not None:
            events = [
                e for e in events
                if e[3] is None
                or any(abs(e[3] - ko) <= _KICKOFF_WINDOW for ko in wanted_kickoffs)
            ]
        if not events:
            logger.info("UnibetLVSScraper %s: 0 événements", league)
            return []

        logger.info("UnibetLVSScraper %s: %d événements à traiter", league, len(events))
        results: list[MatchScrapeResult] = []

        for event_id, home, away, _kickoff in events:
            result = await self._scrape_event(event_id, league, f"{home} vs {away}", report)
            if result:
                results.append(result)
            await asyncio.sleep(_EVENT_SLEEP)

        total_sel = sum(len(r.goalscorer) for r in results)
        logger.info(
            "UnibetLVSScraper %s: %d matchs, %d sélections buteur",
            league, len(results), total_sel,
        )
        return results

    async def scrape_known_events(
        self,
        known_events: dict[int, tuple[int, str]],
        report: UnibetReport | None = None,
    ) -> list[MatchScrapeResult]:
        """Relit des matchs déjà accrochés : {identifiant Unibet: (fixture_id, ligue)}.

        Aucune liste demandée, aucun nom comparé : le résultat porte
        directement le fixture_id.
        """
        results: list[MatchScrapeResult] = []
        for event_id, (fixture_id, league) in known_events.items():
            result = await self._scrape_event(event_id, league, f"fixture {fixture_id}", report)
            if result:
                result.fixture_id = fixture_id
                results.append(result)
            await asyncio.sleep(_EVENT_SLEEP)
        if known_events:
            logger.info(
                "UnibetLVSScraper: %d/%d matchs relus par identifiant",
                len(results), len(known_events),
            )
        return results


async def scrape_all_unibet(
    leagues: list[str],
    *,
    known_events: dict[int, tuple[int, str]] | None = None,
    wanted_kickoffs: dict[str, list[datetime]] | None = None,
    report: UnibetReport | None = None,
) -> list[MatchScrapeResult]:
    """Scrape les cotes Unibet via l'API LVS.

    - `known_events` : matchs déjà accrochés, relus par identifiant.
    - `leagues` : championnats où chercher les matchs pas encore accrochés ;
      `wanted_kickoffs[ligue]` limite la lecture aux matchs attendus.
    - `report` : rempli avec ce qui a raté, pour une reprise rapide.
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:
        scraper = UnibetLVSScraper(client)

        try:
            await scraper._get_token()
        except Exception as exc:
            logger.error("UnibetLVSScraper: impossible d'obtenir le token LVS: %s", exc)
            if report is not None:
                report.failed_leagues.update(leagues)
                report.failed_events.update(known_events or {})
            return []

        all_results: list[MatchScrapeResult] = []
        if known_events:
            all_results.extend(await scraper.scrape_known_events(known_events, report))
        for league in leagues:
            try:
                results = await scraper.scrape_league(
                    league,
                    wanted_kickoffs.get(league) if wanted_kickoffs is not None else None,
                    report,
                )
                all_results.extend(results)
            except Exception as exc:
                if report is not None:
                    report.failed_leagues.add(league)
                logger.error(
                    "UnibetLVSScraper: erreur sur %s: %s", league, exc, exc_info=True
                )

    logger.info(
        "scrape_all_unibet: %d matchs (%d par identifiant, %d ligues)",
        len(all_results), len(known_events or {}), len(leagues),
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
