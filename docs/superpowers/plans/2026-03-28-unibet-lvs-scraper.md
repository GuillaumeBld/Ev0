# Unibet LVS Scraper Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le scraper Kambi (mort après fusion Unibet/PSEL) par un nouveau scraper ciblant l'API LVS du nouveau www.unibet.fr, couvrant le Big 5 + Champions League, et récupérant les marchés buteur ET passeur décisif.

**Architecture:** Nouveau fichier `unibet_lvs_scraper.py` qui s'authentifie via token anonyme LVS, liste les matchs par compétition, puis récupère les marchés complets de chaque match. Il expose la même interface de sortie (`MatchOdds` / `SelectionOdds`) que les autres scrapers — zéro changement dans le storage ou la logique de recommandations. L'ancien `kambi_scraper.py` est supprimé et remplacé dans `worker.py`.

**Tech Stack:** Python 3.11, httpx (async), dataclasses existants `MatchOdds`/`SelectionOdds` de `direct_scrapers.py`, pytest + unittest.mock

---

## Contexte technique

### Nouvelle API LVS (www.unibet.fr post-fusion PSEL)

| Étape | Endpoint |
|-------|----------|
| Token anonyme | `GET https://www.unibet.fr/lvs-api/acc/token` → `{"hsToken": "..."}` |
| Liste matchs | `GET https://www.unibet.fr/lvs-api/next/50/p{nodeId}?lineId=1&originId=3&ext=1&showPromotions=true&showMarketTypeGroups=true` |
| Marchés match | `GET https://www.unibet.fr/lvs-api/ff/e{eventId}?lineId=1&originId=3&ext=1&showPromotions=true&showMarketTypeGroups=true` |

Header requis sur tous les appels après le token : `X-LVS-HSToken: <hsToken>`

### Node IDs des compétitions

```python
LVS_NODE_IDS = {
    "ligue_1":          58531576,
    "premier_league":   58532401,
    "bundesliga":       58529612,
    "la_liga":          58532237,
    "serie_a":          58529754,
    "champions_league": 58532497,
}
```

### Format de la réponse `/lvs-api/ff/e{eventId}`

```json
{
  "items": {
    "e3327251": { "a": "PSG", "b": "Marseille", "start": "2604051930" },
    "m182088290": { "parent": "e3327251", "markettypeId": 31, "desc": "Buteur" },
    "o668512864": { "parent": "m182088290", "desc": "Mbappé", "price": "2,75" }
  }
}
```

- Clés préfixées : `e` = événement, `m` = marché, `o` = outcome/cote
- `start` format `YYMMDDHHMM` → ex. `"2604051930"` = 2026-04-05 19:30 UTC
- `price` avec virgule française → remplacer `,` par `.` puis `float()`
- `price` peut être `null` → marché suspendu, ignorer

### Market type IDs ciblés

| markettypeId | Marché | market_type Ev0 |
|---|---|---|
| `31` | Buteur (anytime scorer) | `"goalscorer"` |
| `4` | 1er Buteur | `"goalscorer"` (dédupliqué, priorité moindre que 31) |
| `100002524` | Passeur décisif | `"assist"` |

### Format liste matchs `/lvs-api/next/50/p{nodeId}`

Retourne des items `e{id}` avec `"a"` (équipe domicile), `"b"` (équipe extérieur), `"start"`.
Les event IDs sont numériques — extraire depuis la clé : `"e12345"` → `12345`.

---

## Structure des fichiers

| Fichier | Action | Rôle |
|---------|--------|------|
| `backend/app/ingestion/unibet_lvs_scraper.py` | **Créer** | Nouveau scraper LVS |
| `backend/tests/test_unibet_lvs_scraper.py` | **Créer** | Tests unitaires |
| `backend/app/worker.py` | **Modifier** | Remplacer kambi → unibet_lvs (4 endroits) |
| `backend/app/ingestion/kambi_scraper.py` | **Supprimer** | Obsolète |

---

## Chunk 1 : Scraper LVS

### Task 1 : Tests unitaires du parser LVS

**Files:**
- Create: `backend/tests/test_unibet_lvs_scraper.py`

- [ ] **Step 1 : Écrire les tests**

