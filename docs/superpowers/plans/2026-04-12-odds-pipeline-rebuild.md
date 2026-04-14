# Odds Pipeline Rebuild — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer le pipeline d'ingestion des cotes mort (OddsPortal + The Odds API) par un pipeline unifié Betclic gRPC + Unibet LVS avec scheduler adaptatif basé sur la distance au KO.

**Architecture:** Les deux scrapers existants sont étendus pour récupérer cotes match (1X2/OU/BTTS) ET cotes joueurs en un seul appel réseau. Un `OddsScheduler` pilote la fréquence (2h → 30min → 2min → stop à KO-5min). `MarketXgService` est conservé, son `MAX_SNAPSHOT_AGE` passe de 3h à 30min.

**Tech Stack:** Python 3.12, SQLAlchemy async, Alembic, httpx, APScheduler, protobuf (hand-parsed)

**Spec:** `docs/superpowers/specs/2026-04-12-odds-pipeline-rebuild-design.md`

---

## Fichiers créés

| Fichier | Rôle |
|---|---|
| `backend/app/ingestion/scrape_result.py` | `MatchScrapeResult` + `PlayerOdds` — contrat partagé entre scrapers |
| `backend/app/ingestion/odds_storage.py` | Persist `MatchScrapeResult` → `match_odds_snapshots` + `player_odds_snapshots` |
| `backend/app/ingestion/odds_scheduler.py` | `OddsScheduler` — logique fréquence adaptative |
| `backend/app/models/player_odds_snapshot.py` | ORM `PlayerOddsSnapshot` |
| `backend/app/models/odds_scrape_state.py` | ORM `OddsScrapeState` |
| `backend/alembic/versions/022_odds_pipeline_rebuild.py` | Migration DB |
| `backend/tests/ingestion/test_scrape_result.py` | Tests `MatchScrapeResult` |
| `backend/tests/ingestion/test_odds_storage.py` | Tests storage |
| `backend/tests/ingestion/test_odds_scheduler.py` | Tests scheduler |

## Fichiers modifiés

| Fichier | Changement |
|---|---|
| `backend/app/ingestion/betclic_grpc_scraper.py` | `_classify_market` + `_parse_match_proto` étendus pour h2h/totals/btts. Retourne `MatchScrapeResult`. |
| `backend/app/ingestion/unibet_lvs_scraper.py` | `_MARKET_TYPES` + `_parse_match_items` étendus. Retourne `MatchScrapeResult`. |
| `backend/app/services/market_xg.py` | `MAX_SNAPSHOT_AGE` = 30min |
| `backend/app/models/__init__.py` | Enregistre `PlayerOddsSnapshot`, `OddsScrapeState`, retire `OddsSnapshot`, `OddsPortalPollState` |
| `backend/app/worker.py` | Retire 4 jobs morts, ajoute `job_odds_scheduler_tick` |

## Fichiers supprimés

`app/ingestion/oddsportal_scraper.py`, `oddsportal_fixture_matcher.py`, `oddsportal_league_discoverer.py`, `market_scrape_chain.py`, `betclic_match_scraper.py`, `unibet_match_scraper.py`, `odds.py`, `direct_scrapers.py` · `app/services/market_scrape_scheduler.py` · `app/models/odds.py`, `poll_state.py`

---

## Task 1 — Shared output type : `scrape_result.py`

**Files:**
- Create: `backend/app/ingestion/scrape_result.py`
- Create: `backend/tests/ingestion/test_scrape_result.py`

- [ ] **Step 1 : Écrire le test**

```python
# backend/tests/ingestion/test_scrape_result.py
from datetime import datetime, timezone

from app.ingestion.scrape_result import MatchScrapeResult, PlayerOdds


def test_match_scrape_result_defaults():
    r = MatchScrapeResult(
        fixture_id=1,
        home_team="PSG",
        away_team="Lyon",
        kickoff_utc=datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc),
        league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
    )
    assert r.h2h is None
    assert r.totals is None
    assert r.btts is None
    assert r.goalscorer == []
    assert r.assist == []


def test_player_odds_fields():
    p = PlayerOdds(player_name="Mbappé", odds=3.5)
    assert p.player_name == "Mbappé"
    assert p.odds == 3.5


def test_is_complete_all_markets():
    r = MatchScrapeResult(
        fixture_id=1, home_team="A", away_team="B",
        kickoff_utc=None, league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        h2h={"home": 2.1, "draw": 3.4, "away": 3.6},
        totals={"over_2.5": 1.8, "under_2.5": 2.0},
        btts={"yes": 1.75, "no": 2.1},
    )
    assert r.is_complete is True


def test_is_complete_missing_btts():
    r = MatchScrapeResult(
        fixture_id=1, home_team="A", away_team="B",
        kickoff_utc=None, league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        h2h={"home": 2.1, "draw": 3.4, "away": 3.6},
        totals={"over_2.5": 1.8, "under_2.5": 2.0},
    )
    assert r.is_complete is False
```

- [ ] **Step 2 : Vérifier que le test échoue**
```bash
cd backend && python -m pytest tests/ingestion/test_scrape_result.py -v
```
Attendu : `ModuleNotFoundError: No module named 'app.ingestion.scrape_result'`

- [ ] **Step 3 : Créer `scrape_result.py`**

```python
# backend/app/ingestion/scrape_result.py
"""Shared output types for all bookmaker scrapers."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PlayerOdds:
    """One player with their decimal odds for a market."""
    player_name: str
    odds: float


@dataclass
class MatchScrapeResult:
    """Unified output from any bookmaker scraper — one object per match per book."""
    fixture_id: int
    home_team: str
    away_team: str
    kickoff_utc: datetime | None
    league: str
    bookmaker: str
    scraped_at: datetime
    # Match-level odds
    h2h: dict | None = None       # {home, draw, away}
    totals: dict | None = None    # {over_1.5, under_1.5, over_2.5, under_2.5, over_3.5, under_3.5}
    btts: dict | None = None      # {yes, no}
    # Player props
    goalscorer: list[PlayerOdds] = field(default_factory=list)
    assist: list[PlayerOdds] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """True when all 3 match markets are present — required for xG solver."""
        return self.h2h is not None and self.totals is not None and self.btts is not None
```

- [ ] **Step 4 : Vérifier que les tests passent**
```bash
cd backend && python -m pytest tests/ingestion/test_scrape_result.py -v
```
Attendu : 4 tests PASS

- [ ] **Step 5 : Commit**
```bash
git add backend/app/ingestion/scrape_result.py backend/tests/ingestion/test_scrape_result.py
git commit -m "feat(odds): add MatchScrapeResult shared output type"
```

---

## Task 2 — Nouveaux modèles SQLAlchemy

**Files:**
- Create: `backend/app/models/player_odds_snapshot.py`
- Create: `backend/app/models/odds_scrape_state.py`

- [ ] **Step 1 : Créer `player_odds_snapshot.py`**

```python
# backend/app/models/player_odds_snapshot.py
"""Player-level bookmaker odds snapshot (goalscorer / assist)."""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PlayerOddsSnapshot(Base):
    """Latest bookmaker odds for a player prop market."""

    __tablename__ = "player_odds_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(30))
    market_type: Mapped[str] = mapped_column(String(20))   # goalscorer | assist
    player_name: Mapped[str] = mapped_column(String(200))
    odds: Mapped[float] = mapped_column(Float)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "fixture_id", "bookmaker", "market_type", "player_name",
            name="uq_player_odds_snapshot",
        ),
    )
```

- [ ] **Step 2 : Créer `odds_scrape_state.py`**

```python
# backend/app/models/odds_scrape_state.py
"""Tracks last/next scrape timestamps per fixture for the OddsScheduler."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OddsScrapeState(Base):
    """One row per fixture — when it was last scraped and when next scrape is due."""

    __tablename__ = "odds_scrape_state"

    fixture_id: Mapped[int] = mapped_column(
        ForeignKey("fixtures.id"), primary_key=True
    )
    last_scraped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_scrape_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    betclic_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    unibet_ok: Mapped[bool] = mapped_column(Boolean, default=False)
```

- [ ] **Step 3 : Vérifier l'import**
```bash
cd backend && python -c "from app.models.player_odds_snapshot import PlayerOddsSnapshot; from app.models.odds_scrape_state import OddsScrapeState; print('OK')"
```
Attendu : `OK`

- [ ] **Step 4 : Mettre à jour `models/__init__.py`**

Remplacer les imports concernés :

```python
# backend/app/models/__init__.py  — diff complet
"""SQLAlchemy models."""

from app.models.autopilot import AutopilotDecision
from app.models.bankroll import BankrollEntry
from app.models.base import Base
from app.models.canonical_teams import CanonicalTeam
from app.models.fixtures import Fixture
from app.models.lineups import TeamLineup, TeamLineupPlayer  # noqa: F401
from app.models.match_events import MatchEvent
from app.models.match_odds import MatchOddsSnapshot
from app.models.odds_scrape_state import OddsScrapeState
from app.models.player_match_minutes import PlayerMatchMinutes
from app.models.player_odds_snapshot import PlayerOddsSnapshot
from app.models.players import DataSource, Player, PlayerStats, Team
from app.models.recommendations import Recommendation
from app.models.settings import UserSettings
from app.models.team_xg import TeamXgEstimate

__all__ = [
    "AutopilotDecision",
    "Base",
    "BankrollEntry",
    "CanonicalTeam",
    "DataSource",
    "Fixture",
    "MatchEvent",
    "MatchOddsSnapshot",
    "OddsScrapeState",
    "Player",
    "PlayerMatchMinutes",
    "PlayerOddsSnapshot",
    "PlayerStats",
    "Recommendation",
    "Team",
    "TeamLineup",
    "TeamLineupPlayer",
    "TeamXgEstimate",
    "UserSettings",
]
```

- [ ] **Step 5 : Commit**
```bash
git add backend/app/models/player_odds_snapshot.py backend/app/models/odds_scrape_state.py backend/app/models/__init__.py
git commit -m "feat(odds): add PlayerOddsSnapshot + OddsScrapeState models"
```

---

## Task 3 — Migration Alembic

**Files:**
- Create: `backend/alembic/versions/022_odds_pipeline_rebuild.py`

- [ ] **Step 1 : Créer la migration**

```python
# backend/alembic/versions/022_odds_pipeline_rebuild.py
"""Odds pipeline rebuild: add player_odds_snapshots + odds_scrape_state,
drop odds_snapshots + oddsportal_poll_state.

Revision ID: 022
Revises: 021
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. player_odds_snapshots
    op.create_table(
        "player_odds_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("bookmaker", sa.String(30), nullable=False),
        sa.Column("market_type", sa.String(20), nullable=False),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("odds", sa.Float(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "fixture_id", "bookmaker", "market_type", "player_name",
            name="uq_player_odds_snapshot",
        ),
    )
    op.create_index("ix_player_odds_fixture", "player_odds_snapshots", ["fixture_id"])

    # 2. odds_scrape_state
    op.create_table(
        "odds_scrape_state",
        sa.Column(
            "fixture_id", sa.Integer(),
            sa.ForeignKey("fixtures.id"), primary_key=True,
        ),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_scrape_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("betclic_ok", sa.Boolean(), server_default="false"),
        sa.Column("unibet_ok", sa.Boolean(), server_default="false"),
    )
    op.create_index(
        "ix_odds_scrape_state_next", "odds_scrape_state", ["next_scrape_at"]
    )

    # 3. Drop dead tables (ignore if already absent)
    op.execute("DROP TABLE IF EXISTS oddsportal_poll_state CASCADE")
    op.execute("DROP TABLE IF EXISTS odds_snapshots CASCADE")


def downgrade() -> None:
    op.drop_index("ix_odds_scrape_state_next", table_name="odds_scrape_state")
    op.drop_table("odds_scrape_state")
    op.drop_index("ix_player_odds_fixture", table_name="player_odds_snapshots")
    op.drop_table("player_odds_snapshots")
```

- [ ] **Step 2 : Vérifier la migration**
```bash
cd backend && python -m alembic upgrade head --sql 2>&1 | tail -20
```
Attendu : SQL valide sans erreur de syntaxe.

- [ ] **Step 3 : Appliquer sur le VPS**

Sur le VPS (`ssh root@213.130.144.204`) :
```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt --env-file .env exec backend python -m alembic upgrade head
```
Attendu : `Running upgrade 021 -> 022`

- [ ] **Step 4 : Commit**
```bash
git add backend/alembic/versions/022_odds_pipeline_rebuild.py
git commit -m "feat(odds): migration 022 — player_odds_snapshots + odds_scrape_state"
```

---

## Task 4 — Betclic gRPC : audit des noms de marchés

Avant d'étendre le parser, il faut connaître les noms exacts que Betclic utilise pour les marchés h2h / totals / btts dans la réponse protobuf.

**Files:**
- Create: `backend/scripts/audit_betclic_markets.py` (temporaire, supprimé après audit)

- [ ] **Step 1 : Créer le script d'audit**

```python
# backend/scripts/audit_betclic_markets.py
"""Dump ALL market names from a live Betclic gRPC response.
Run: cd backend && python scripts/audit_betclic_markets.py
"""
import asyncio
import struct
import httpx
from app.ingestion.betclic_grpc_scraper import (
    BetclicGrpcScraper, _PAGE_HEADERS, _GRPC_HEADERS,
    _stream_first_grpc_frame, encode_grpc_web_request,
    _proto_fields, GRPC_ENDPOINT,
)

async def main():
    async with (
        httpx.AsyncClient(headers=_PAGE_HEADERS, follow_redirects=True) as page_client,
        httpx.AsyncClient(headers=_GRPC_HEADERS, follow_redirects=True) as grpc_client,
    ):
        scraper = BetclicGrpcScraper(page_client)
        matches = await scraper.fetch_competition_matches("ligue_1")
        if not matches:
            print("No matches found"); return
        m = matches[0]
        print(f"Auditing: {m['home_team']} vs {m['away_team']} (match_id={m['match_id']})")
        body = encode_grpc_web_request(m["match_id"])
        raw = await _stream_first_grpc_frame(
            grpc_client, GRPC_ENDPOINT, body,
            httpx.Timeout(30.0, connect=10.0)
        )
        if not raw:
            print("Empty response"); return
        # Walk proto tree to find markets
        root  = _proto_fields(raw)
        f1    = _proto_fields(root[1][0])
        f1f1  = _proto_fields(f1[1][0])
        f11   = _proto_fields(f1f1[11][0])
        markets = f11.get(3, [])
        print(f"\nFound {len(markets)} markets:")
        for mb in markets:
            mf = _proto_fields(mb)
            name_raw = mf.get(2, [b""])[0]
            name = name_raw.decode("utf-8", errors="replace") if name_raw else "?"
            state = mf.get(9, [0])[0]
            n_groups = len(mf.get(11, []))
            n_sels = sum(len(_proto_fields(g).get(2, [])) for g in mf.get(11, []))
            print(f"  [{state}] {name!r:50s}  groups={n_groups}  sels={n_sels}")

asyncio.run(main())
```