```python
# backend/tests/test_unibet_lvs_scraper.py
"""Tests for Unibet LVS scraper (new post-PSEL-merger site)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ingestion.unibet_lvs_scraper import (
    UnibetLVSScraper,
    _parse_price,
    _parse_start,
)


class TestParsePrice:
    def test_french_decimal(self):
        assert _parse_price("3,05") == pytest.approx(3.05)

    def test_integer_string(self):
        assert _parse_price("2") == pytest.approx(2.0)

    def test_none_returns_none(self):
        assert _parse_price(None) is None

    def test_null_string_returns_none(self):
        assert _parse_price("null") is None

    def test_below_one_returns_none(self):
        assert _parse_price("0,95") is None

    def test_above_max_returns_none(self):
        assert _parse_price("1001") is None

    def test_empty_string_returns_none(self):
        assert _parse_price("") is None


class TestParseStart:
    def test_valid_format(self):
        # "2604051930" → 2026-04-05 19:30 UTC
        dt = _parse_start("2604051930")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 4
        assert dt.day == 5
        assert dt.hour == 19
        assert dt.minute == 30

    def test_invalid_returns_none(self):
        assert _parse_start("") is None
        assert _parse_start(None) is None
        assert _parse_start("abc") is None

    def test_non_numeric_ten_chars_returns_none(self):
        # 10 chars but non-numeric → ValueError branch
        assert _parse_start("260405193X") is None

    def test_short_string_returns_none(self):
        assert _parse_start("26040519") is None  # 8 chars


class TestParseMatchItems:
    """Tests for _parse_match_items: extracts MatchOdds from LVS /ff/ response."""

    SAMPLE_ITEMS = {
        "e100": {"a": "PSG", "b": "Marseille", "start": "2604051930"},
        # markettypeId=31 : buteur anytime
        "m200": {"parent": "e100", "markettypeId": 31, "desc": "Buteur"},
        # markettypeId=100002524 : passeur décisif
        "m201": {"parent": "e100", "markettypeId": 100002524, "desc": "Passeur décisif"},
        # markettypeId=4 : 1er buteur (doit être dédupliqué au profit de l'anytime)
        "m202": {"parent": "e100", "markettypeId": 4, "desc": "1er Buteur"},
        # Outcomes buteur anytime
        "o300": {"parent": "m200", "desc": "Mbappé", "price": "2,75"},
        "o301": {"parent": "m200", "desc": "Neymar", "price": "3,50"},
        # Outcome passeur décisif
        "o302": {"parent": "m201", "desc": "Mbappé", "price": "3,20"},
        # Outcome 1er buteur pour Mbappé — doit être ignoré au profit de o300 (anytime)
        "o303": {"parent": "m202", "desc": "Mbappé", "price": "4,00"},
        # Outcome suspendu (price=None) — doit être ignoré
        "o304": {"parent": "m200", "desc": "Giroud", "price": None},
    }

    def _scraper(self):
        return UnibetLVSScraper(httpx.AsyncClient())

    def test_extracts_goalscorer_selections(self):
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        assert mo is not None
        goal = [s for s in mo.selections if s.market_type == "goalscorer"]
        names = [s.player_name for s in goal]
        assert "Mbappé" in names
        assert "Neymar" in names

    def test_extracts_assist_selections(self):
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        assist = [s for s in mo.selections if s.market_type == "assist"]
        assert len(assist) == 1
        assert assist[0].player_name == "Mbappé"
        assert assist[0].odds == pytest.approx(3.20)

    def test_deduplicates_anytime_over_first_scorer(self):
        """Anytime (markettypeId=31) bat toujours First Scorer (markettypeId=4)."""
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        mbappe_goals = [
            s for s in mo.selections
            if s.market_type == "goalscorer" and s.player_name == "Mbappé"
        ]
        # Un seul Mbappé goalscorer, avec les cotes anytime (2.75) pas first scorer (4.00)
        assert len(mbappe_goals) == 1
        assert mbappe_goals[0].odds == pytest.approx(2.75)

    def test_skips_null_price(self):
        """Outcomes avec price=None (marché suspendu) sont ignorés."""
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        # Giroud a price=None → ne doit pas apparaître
        goal_names = [s.player_name for s in mo.selections if s.market_type == "goalscorer"]
        assert "Giroud" not in goal_names
        # Exactement 2 buteurs (Mbappé + Neymar)
        assert len([s for s in mo.selections if s.market_type == "goalscorer"]) == 2

    def test_correct_teams_and_league(self):
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        assert mo.home_team == "PSG"
        assert mo.away_team == "Marseille"
        assert mo.league == "ligue_1"

    def test_returns_none_when_no_event(self):
        assert self._scraper()._parse_match_items({}, "ligue_1") is None

    def test_returns_none_when_no_target_markets(self):
        items = {
            "e100": {"a": "PSG", "b": "Marseille", "start": "2604051930"},
            # Seul un marché 1X2 (non ciblé)
            "m200": {"parent": "e100", "markettypeId": 1, "desc": "1X2"},
            "o300": {"parent": "m200", "desc": "PSG", "price": "1,80"},
        }
        assert self._scraper()._parse_match_items(items, "ligue_1") is None

    def test_bookmaker_is_unibet(self):
        mo = self._scraper()._parse_match_items(self.SAMPLE_ITEMS, "ligue_1")
        assert all(s.bookmaker == "unibet" for s in mo.selections)


class TestFetchEventIds:
    """Tests for fetch_event_ids: appel /next/ et filtrage des matchs passés."""

    SAMPLE_NEXT_RESPONSE = {
        "items": {
            "e1001": {"a": "PSG", "b": "Lyon", "start": "2612311930"},      # futur
            "e1002": {"a": "Monaco", "b": "Nice", "start": "2001011200"},   # passé (2020)
            "e1003": {"a": "Lens", "b": "Lille", "start": "2612251500"},    # futur
            "m9999": {"parent": "e1001", "markettypeId": 31},               # marché ignoré
        }
    }

    @pytest.mark.asyncio
    async def test_returns_only_future_events(self):
        scraper = UnibetLVSScraper(httpx.AsyncClient())
        scraper._token = "fake-token"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = self.SAMPLE_NEXT_RESPONSE

        with patch.object(scraper._client, "get", new=AsyncMock(return_value=mock_response)):
            events = await scraper.fetch_event_ids("ligue_1")

        # Monaco vs Nice (passé) doit être exclu
        event_ids = [e[0] for e in events]
        assert 1001 in event_ids
        assert 1003 in event_ids
        assert 1002 not in event_ids

    @pytest.mark.asyncio
    async def test_ignores_non_event_items(self):
        scraper = UnibetLVSScraper(httpx.AsyncClient())
        scraper._token = "fake-token"

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = self.SAMPLE_NEXT_RESPONSE

        with patch.object(scraper._client, "get", new=AsyncMock(return_value=mock_response)):
            events = await scraper.fetch_event_ids("ligue_1")

        # m9999 ne doit pas apparaître comme événement
        event_ids = [e[0] for e in events]
        assert 9999 not in event_ids

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        scraper = UnibetLVSScraper(httpx.AsyncClient())
        scraper._token = "fake-token"

        with patch.object(
            scraper._client, "get", new=AsyncMock(side_effect=httpx.ConnectError("refused"))
        ):
            events = await scraper.fetch_event_ids("ligue_1")

        assert events == []

    @pytest.mark.asyncio
    async def test_unknown_league_returns_empty(self):
        scraper = UnibetLVSScraper(httpx.AsyncClient())
        events = await scraper.fetch_event_ids("ligue_inconnue")
        assert events == []
```