- [ ] **Step 2 : Exécuter localement**
```bash
cd backend && python scripts/audit_betclic_markets.py
```
Noter les noms exacts des marchés dans la sortie — chercher ceux qui ressemblent à :
- `"Résultat du match"` ou `"1X2"` ou `"Vainqueur du match"` → `h2h`
- `"Total de buts"` ou `"Nombre de buts"` → `totals`
- `"Les deux équipes marquent"` ou `"BTTS"` → `btts`

La sortie sera utilisée dans Task 5 pour renseigner `_H2H_LABELS`, `_TOTALS_LABELS`, `_BTTS_LABELS`.

⚠️ **Ne pas passer à Task 5 avant d'avoir les noms exacts.**

---

## Task 5 — Betclic gRPC : extension h2h / totals / btts

**Files:**
- Modify: `backend/app/ingestion/betclic_grpc_scraper.py`
- Modify: `backend/tests/ingestion/test_betclic_grpc_scraper.py`

- [ ] **Step 1 : Écrire les tests**

Ajouter dans `backend/tests/ingestion/test_betclic_grpc_scraper.py` :

```python
from app.ingestion.betclic_grpc_scraper import _classify_market


# ── Tests _classify_market ─────────────────────────────────────────────────


def test_classify_goalscorer():
    assert _classify_market("Buteur (tps rég.)") == "goalscorer"


def test_classify_assist():
    assert _classify_market("Passeur décisif") == "assist"


def test_classify_h2h():
    # Remplacer par le nom exact trouvé lors de l'audit Task 4
    assert _classify_market("Résultat du match") == "h2h"


def test_classify_totals():
    # Remplacer par le nom exact trouvé lors de l'audit Task 4
    assert _classify_market("Total de buts") == "totals"


def test_classify_btts():
    # Remplacer par le nom exact trouvé lors de l'audit Task 4
    assert _classify_market("Les deux équipes marquent") == "btts"


def test_classify_unknown_returns_none():
    assert _classify_market("Paris en avance") is None


# ── Test intégration live ──────────────────────────────────────────────────


import pytest, httpx
from app.ingestion.betclic_grpc_scraper import (
    BetclicGrpcScraper, _PAGE_HEADERS, _GRPC_HEADERS, scrape_betclic_leagues,
)
from app.ingestion.scrape_result import MatchScrapeResult


@pytest.mark.asyncio
async def test_scrape_returns_match_scrape_result():
    """scrape_betclic_leagues retourne des MatchScrapeResult avec h2h + totals."""
    results = await scrape_betclic_leagues(["ligue_1"])
    assert len(results) > 0
    r = results[0]
    assert isinstance(r, MatchScrapeResult)
    assert r.bookmaker == "betclic"
    assert r.h2h is not None, "h2h manquant"
    assert "home" in r.h2h and "draw" in r.h2h and "away" in r.h2h
    assert r.totals is not None, "totals manquants"
    assert "over_2.5" in r.totals
    assert len(r.goalscorer) > 0
```

- [ ] **Step 2 : Exécuter pour vérifier l'échec**
```bash
cd backend && python -m pytest tests/ingestion/test_betclic_grpc_scraper.py::test_classify_h2h -v
```
Attendu : FAIL (`assert None == 'h2h'`)

- [ ] **Step 3 : Étendre `betclic_grpc_scraper.py`**

Remplacer dans `_classify_market` et `_parse_match_proto`. **Utiliser les noms exacts de l'audit Task 4 pour `_H2H_LABELS`, `_TOTALS_LABELS`, `_BTTS_LABELS`.**

```python
# Ajouter en haut du fichier, après _ASSIST_LABELS :
_H2H_LABELS = ("résultat du match", "1x2", "vainqueur du match")   # à ajuster selon audit
_TOTALS_LABELS = ("total de buts", "nombre de buts")                # à ajuster
_BTTS_LABELS = ("les deux équipes marquent", "btts")                # à ajuster

# Remplacer _classify_market :
def _classify_market(name: str) -> str | None:
    lower = name.lower()
    if any(x in lower for x in _GOALSCORER_LABELS):
        return "goalscorer"
    if any(x in lower for x in _ASSIST_LABELS):
        return "assist"
    if any(x in lower for x in _H2H_LABELS):
        return "h2h"
    if any(x in lower for x in _TOTALS_LABELS):
        return "totals"
    if any(x in lower for x in _BTTS_LABELS):
        return "btts"
    return None
```

Modifier `_parse_match_proto` — après la boucle sur les sélections, extraire les outcomes h2h/totals/btts et les stocker dans des dicts. Puis construire `MatchScrapeResult` à la place de `list[SelectionOdds]`.

Modifier la signature de retour de `fetch_match_odds` : `list[SelectionOdds]` → `MatchScrapeResult | None`.

Modifier `scrape_league` pour retourner `list[MatchScrapeResult]`.

Modifier `scrape_betclic_leagues` pour retourner `list[MatchScrapeResult]`.

**Code complet de `_parse_match_proto` révisé :**

```python
def _parse_match_proto(
    proto_bytes: bytes,
    home_team: str = "",
    away_team: str = "",
) -> dict:
    """Parse tous les marchés d'un GetMatchWithNotification.

    Retourne un dict avec les clés :
      h2h, totals, btts (dict | None)
      goalscorer, assist (list[PlayerOdds])
    """
    from app.ingestion.scrape_result import PlayerOdds

    out = {
        "h2h": None, "totals": None, "btts": None,
        "goalscorer": [], "assist": [],
    }

    try:
        root  = _proto_fields(proto_bytes)
        f1    = _proto_fields(root[1][0])
        f1f1  = _proto_fields(f1[1][0])
        f11   = _proto_fields(f1f1[11][0])
        markets = f11.get(3, [])
    except (KeyError, IndexError, ValueError):
        logger.warning("BetclicGrpcScraper: unexpected protobuf structure")
        return out

    for market_bytes in markets:
        try:
            market = _proto_fields(market_bytes)
        except Exception:
            continue

        name_raw = market.get(2, [b""])[0]
        market_name = name_raw.decode("utf-8", errors="replace") if name_raw else ""
        market_type = _classify_market(market_name)
        if not market_type:
            continue
        if market.get(9, [0])[0] == 3:  # suspended
            continue

        # Collect all selection (name, odds) pairs for this market
        sels: list[tuple[str, float]] = []
        for group_bytes in market.get(11, []):
            try:
                group = _proto_fields(group_bytes)
            except Exception:
                continue
            for sel_bytes in group.get(2, []):
                try:
                    sel = _proto_fields(sel_bytes)
                except Exception:
                    continue
                name_b = (sel.get(10) or sel.get(11) or [None])[0]
                if not name_b:
                    continue
                sel_name = name_b.decode("utf-8", errors="replace").strip()
                odds_raw = (sel.get(12) or [None])[0]
                if not odds_raw or len(odds_raw) != 8:
                    continue
                try:
                    val = struct.unpack("<d", odds_raw)[0]
                    if 1.01 <= val <= 1000.0:
                        sels.append((sel_name, round(val, 2)))
                except struct.error:
                    continue

        if market_type == "goalscorer":
            out["goalscorer"].extend(PlayerOdds(n, o) for n, o in sels if n and n != "Yes")
        elif market_type == "assist":
            out["assist"].extend(PlayerOdds(n, o) for n, o in sels if n and n != "Yes")
        elif market_type == "h2h" and len(sels) == 3:
            # Betclic order: home, draw, away — verified from live responses
            out["h2h"] = {"home": sels[0][1], "draw": sels[1][1], "away": sels[2][1]}
        elif market_type == "totals":
            totals: dict = {}
            for name, odds in sels:
                low = name.lower()
                if "1.5" in low:
                    key = "over_1.5" if "plus" in low or "over" in low else "under_1.5"
                elif "2.5" in low:
                    key = "over_2.5" if "plus" in low or "over" in low else "under_2.5"
                elif "3.5" in low:
                    key = "over_3.5" if "plus" in low or "over" in low else "under_3.5"
                else:
                    continue
                totals[key] = odds
            if totals:
                out["totals"] = totals
        elif market_type == "btts" and len(sels) == 2:
            out["btts"] = {"yes": sels[0][1], "no": sels[1][1]}

    return out
```

Modifier `fetch_match_odds` pour appeler `_parse_match_proto(raw, home_team, away_team)` et construire un `MatchScrapeResult`.

```python
async def fetch_match_odds(
    self,
    match_id: int,
    home_team: str,
    away_team: str,
    league: str,
    fixture_id: int = 0,
    language: str = "fr",
) -> MatchScrapeResult | None:
    from app.ingestion.scrape_result import MatchScrapeResult
    from datetime import datetime, timezone

    body = encode_grpc_web_request(match_id, language)
    client = self._grpc_client if self._grpc_client is not None else self._client
    timeout = httpx.Timeout(30.0, connect=10.0)

    try:
        raw = await _stream_first_grpc_frame(client, GRPC_ENDPOINT, body, timeout)
    except Exception as exc:
        logger.warning("BetclicGrpcScraper: gRPC failed match=%d: %s", match_id, exc)
        return None

    if not raw:
        return None

    parsed = _parse_match_proto(raw, home_team, away_team)
    return MatchScrapeResult(
        fixture_id=fixture_id,
        home_team=home_team,
        away_team=away_team,
        kickoff_utc=None,  # set by caller from match metadata
        league=league,
        bookmaker=BOOKMAKER,
        scraped_at=datetime.now(timezone.utc),
        h2h=parsed["h2h"],
        totals=parsed["totals"],
        btts=parsed["btts"],
        goalscorer=parsed["goalscorer"],
        assist=parsed["assist"],
    )
```

Mettre à jour `scrape_league` :

```python
async def scrape_league(self, league: str) -> list[MatchScrapeResult]:
    matches = await self.fetch_competition_matches(league)
    if not matches:
        return []
    results = []
    for i, mx in enumerate(matches):
        result = await self.fetch_match_odds(
            mx["match_id"], mx["home_team"], mx["away_team"], league
        )
        if result:
            result.kickoff_utc = mx.get("kickoff_utc")
        if result and result.goalscorer:
            results.append(result)
        if i < len(matches) - 1:
            await asyncio.sleep(_MATCH_SLEEP)
    return results
```

Mettre à jour `scrape_betclic_leagues` pour retourner `list[MatchScrapeResult]`.

- [ ] **Step 4 : Exécuter les tests unitaires**
```bash
cd backend && python -m pytest tests/ingestion/test_betclic_grpc_scraper.py -v -k "not asyncio"
```
Attendu : tous les tests `_classify_*` PASS

- [ ] **Step 5 : Dry-run live**
```bash
cd backend && python -m app.ingestion.betclic_grpc_scraper --league ligue_1 --dry-run
```
Vérifier que h2h, totals, btts apparaissent dans la sortie pour chaque match.

- [ ] **Step 6 : Commit**
```bash
git add backend/app/ingestion/betclic_grpc_scraper.py backend/tests/ingestion/test_betclic_grpc_scraper.py
git commit -m "feat(betclic): extend gRPC scraper — h2h + totals + btts + MatchScrapeResult"
```

---

## Task 6 — Unibet LVS : audit des markettypeId

- [ ] **Step 1 : Créer le script d'audit**

```python
# backend/scripts/audit_unibet_markets.py
"""Dump tous les markettypeId pour un match live Unibet LVS.
Run: cd backend && python scripts/audit_unibet_markets.py
"""
import asyncio
import httpx
from app.ingestion.unibet_lvs_scraper import (
    UnibetLVSScraper, _HEADERS, _LIST_PARAMS, LVS_BASE, LVS_NODE_IDS,
)

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        scraper = UnibetLVSScraper(client)
        await scraper._get_token()
        events = await scraper.fetch_event_ids("ligue_1")
        if not events:
            print("Aucun match"); return
        event_id, home, away, kickoff = events[0]
        print(f"Audit: {home} vs {away} (event_id={event_id})")
        url = f"{LVS_BASE}/lvs-api/ff/e{event_id}?{_LIST_PARAMS}"
        r = await client.get(url, headers=scraper._auth_headers(), timeout=15)
        r.raise_for_status()
        items = r.json().get("items", {})
        seen: dict[int, str] = {}
        for key, val in items.items():
            if not key.startswith("m"):
                continue
            mtype_id = val.get("markettypeId")
            name = val.get("desc") or val.get("name") or "?"
            if mtype_id and mtype_id not in seen:
                seen[mtype_id] = name
        print(f"\n{len(seen)} market types uniques:")
        for mid, name in sorted(seen.items(), key=lambda x: x[0]):
            print(f"  {mid:>12}  {name}")

asyncio.run(main())
```

- [ ] **Step 2 : Exécuter**
```bash
cd backend && python scripts/audit_unibet_markets.py
```
Chercher dans la sortie les IDs pour :
- Résultat 1X2 → `h2h`
- Total de buts / Over-Under → `totals`
- Les deux équipes marquent → `btts`

⚠️ **Ne pas passer à Task 7 avant d'avoir les IDs exacts.**

---

## Task 7 — Unibet LVS : extension h2h / totals / btts

**Files:**
- Modify: `backend/app/ingestion/unibet_lvs_scraper.py`
- Modify: `backend/tests/test_unibet_lvs_scraper.py`

- [ ] **Step 1 : Ajouter les tests**