- [ ] **Step 2 : Vérifier que les tests échouent (module n'existe pas)**

```bash
cd /Users/yohan.resin/Ev0/.worktrees/feature-unibet-lvs/backend
uv run pytest tests/test_unibet_lvs_scraper.py -v 2>&1 | head -20
```

Attendu : `ModuleNotFoundError: No module named 'app.ingestion.unibet_lvs_scraper'`

---

### Task 2 : Implémenter `unibet_lvs_scraper.py`

**Files:**
- Create: `backend/app/ingestion/unibet_lvs_scraper.py`

- [ ] **Step 1 : Créer le fichier**

```python
# backend/app/ingestion/unibet_lvs_scraper.py
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
        event_key = next((k for k in items if k.startswith("e")), None)
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

        # Séparer les marchés anytime goalscorer des autres pour la déduplication
        anytime_market_keys = {
            k for k, (mid, _) in target_markets.items() if mid == _ANYTIME_SCORER_ID
        }

        # Collecter les selections
        # Pour goalscorer : anytime (31) a priorité absolue sur first scorer (4)
        # On fait deux passes : d'abord les anytime, puis les first scorer (comble les trous)
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
```

- [ ] **Step 2 : Lancer les tests**

```bash
cd /Users/yohan.resin/Ev0/.worktrees/feature-unibet-lvs/backend
uv run pytest tests/test_unibet_lvs_scraper.py -v
```

Attendu : tous les tests PASS

- [ ] **Step 3 : Commit**

```bash
git add backend/app/ingestion/unibet_lvs_scraper.py backend/tests/test_unibet_lvs_scraper.py
git commit -m "feat: scraper Unibet LVS (nouveau site post-fusion PSEL)

- Remplace kambi_scraper.py (API Kambi abandonnée pour la France)
- Plateforme LVS (Lineup7/SportEase) sur www.unibet.fr
- Token anonyme, pure HTTP, pas de Playwright
- Big 5 + Champions League
- Marchés : buteur anytime + 1er buteur (dédupliqué) + passeur décisif"
```

---

## Chunk 2 : Intégration dans le worker

### Task 3 : Mettre à jour `worker.py` et supprimer `kambi_scraper.py`

Il y a **4 références Kambi** à mettre à jour dans `worker.py`.

**Files:**
- Modify: `backend/app/worker.py`
- Delete: `backend/app/ingestion/kambi_scraper.py`

- [ ] **Step 1 : Remplacer le bloc d'appel Kambi (lignes ~563-571)**

Trouver :
```python
    # ── 1. Kambi HTTP scraper (Unibet — pure HTTP, no Playwright needed) ──
    try:
        from app.ingestion.kambi_scraper import scrape_all_kambi

        kambi_results = await scrape_all_kambi(leagues)
        all_match_odds.extend(kambi_results)
        logger.info("Kambi scraper: %d match-odds objects", len(kambi_results))
    except Exception as exc:
        logger.error("Kambi scrape failed: %s", exc, exc_info=True)
```

Remplacer par :
```python
    # ── 1. Unibet LVS scraper (nouveau site post-fusion PSEL, pure HTTP) ──
    try:
        from app.ingestion.unibet_lvs_scraper import scrape_all_unibet

        unibet_results = await scrape_all_unibet(leagues)
        all_match_odds.extend(unibet_results)
        logger.info("Unibet LVS scraper: %d match-odds objects", len(unibet_results))
    except Exception as exc:
        logger.error("Unibet LVS scrape failed: %s", exc, exc_info=True)
```

- [ ] **Step 2 : Mettre à jour la docstring de `job_snapshot_direct_odds`**

Trouver :
```python
    """Snapshot odds from Kambi (Unibet) HTTP API + Playwright scrapers.

    Flow:
    1. Kambi HTTP API (Unibet) — no browser needed, always runs first
```

Remplacer par :
```python
    """Snapshot odds from Unibet LVS HTTP API + Playwright scrapers.

    Flow:
    1. Unibet LVS API (nouveau site post-fusion PSEL) — no browser needed
```

- [ ] **Step 3 : Mettre à jour le nom du job dans le scheduler (ligne ~1563)**

Trouver :
```python
        name="Snapshot direct odds (Kambi, Betclic, ParionsSport)",
```

Remplacer par :
```python
        name="Snapshot direct odds (Unibet LVS, Betclic, ParionsSport)",
```

- [ ] **Step 4 : Mettre à jour le log de fallback (ligne ~704)**

Chercher dans `worker.py` la ligne contenant `"Kambi odds"` ou `"direct/Kambi"` :
```python
grep -n "Kambi" backend/app/worker.py
```

Remplacer toute occurrence `Kambi` restante par `Unibet LVS`.

- [ ] **Step 5 : Supprimer `kambi_scraper.py`**

```bash
git rm backend/app/ingestion/kambi_scraper.py
```

- [ ] **Step 6 : Vérifier l'absence de références Kambi résiduelles**

```bash
grep -rn "kambi" /Users/yohan.resin/Ev0/.worktrees/feature-unibet-lvs/backend --include="*.py"
```

Attendu : 0 résultats

- [ ] **Step 7 : Lancer les tests complets**

```bash
cd /Users/yohan.resin/Ev0/.worktrees/feature-unibet-lvs/backend
uv run pytest tests/ -x -q
```

Attendu : tous les tests PASS

- [ ] **Step 8 : Commit**

```bash
git add backend/app/worker.py
git commit -m "chore: remplacer kambi_scraper par unibet_lvs_scraper dans worker"
```

---

## Chunk 3 : Validation live (optionnel, sur VPS ou local)

### Task 4 : Test dry-run en conditions réelles

> ⚠️ Ce test effectue de vraies requêtes HTTP vers www.unibet.fr. À lancer de préférence 2-5 jours avant un match pour avoir des marchés ouverts.

- [ ] **Step 1 : Dry-run local sur Ligue 1**

```bash
cd /Users/yohan.resin/Ev0/.worktrees/feature-unibet-lvs/backend
uv run python -m app.ingestion.unibet_lvs_scraper --league ligue_1 --dry-run
```

Attendu si matchs à venir :
- `[G] Joueur: 2.75` et `[A] Joueur: 3.20`
- Aucune exception

- [ ] **Step 2 : Dry-run toutes les ligues**

```bash
uv run python -m app.ingestion.unibet_lvs_scraper --dry-run
```

Attendu : Big 5 + CL listés, sélections pour les matchs à venir

---

## Intégration finale

- [ ] **Merger sur main**

```bash
cd /Users/yohan.resin/Ev0
git merge feature/unibet-lvs
git push origin main
```

- [ ] **Déployer sur le VPS**

```bash
ssh root@213.130.144.204 "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && git pull origin main && docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker"
```

- [ ] **Vérifier les logs après le premier job**

```bash
ssh root@213.130.144.204 "docker logs ev0-compose-z5hvqt-worker-1 2>&1 | grep -i 'unibet\|lvs\|direct odds' | tail -20"
```