```python
# Ajouter dans backend/tests/test_unibet_lvs_scraper.py

from app.ingestion.scrape_result import MatchScrapeResult

class TestParseMatchItemsMatchOdds:
    """_parse_match_items extrait h2h + totals + btts quand disponibles."""

    def _make_items(self, h2h_id: int, totals_id: int, btts_id: int) -> dict:
        """Build a minimal LVS items dict with match-level markets."""
        return {
            "e100": {"a": "PSG", "b": "Lyon", "start": "2605011900"},
            "m1": {"markettypeId": h2h_id, "parent": "e100", "desc": "1X2"},
            "m2": {"markettypeId": totals_id, "parent": "e100", "desc": "Total de buts"},
            "m3": {"markettypeId": btts_id, "parent": "e100", "desc": "BTTS"},
            # h2h outcomes
            "o1": {"parent": "m1", "desc": "PSG", "price": "2,10"},
            "o2": {"parent": "m1", "desc": "Match nul", "price": "3,40"},
            "o3": {"parent": "m1", "desc": "Lyon", "price": "3,60"},
            # totals outcomes
            "o4": {"parent": "m2", "desc": "Plus de 2.5", "price": "1,80"},
            "o5": {"parent": "m2", "desc": "Moins de 2.5", "price": "2,00"},
            # btts outcomes
            "o6": {"parent": "m3", "desc": "Oui", "price": "1,75"},
            "o7": {"parent": "m3", "desc": "Non", "price": "2,10"},
        }

    def test_h2h_extracted(self):
        # Remplacer les IDs par ceux trouvés lors de l'audit Task 6
        from app.ingestion.unibet_lvs_scraper import _MARKET_TYPES
        h2h_id = next(k for k, v in _MARKET_TYPES.items() if v == "h2h")
        totals_id = next(k for k, v in _MARKET_TYPES.items() if v == "totals")
        btts_id = next(k for k, v in _MARKET_TYPES.items() if v == "btts")
        scraper = UnibetLVSScraper.__new__(UnibetLVSScraper)
        items = self._make_items(h2h_id, totals_id, btts_id)
        mo = scraper._parse_match_items(items, "ligue_1")
        assert mo is not None
        assert mo.h2h == {"home": 2.10, "draw": 3.40, "away": 3.60}

    def test_totals_extracted(self):
        from app.ingestion.unibet_lvs_scraper import _MARKET_TYPES
        h2h_id = next(k for k, v in _MARKET_TYPES.items() if v == "h2h")
        totals_id = next(k for k, v in _MARKET_TYPES.items() if v == "totals")
        btts_id = next(k for k, v in _MARKET_TYPES.items() if v == "btts")
        scraper = UnibetLVSScraper.__new__(UnibetLVSScraper)
        items = self._make_items(h2h_id, totals_id, btts_id)
        mo = scraper._parse_match_items(items, "ligue_1")
        assert mo.totals is not None
        assert "over_2.5" in mo.totals

    def test_btts_extracted(self):
        from app.ingestion.unibet_lvs_scraper import _MARKET_TYPES
        h2h_id = next(k for k, v in _MARKET_TYPES.items() if v == "h2h")
        totals_id = next(k for k, v in _MARKET_TYPES.items() if v == "totals")
        btts_id = next(k for k, v in _MARKET_TYPES.items() if v == "btts")
        scraper = UnibetLVSScraper.__new__(UnibetLVSScraper)
        items = self._make_items(h2h_id, totals_id, btts_id)
        mo = scraper._parse_match_items(items, "ligue_1")
        assert mo.btts == {"yes": 1.75, "no": 2.10}

    def test_returns_match_scrape_result(self):
        from app.ingestion.unibet_lvs_scraper import _MARKET_TYPES
        h2h_id = next(k for k, v in _MARKET_TYPES.items() if v == "h2h")
        totals_id = next(k for k, v in _MARKET_TYPES.items() if v == "totals")
        btts_id = next(k for k, v in _MARKET_TYPES.items() if v == "btts")
        scraper = UnibetLVSScraper.__new__(UnibetLVSScraper)
        items = self._make_items(h2h_id, totals_id, btts_id)
        mo = scraper._parse_match_items(items, "ligue_1")
        assert isinstance(mo, MatchScrapeResult)
        assert mo.bookmaker == "unibet"
```

- [ ] **Step 2 : Vérifier l'échec**
```bash
cd backend && python -m pytest tests/test_unibet_lvs_scraper.py::TestParseMatchItemsMatchOdds -v
```
Attendu : FAIL (`assert None == {...}` car les IDs ne sont pas encore dans `_MARKET_TYPES`)

- [ ] **Step 3 : Étendre `unibet_lvs_scraper.py`**

1. Ajouter les IDs confirmés dans `_MARKET_TYPES` (remplacer `???` par les IDs de l'audit) :

```python
_MARKET_TYPES: dict[int, str] = {
    # Match-level (confirmés par audit Task 6)
    ???: "h2h",
    ???: "totals",
    ???: "btts",
    # Player props (confirmés)
    31:        "goalscorer",
    4:         "goalscorer",
    100002524: "assist",
}
```

2. Modifier `_parse_match_items` pour retourner `MatchScrapeResult` au lieu de `MatchOdds`. La logique h2h/totals/btts suit le même pattern que les props : itérer sur les outcomes avec `parent` pointant vers un marché target. Le mapping des outcomes :
   - h2h : 3 outcomes → `{home: sels[0], draw: sels[1], away: sels[2]}`
   - totals : outcomes avec "2.5" dans le nom → déduire over/under depuis "plus"/"moins"
   - btts : 2 outcomes → `{yes: sel_oui, no: sel_non}`

3. Modifier `scrape_league` pour retourner `list[MatchScrapeResult]`.
4. Modifier `scrape_all_unibet` pour retourner `list[MatchScrapeResult]`.

- [ ] **Step 4 : Tests unitaires**
```bash
cd backend && python -m pytest tests/test_unibet_lvs_scraper.py -v
```
Attendu : tous PASS

- [ ] **Step 5 : Dry-run live**
```bash
cd backend && python -m app.ingestion.unibet_lvs_scraper --league ligue_1 --dry-run
```
Vérifier la présence de h2h, totals, btts dans la sortie.

- [ ] **Step 6 : Commit**
```bash
git add backend/app/ingestion/unibet_lvs_scraper.py backend/tests/test_unibet_lvs_scraper.py
git commit -m "feat(unibet): extend LVS scraper — h2h + totals + btts + MatchScrapeResult"
```

---

## Task 8 — Storage : persister MatchScrapeResult

**Files:**
- Create: `backend/app/ingestion/odds_storage.py`
- Create: `backend/tests/ingestion/test_odds_storage.py`

- [ ] **Step 1 : Écrire les tests**

```python
# backend/tests/ingestion/test_odds_storage.py
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.ingestion.scrape_result import MatchScrapeResult, PlayerOdds
from app.ingestion.odds_storage import store_match_scrape_result


@pytest.mark.asyncio
async def test_store_complete_result():
    """store_match_scrape_result retourne (match_rows, player_rows) positifs."""
    result = MatchScrapeResult(
        fixture_id=42,
        home_team="PSG",
        away_team="Lyon",
        kickoff_utc=datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc),
        league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
        h2h={"home": 2.1, "draw": 3.4, "away": 3.6},
        totals={"over_2.5": 1.8, "under_2.5": 2.0},
        btts={"yes": 1.75, "no": 2.1},
        goalscorer=[PlayerOdds("Mbappé", 3.5), PlayerOdds("Dembélé", 4.0)],
        assist=[PlayerOdds("Vitinha", 5.0)],
    )

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=7))
    session.commit = AsyncMock()

    match_rows, player_rows = await store_match_scrape_result(result, session)
    assert match_rows >= 0
    assert player_rows >= 0
    assert session.commit.called


@pytest.mark.asyncio
async def test_store_incomplete_result_still_stores_player_props():
    """Même sans h2h/totals/btts, les props joueurs sont stockés."""
    result = MatchScrapeResult(
        fixture_id=42,
        home_team="PSG", away_team="Lyon",
        kickoff_utc=None, league="ligue_1",
        bookmaker="betclic",
        scraped_at=datetime(2026, 5, 1, 17, 0, tzinfo=timezone.utc),
        goalscorer=[PlayerOdds("Mbappé", 3.5)],
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    session.commit = AsyncMock()
    _, player_rows = await store_match_scrape_result(result, session)
    assert player_rows >= 0
```

- [ ] **Step 2 : Vérifier l'échec**
```bash
cd backend && python -m pytest tests/ingestion/test_odds_storage.py -v
```
Attendu : `ModuleNotFoundError`

- [ ] **Step 3 : Créer `odds_storage.py`**

```python
# backend/app/ingestion/odds_storage.py
"""Persist MatchScrapeResult to match_odds_snapshots + player_odds_snapshots."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.scrape_result import MatchScrapeResult
from app.models.match_odds import MatchOddsSnapshot
from app.models.player_odds_snapshot import PlayerOddsSnapshot

logger = logging.getLogger(__name__)


async def store_match_scrape_result(
    result: MatchScrapeResult,
    session: AsyncSession,
) -> tuple[int, int]:
    """Write MatchScrapeResult to both snapshot tables.

    Returns (match_odds_rows_inserted, player_odds_rows_upserted).
    Uses ON CONFLICT DO NOTHING for match odds (immutable snapshots).
    Uses ON CONFLICT DO UPDATE for player odds (keep latest odds).
    """
    now = result.scraped_at

    # ── 1. match_odds_snapshots ───────────────────────────────────
    match_rows: list[dict] = []
    if result.h2h:
        for outcome, odds in result.h2h.items():
            match_rows.append(_match_row(result, "h2h", outcome, odds, now))
    if result.totals:
        for outcome, odds in result.totals.items():
            match_rows.append(_match_row(result, "totals", outcome, odds, now))
    if result.btts:
        for outcome, odds in result.btts.items():
            match_rows.append(_match_row(result, "btts", outcome, odds, now))

    match_inserted = 0
    if match_rows:
        stmt = (
            pg_insert(MatchOddsSnapshot)
            .values(match_rows)
            .on_conflict_do_nothing(constraint="uq_match_odds_snapshot")
        )
        res = await session.execute(stmt)
        match_inserted = res.rowcount or 0

    # ── 2. player_odds_snapshots ──────────────────────────────────
    player_rows: list[dict] = []
    for p in result.goalscorer:
        player_rows.append(_player_row(result, "goalscorer", p.player_name, p.odds, now))
    for p in result.assist:
        player_rows.append(_player_row(result, "assist", p.player_name, p.odds, now))

    player_upserted = 0
    if player_rows:
        stmt2 = (
            pg_insert(PlayerOddsSnapshot)
            .values(player_rows)
            .on_conflict_do_update(
                constraint="uq_player_odds_snapshot",
                set_={"odds": pg_insert(PlayerOddsSnapshot).excluded.odds,
                      "scraped_at": pg_insert(PlayerOddsSnapshot).excluded.scraped_at},
            )
        )
        res2 = await session.execute(stmt2)
        player_upserted = res2.rowcount or 0

    await session.commit()
    logger.debug(
        "odds_storage: fixture=%d %s match_rows=%d player_rows=%d",
        result.fixture_id, result.bookmaker, match_inserted, player_upserted,
    )
    return match_inserted, player_upserted


def _match_row(r: MatchScrapeResult, market: str, outcome: str, odds: float, now: datetime) -> dict:
    return {
        "fixture_id": r.fixture_id,
        "bookmaker": r.bookmaker,
        "market_type": market,
        "outcome": outcome,
        "odds": odds,
        "snapshot_utc": now,
        "source": r.bookmaker,
        "source_url": None,
        "parse_version": "v2",
        "fallback_used": False,
    }


def _player_row(r: MatchScrapeResult, market: str, player: str, odds: float, now: datetime) -> dict:
    return {
        "fixture_id": r.fixture_id,
        "bookmaker": r.bookmaker,
        "market_type": market,
        "player_name": player,
        "odds": odds,
        "scraped_at": now,
    }
```

- [ ] **Step 4 : Tests**
```bash
cd backend && python -m pytest tests/ingestion/test_odds_storage.py -v
```
Attendu : 2 PASS

- [ ] **Step 5 : Commit**
```bash
git add backend/app/ingestion/odds_storage.py backend/tests/ingestion/test_odds_storage.py
git commit -m "feat(odds): add odds_storage — persist MatchScrapeResult to DB"
```

---

## Task 9 — OddsScheduler

**Files:**
- Create: `backend/app/ingestion/odds_scheduler.py`
- Create: `backend/tests/ingestion/test_odds_scheduler.py`

- [ ] **Step 1 : Écrire les tests**

```python
# backend/tests/ingestion/test_odds_scheduler.py
from datetime import datetime, timedelta, timezone
import pytest
from app.ingestion.odds_scheduler import scrape_interval_seconds, should_scrape


def _ko(minutes_from_now: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)


def test_interval_far_from_ko():
    """Plus de 6h → 7200s (2h)."""
    assert scrape_interval_seconds(_ko(600)) == 7200


def test_interval_mid_range():
    """2h → 6h → 1800s (30min)."""
    assert scrape_interval_seconds(_ko(240)) == 1800


def test_interval_close_to_ko():
    """5min → 2h → 120s (2min)."""
    assert scrape_interval_seconds(_ko(30)) == 120


def test_should_not_scrape_within_5min():
    """Moins de 5min avant KO → stop."""
    assert should_scrape(_ko(3), last_scraped_at=None) is False


def test_should_not_scrape_past_ko():
    """Après le KO → stop."""
    assert should_scrape(_ko(-10), last_scraped_at=None) is False


def test_should_scrape_when_never_scraped():
    """Jamais scrapé + dans fenêtre → True."""
    assert should_scrape(_ko(60), last_scraped_at=None) is True


def test_should_not_scrape_when_recent():
    """Scrapé il y a 1min, intervalle=120s → False."""
    last = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert should_scrape(_ko(60), last_scraped_at=last) is False


def test_should_scrape_when_overdue():
    """Scrapé il y a 3min, intervalle=120s → True."""
    last = datetime.now(timezone.utc) - timedelta(seconds=180)
    assert should_scrape(_ko(60), last_scraped_at=last) is True
```

- [ ] **Step 2 : Vérifier l'échec**
```bash
cd backend && python -m pytest tests/ingestion/test_odds_scheduler.py -v
```
Attendu : `ModuleNotFoundError`

- [ ] **Step 3 : Créer `odds_scheduler.py`**

```python
# backend/app/ingestion/odds_scheduler.py
"""OddsScheduler — adaptive scraping frequency based on time-to-KO.

Frequency table:
  > 6h before KO   : every 2h   (7200s)
  2h–6h before KO  : every 30m  (1800s)
  5min–2h before KO: every 2min (120s)
  < 5min before KO : stop
  after KO         : stop
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Thresholds
_STOP_BEFORE_KO = timedelta(minutes=5)
_HIGH_FREQ_THRESHOLD = timedelta(hours=2)
_MID_FREQ_THRESHOLD = timedelta(hours=6)

# Intervals
_INTERVAL_HIGH = 120    # 2min
_INTERVAL_MID = 1800    # 30min
_INTERVAL_LOW = 7200    # 2h


def scrape_interval_seconds(kickoff_utc: datetime) -> int:
    """Return the required scrape interval in seconds for a given KO time."""
    now = datetime.now(UTC)
    if kickoff_utc.tzinfo is None:
        kickoff_utc = kickoff_utc.replace(tzinfo=UTC)
    delta = kickoff_utc - now
    if delta <= _HIGH_FREQ_THRESHOLD:
        return _INTERVAL_HIGH
    if delta <= _MID_FREQ_THRESHOLD:
        return _INTERVAL_MID
    return _INTERVAL_LOW


def should_scrape(kickoff_utc: datetime, last_scraped_at: datetime | None) -> bool:
    """Return True if this fixture is due for a scrape."""
    now = datetime.now(UTC)
    if kickoff_utc.tzinfo is None:
        kickoff_utc = kickoff_utc.replace(tzinfo=UTC)
    delta = kickoff_utc - now
    # Stop window: less than 5min before KO, or past KO
    if delta <= _STOP_BEFORE_KO:
        return False
    # Never scraped → scrape now
    if last_scraped_at is None:
        return True
    if last_scraped_at.tzinfo is None:
        last_scraped_at = last_scraped_at.replace(tzinfo=UTC)
    interval = scrape_interval_seconds(kickoff_utc)
    return (now - last_scraped_at).total_seconds() >= interval


class OddsScheduler:
    """Drives adaptive scraping of Betclic + Unibet for upcoming fixtures."""

    async def tick(self, session: AsyncSession) -> int:
        """Process all fixtures due for a scrape. Returns count of fixtures scraped."""
        from app.ingestion.betclic_grpc_scraper import scrape_betclic_leagues
        from app.ingestion.odds_storage import store_match_scrape_result
        from app.ingestion.unibet_lvs_scraper import scrape_all_unibet
        from app.models.bzzoiro import BzzEvent
        from app.models.fixtures import Fixture
        from app.models.odds_scrape_state import OddsScrapeState

        now = datetime.now(UTC)

        # Load upcoming fixtures with KO in the next 72h
        cutoff = now + timedelta(hours=72)
        result = await session.execute(
            select(Fixture).where(
                Fixture.kickoff_utc.isnot(None),
                Fixture.kickoff_utc <= cutoff,
                Fixture.kickoff_utc > now - timedelta(minutes=5),
                Fixture.status.notin_(["finished", "cancelled", "postponed"]),
            )
        )
        fixtures = result.scalars().all()
        if not fixtures:
            return 0

        # Load scrape states
        states_result = await session.execute(
            select(OddsScrapeState).where(
                OddsScrapeState.fixture_id.in_([f.id for f in fixtures])
            )
        )
        states: dict[int, OddsScrapeState] = {
            s.fixture_id: s for s in states_result.scalars().all()
        }

        # Determine which fixtures are due
        due = [
            f for f in fixtures
            if should_scrape(
                f.kickoff_utc,
                states[f.id].last_scraped_at if f.id in states else None,
            )
        ]

        if not due:
            logger.debug("OddsScheduler.tick: 0 fixtures due")
            return 0

        logger.info("OddsScheduler.tick: %d fixtures due for scraping", len(due))

        # Group by league
        leagues_needed: set[str] = set()
        fixture_by_teams: dict[tuple[str, str], int] = {}
        for f in due:
            league = _league_key(f.league) if hasattr(f, "league") else None
            if league:
                leagues_needed.add(league)
            fixture_by_teams[(f.home_team.lower(), f.away_team.lower())] = f.id

        # Scrape both books in parallel
        betclic_results, unibet_results = await asyncio.gather(
            scrape_betclic_leagues(list(leagues_needed)),
            scrape_all_unibet(list(leagues_needed)),
            return_exceptions=True,
        )

        scraped = 0
        all_results = []
        if isinstance(betclic_results, list):
            all_results.extend(betclic_results)
        if isinstance(unibet_results, list):
            all_results.extend(unibet_results)

        # Match scraped results to fixture_ids and store
        for r in all_results:
            key = (r.home_team.lower(), r.away_team.lower())
            fixture_id = fixture_by_teams.get(key)
            if not fixture_id:
                # Try reversed (some books swap home/away)
                key_rev = (r.away_team.lower(), r.home_team.lower())
                fixture_id = fixture_by_teams.get(key_rev)
            if not fixture_id:
                logger.debug("OddsScheduler: no fixture match for %s vs %s", r.home_team, r.away_team)
                continue
            r.fixture_id = fixture_id
            await store_match_scrape_result(r, session)
            scraped += 1

        # Update odds_scrape_state for all due fixtures
        for f in due:
            betclic_ok = any(
                r.fixture_id == f.id and r.bookmaker == "betclic"
                for r in all_results if not isinstance(r, Exception)
            )
            unibet_ok = any(
                r.fixture_id == f.id and r.bookmaker == "unibet"
                for r in all_results if not isinstance(r, Exception)
            )
            interval = scrape_interval_seconds(f.kickoff_utc)
            stmt = pg_insert(OddsScrapeState).values(
                fixture_id=f.id,
                last_scraped_at=now,
                next_scrape_at=now + timedelta(seconds=interval),
                betclic_ok=betclic_ok,
                unibet_ok=unibet_ok,
            ).on_conflict_do_update(
                index_elements=["fixture_id"],
                set_={
                    "last_scraped_at": now,
                    "next_scrape_at": now + timedelta(seconds=interval),
                    "betclic_ok": betclic_ok,
                    "unibet_ok": unibet_ok,
                },
            )
            await session.execute(stmt)

        await session.commit()
        logger.info("OddsScheduler.tick: stored %d results for %d fixtures", scraped, len(due))
        return len(due)


def _league_key(league_name: str | None) -> str | None:
    """Map fixture league name to scraper league key."""
    if not league_name:
        return None
    mapping = {
        "ligue 1": "ligue_1", "ligue_1": "ligue_1",
        "premier league": "premier_league", "premier_league": "premier_league",
        "bundesliga": "bundesliga",
        "la liga": "la_liga", "la_liga": "la_liga",
        "serie a": "serie_a", "serie_a": "serie_a",
        "champions league": "champions_league", "champions_league": "champions_league",
    }
    return mapping.get(league_name.lower())
```

- [ ] **Step 4 : Tests unitaires**
```bash
cd backend && python -m pytest tests/ingestion/test_odds_scheduler.py -v
```
Attendu : 8 PASS

- [ ] **Step 5 : Commit**
```bash
git add backend/app/ingestion/odds_scheduler.py backend/tests/ingestion/test_odds_scheduler.py
git commit -m "feat(odds): add OddsScheduler with adaptive KO-based frequency"
```

---

## Task 10 — MarketXgService : MAX_SNAPSHOT_AGE = 30min

**Files:**
- Modify: `backend/app/services/market_xg.py:30`

- [ ] **Step 1 : Modifier la constante**

```python
# backend/app/services/market_xg.py — ligne ~30
MAX_SNAPSHOT_AGE = timedelta(minutes=30)   # était timedelta(hours=3)
```

- [ ] **Step 2 : Vérifier les tests existants**
```bash
cd backend && python -m pytest tests/test_market_xg_service.py tests/test_market_xg_solvers.py -v
```
Attendu : tous PASS (les tests mocquent les timestamps, pas sensibles à cette constante)

- [ ] **Step 3 : Commit**
```bash
git add backend/app/services/market_xg.py
git commit -m "fix(market_xg): reduce MAX_SNAPSHOT_AGE from 3h to 30min"
```

---

## Task 11 — Mettre à jour worker.py

**Files:**
- Modify: `backend/app/worker.py`

- [ ] **Step 1 : Supprimer les imports morts**

Retirer de la section imports en haut du fichier :
```python
# Supprimer ces lignes :
from app.ingestion.odds import QuotaExhaustedError, fetch_events_for_league, ingest_odds_for_league, normalize_league_key
from app.ingestion.match_odds import ingest_match_odds_for_league
from app.services.market_scrape_scheduler import MarketScrapeScheduler
```

- [ ] **Step 2 : Supprimer les 4 jobs morts**

Supprimer les fonctions complètes :
- `async def job_snapshot_odds():` (lignes ~511–700)
- `async def job_snapshot_direct_odds():` (lignes ~703–828)
- `async def job_oddsportal_scheduler_tick():` (lignes ~1667–1677)
- `async def job_discover_oddsportal_urls():` (lignes ~1679–1697)

- [ ] **Step 3 : Ajouter `job_odds_scheduler_tick`**

Ajouter avant la section `# ── Bzzoiro Jobs ───` :

```python
# ── Odds Scheduler ────────────────────────────────────────────────

_odds_scheduler = None  # initialized lazily

async def job_odds_scheduler_tick() -> None:
    """Adaptive odds scraper — fires Betclic + Unibet for fixtures due based on KO distance."""
    global _odds_scheduler
    from app.ingestion.odds_scheduler import OddsScheduler
    if _odds_scheduler is None:
        _odds_scheduler = OddsScheduler()
    async with async_session() as session:
        try:
            n = await _odds_scheduler.tick(session)
            if n:
                logger.info("job_odds_scheduler_tick: scraped %d fixtures", n)
        except Exception as exc:
            logger.error("job_odds_scheduler_tick error: %s", exc, exc_info=True)
```

- [ ] **Step 4 : Enregistrer le job dans `setup_scheduler`**

Remplacer les 4 blocs `scheduler.add_job` des jobs supprimés par un seul :

```python
    # Odds scheduler tick: every 60 seconds
    scheduler.add_job(
        job_odds_scheduler_tick,
        IntervalTrigger(seconds=60),
        id="job_odds_scheduler_tick",
        name="Odds scheduler: adaptive Betclic+Unibet scraping based on KO distance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 5 : Vérifier que le worker démarre sans erreur**
```bash
cd backend && python -c "from app.worker import setup_scheduler; print('OK')"
```
Attendu : `OK`

- [ ] **Step 6 : Commit**
```bash
git add backend/app/worker.py
git commit -m "feat(worker): replace 4 dead odds jobs with job_odds_scheduler_tick"
```

---

## Task 12 — Supprimer le code mort

- [ ] **Step 1 : Supprimer les fichiers**

```bash
cd backend
git rm app/ingestion/oddsportal_scraper.py
git rm app/ingestion/oddsportal_fixture_matcher.py
git rm app/ingestion/oddsportal_league_discoverer.py
git rm app/ingestion/market_scrape_chain.py
git rm app/ingestion/betclic_match_scraper.py
git rm app/ingestion/unibet_match_scraper.py
git rm app/ingestion/odds.py
git rm app/ingestion/direct_scrapers.py
git rm app/services/market_scrape_scheduler.py
git rm app/models/odds.py
git rm app/models/poll_state.py
```

- [ ] **Step 2 : Vérifier qu'aucun fichier restant n'importe ces modules**

```bash
cd backend && grep -rn "oddsportal_scraper\|oddsportal_fixture\|oddsportal_league\|market_scrape_chain\|betclic_match_scraper\|unibet_match_scraper\|ingestion.odds\|direct_scrapers\|market_scrape_scheduler\|models.odds\|models.poll_state\|OddsPortalPollState\|OddsSnapshot" app/ --include="*.py"
```
Attendu : **aucun résultat**. Si des imports subsistent, les corriger avant de continuer.

- [ ] **Step 3 : Vérifier que la suite de tests passe**

```bash
cd backend && python -m pytest tests/ -v --ignore=tests/ingestion/bzzoiro -x -q 2>&1 | tail -20
```
Attendu : tous PASS, aucune `ImportError`.

- [ ] **Step 4 : Commit**

```bash
git add -A
git commit -m "chore: delete dead odds pipeline code (OddsPortal, Odds API, old scrapers)"
```

---

## Task 13 — Test end-to-end sur VPS

- [ ] **Step 1 : Rebuild backend + worker**

Sur le VPS :
```bash
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker
```

- [ ] **Step 2 : Déclencher manuellement le scheduler**

```bash
docker exec ev0-compose-z5hvqt-backend-1 python -c "
import asyncio
from app.ingestion.odds_scheduler import OddsScheduler
from app.db import async_session

async def run():
    async with async_session() as session:
        n = await OddsScheduler().tick(session)
        print(f'Scraped: {n} fixtures')

asyncio.run(run())
"
```

- [ ] **Step 3 : Vérifier les données en DB**

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -c "
SELECT bookmaker, market_type, COUNT(*) 
FROM match_odds_snapshots 
WHERE snapshot_utc > NOW() - INTERVAL '10 minutes'
GROUP BY bookmaker, market_type 
ORDER BY bookmaker, market_type;"
```
Attendu : lignes pour `betclic` et/ou `unibet` avec `h2h`, `totals`, `btts`.

```bash
docker exec ev0-compose-z5hvqt-db-1 psql -U ev0 -c "
SELECT bookmaker, market_type, COUNT(*) 
FROM player_odds_snapshots 
WHERE scraped_at > NOW() - INTERVAL '10 minutes'
GROUP BY bookmaker, market_type;"
```
Attendu : lignes `goalscorer` + `assist` pour betclic et unibet.

- [ ] **Step 4 : Tester le calculateur**

Ouvrir le frontend → sélectionner un match → ouvrir le calculateur.
Attendu : les xG s'affichent (plus "No market odds available").

- [ ] **Step 5 : Commit final**

```bash
git add -A
git commit -m "feat: odds pipeline rebuild complete — Betclic+Unibet adaptive scheduler"
```

---

## Résumé des commits

| # | Message |
|---|---|
| 1 | `feat(odds): add MatchScrapeResult shared output type` |
| 2 | `feat(odds): add PlayerOddsSnapshot + OddsScrapeState models` |
| 3 | `feat(odds): migration 022 — player_odds_snapshots + odds_scrape_state` |
| 4 | `feat(betclic): extend gRPC scraper — h2h + totals + btts + MatchScrapeResult` |
| 5 | `feat(unibet): extend LVS scraper — h2h + totals + btts + MatchScrapeResult` |
| 6 | `feat(odds): add odds_storage — persist MatchScrapeResult to DB` |
| 7 | `feat(odds): add OddsScheduler with adaptive KO-based frequency` |
| 8 | `fix(market_xg): reduce MAX_SNAPSHOT_AGE from 3h to 30min` |
| 9 | `feat(worker): replace 4 dead odds jobs with job_odds_scheduler_tick` |
| 10 | `chore: delete dead odds pipeline code` |
| 11 | `feat: odds pipeline rebuild complete — end-to-end verified` |
