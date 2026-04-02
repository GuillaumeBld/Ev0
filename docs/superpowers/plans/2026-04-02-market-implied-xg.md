# Market-Implied xG Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplacer Dixon-Coles par des xG dérivés des cotes betting (Over 2.5 + BTTS + H2H) pour aligner le pricing sur le marché.

**Architecture:** Trois composants séquentiels : (1) nouveau modèle `MatchOddsSnapshot` + migration Alembic, (2) ingestion match odds dans `job_snapshot_odds`, (3) `MarketXgService` qui lit ces cotes et les inverse en (λh, λa) via scipy, intégré dans `load_match_pricing()` avec fallback Dixon-Coles.

**Tech Stack:** Python, SQLAlchemy (async), FastAPI, scipy.optimize.brentq, APScheduler, The Odds API v4

---

## File Structure

| Action | Fichier | Rôle |
|--------|---------|------|
| Create | `backend/app/models/match_odds.py` | Modèle SQLAlchemy `MatchOddsSnapshot` |
| Create | `backend/alembic/versions/015_match_odds_snapshots.py` | Migration Alembic |
| Modify | `backend/app/models/__init__.py` | Enregistrer le nouveau modèle |
| Create | `backend/app/ingestion/match_odds.py` | Ingestion match-level odds (h2h/totals/btts) |
| Modify | `backend/app/worker.py` | Appeler l'ingestion match odds dans `job_snapshot_odds` |
| Create | `backend/app/services/market_xg.py` | `MarketXgResult` + `MarketXgService` (solvers + compute) |
| Modify | `backend/app/pricing/team_xg.py` | Ajouter `xg_source` à `MatchPricingResult`, appeler `MarketXgService` dans `load_match_pricing()` |
| Modify | `backend/app/api/pricing.py` | Ajouter `xg_source` à `MatchPriceResponse` |
| Create | `backend/tests/test_market_xg_solvers.py` | Unit tests des solvers (Over 2.5, BTTS, H2H, cross-val) |
| Create | `backend/tests/test_match_odds_ingestion.py` | Tests du parseur match odds |
| Create | `backend/tests/test_market_xg_integration.py` | Test integration `compute()` + `load_match_pricing()` |

---

## Chunk 1: DB Schema + Ingestion

### Task 1: Modèle MatchOddsSnapshot + migration

**Files:**
- Create: `backend/app/models/match_odds.py`
- Create: `backend/alembic/versions/015_match_odds_snapshots.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: Écrire le test d'import du modèle**

```python
# backend/tests/test_match_odds_ingestion.py
def test_match_odds_snapshot_model_importable():
    from app.models.match_odds import MatchOddsSnapshot
    snap = MatchOddsSnapshot(
        fixture_id=1,
        bookmaker="betfair",
        market_type="totals",
        outcome="over_2.5",
        odds=1.85,
    )
    assert snap.bookmaker == "betfair"
    assert snap.market_type == "totals"
    assert snap.outcome == "over_2.5"
    assert snap.odds == 1.85
```

- [ ] **Step 2: Vérifier que le test échoue**

```bash
cd backend && uv run pytest tests/test_match_odds_ingestion.py::test_match_odds_snapshot_model_importable -v
```
Expected: FAIL avec `ModuleNotFoundError` ou `ImportError`

- [ ] **Step 3: Créer le modèle**

```python
# backend/app/models/match_odds.py
"""Match-level odds snapshot model (h2h / totals / btts)."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class MatchOddsSnapshot(Base, TimestampMixin):
    """A snapshot of match-level bookmaker odds (h2h, totals, btts)."""

    __tablename__ = "match_odds_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(String(50), index=True)
    # 'h2h' | 'totals' | 'btts'
    market_type: Mapped[str] = mapped_column(String(50), index=True)
    # 'home' | 'draw' | 'away' | 'over_2.5' | 'under_2.5' | 'yes' | 'no'
    outcome: Mapped[str] = mapped_column(String(50))
    odds: Mapped[float] = mapped_column(Float)
    snapshot_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    fixture = relationship("Fixture")

    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "bookmaker",
            "market_type",
            "outcome",
            "snapshot_utc",
            name="uq_match_odds_snapshot",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MatchOddsSnapshot fixture={self.fixture_id} "
            f"{self.bookmaker} {self.market_type}/{self.outcome} @{self.odds}>"
        )
```

- [ ] **Step 4: Enregistrer dans `__init__.py`**

Dans `backend/app/models/__init__.py`, ajouter :
```python
from app.models.match_odds import MatchOddsSnapshot  # après la ligne OddsSnapshot
```
Et ajouter `"MatchOddsSnapshot"` dans `__all__`.

- [ ] **Step 5: Créer la migration Alembic**

```python
# backend/alembic/versions/015_match_odds_snapshots.py
"""match_odds_snapshots table for market-implied xG

Revision ID: 015
Revises: 014
Create Date: 2026-04-02
"""

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_odds_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "fixture_id",
            sa.Integer(),
            sa.ForeignKey("fixtures.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("bookmaker", sa.String(50), nullable=False, index=True),
        sa.Column("market_type", sa.String(50), nullable=False, index=True),
        sa.Column("outcome", sa.String(50), nullable=False),
        sa.Column("odds", sa.Float(), nullable=False),
        sa.Column(
            "snapshot_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "fixture_id",
            "bookmaker",
            "market_type",
            "outcome",
            "snapshot_utc",
            name="uq_match_odds_snapshot",
        ),
    )


def downgrade() -> None:
    op.drop_table("match_odds_snapshots")
```

- [ ] **Step 6: Vérifier que le test passe**

```bash
cd backend && uv run pytest tests/test_match_odds_ingestion.py::test_match_odds_snapshot_model_importable -v
```
Expected: PASS

- [ ] **Step 7: Lint**

```bash
cd backend && uv run ruff check app/models/match_odds.py app/models/__init__.py alembic/versions/015_match_odds_snapshots.py
```
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/match_odds.py backend/app/models/__init__.py backend/alembic/versions/015_match_odds_snapshots.py backend/tests/test_match_odds_ingestion.py
git commit -m "feat: add MatchOddsSnapshot model and migration 015"
```

---

### Task 2: Ingestion match odds (parseur + ingest function)

**Files:**
- Create: `backend/app/ingestion/match_odds.py`
- Modify: `backend/tests/test_match_odds_ingestion.py`

Le format The Odds API pour match odds :

```json
{
  "id": "event_123",
  "home_team": "Paris Saint-Germain",
  "away_team": "Olympique Lyonnais",
  "bookmakers": [{
    "key": "betfair",
    "markets": [
      {
        "key": "h2h",
        "outcomes": [
          {"name": "Paris Saint-Germain", "price": 1.55},
          {"name": "Draw", "price": 4.10},
          {"name": "Olympique Lyonnais", "price": 6.00}
        ]
      },
      {
        "key": "totals",
        "outcomes": [
          {"name": "Over", "point": 2.5, "price": 1.85},
          {"name": "Under", "point": 2.5, "price": 2.00}
        ]
      },
      {
        "key": "both_teams_to_score",
        "outcomes": [
          {"name": "Yes", "price": 1.92},
          {"name": "No", "price": 1.90}
        ]
      }
    ]
  }]
}
```

- [ ] **Step 1: Écrire les tests du parseur**

```python
# Ajouter à backend/tests/test_match_odds_ingestion.py

from app.ingestion.match_odds import parse_match_odds_event


_SAMPLE_EVENT = {
    "id": "event_123",
    "home_team": "Paris Saint-Germain",
    "away_team": "Olympique Lyonnais",
    "bookmakers": [
        {
            "key": "betfair",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Paris Saint-Germain", "price": 1.55},
                        {"name": "Draw", "price": 4.10},
                        {"name": "Olympique Lyonnais", "price": 6.00},
                    ],
                },
                {
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "point": 2.5, "price": 1.85},
                        {"name": "Under", "point": 2.5, "price": 2.00},
                    ],
                },
                {
                    "key": "both_teams_to_score",
                    "outcomes": [
                        {"name": "Yes", "price": 1.92},
                        {"name": "No", "price": 1.90},
                    ],
                },
            ],
        }
    ],
}


def test_parse_match_odds_h2h():
    rows = parse_match_odds_event(_SAMPLE_EVENT)
    h2h = [r for r in rows if r["market_type"] == "h2h"]
    assert len(h2h) == 3
    outcomes = {r["outcome"]: r["odds"] for r in h2h}
    assert outcomes["home"] == pytest.approx(1.55)
    assert outcomes["draw"] == pytest.approx(4.10)
    assert outcomes["away"] == pytest.approx(6.00)


def test_parse_match_odds_totals():
    rows = parse_match_odds_event(_SAMPLE_EVENT)
    totals = [r for r in rows if r["market_type"] == "totals"]
    assert len(totals) == 2
    outcomes = {r["outcome"]: r["odds"] for r in totals}
    assert outcomes["over_2.5"] == pytest.approx(1.85)
    assert outcomes["under_2.5"] == pytest.approx(2.00)


def test_parse_match_odds_btts():
    rows = parse_match_odds_event(_SAMPLE_EVENT)
    btts = [r for r in rows if r["market_type"] == "btts"]
    assert len(btts) == 2
    outcomes = {r["outcome"]: r["odds"] for r in btts}
    assert outcomes["yes"] == pytest.approx(1.92)
    assert outcomes["no"] == pytest.approx(1.90)


def test_parse_match_odds_bookmaker():
    rows = parse_match_odds_event(_SAMPLE_EVENT)
    assert all(r["bookmaker"] == "betfair" for r in rows)


def test_parse_match_odds_skips_unknown_bookmaker():
    event = {
        **_SAMPLE_EVENT,
        "bookmakers": [{"key": "winamax", "markets": []}]
    }
    rows = parse_match_odds_event(event)
    assert rows == []
```

- [ ] **Step 2: Vérifier que les tests échouent**

```bash
cd backend && uv run pytest tests/test_match_odds_ingestion.py -k "parse" -v
```
Expected: FAIL avec ImportError

- [ ] **Step 3: Créer `backend/app/ingestion/match_odds.py`**

```python
# backend/app/ingestion/match_odds.py
"""Match-level odds ingestion (h2h / totals / btts) from The Odds API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings
from app.ingestion.odds import ODDS_API_BASE, SPORT_KEYS, OddsAPIClient

logger = logging.getLogger(__name__)

# Bookmakers priority: betfair first (exchange = no overround), pinnacle fallback
MATCH_BOOKMAKERS = {"betfair", "pinnacle"}

# Markets to fetch for market-implied xG
MATCH_MARKET_KEYS = "h2h,totals,both_teams_to_score"


@dataclass
class MatchOddsRow:
    """A single outcome row ready to insert into match_odds_snapshots."""

    bookmaker: str
    market_type: str  # 'h2h' | 'totals' | 'btts'
    outcome: str       # 'home' | 'draw' | 'away' | 'over_2.5' | 'under_2.5' | 'yes' | 'no'
    odds: float
    snapshot_utc: datetime = field(default_factory=lambda: datetime.now(UTC))


def parse_match_odds_event(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse a single The Odds API event dict into flat outcome rows.

    Args:
        event: Raw event dict from The Odds API (includes 'bookmakers' key).

    Returns:
        List of dicts with keys: bookmaker, market_type, outcome, odds.
        Only includes bookmakers in MATCH_BOOKMAKERS.
        For totals, only includes the 2.5-point line.
    """
    home_team = event.get("home_team", "")
    rows: list[dict[str, Any]] = []

    for bm in event.get("bookmakers", []):
        bm_key = bm.get("key", "")
        if bm_key not in MATCH_BOOKMAKERS:
            continue

        for market in bm.get("markets", []):
            mkey = market.get("key", "")

            if mkey == "h2h":
                for oc in market.get("outcomes", []):
                    name = oc.get("name", "")
                    price = oc.get("price")
                    if price is None:
                        continue
                    if name == "Draw":
                        outcome = "draw"
                    elif name == home_team:
                        outcome = "home"
                    else:
                        outcome = "away"
                    rows.append({
                        "bookmaker": bm_key,
                        "market_type": "h2h",
                        "outcome": outcome,
                        "odds": float(price),
                    })

            elif mkey == "totals":
                for oc in market.get("outcomes", []):
                    point = oc.get("point")
                    if point != 2.5:
                        continue
                    name = oc.get("name", "").lower()
                    price = oc.get("price")
                    if price is None:
                        continue
                    outcome = "over_2.5" if name == "over" else "under_2.5"
                    rows.append({
                        "bookmaker": bm_key,
                        "market_type": "totals",
                        "outcome": outcome,
                        "odds": float(price),
                    })

            elif mkey == "both_teams_to_score":
                for oc in market.get("outcomes", []):
                    name = oc.get("name", "").lower()
                    price = oc.get("price")
                    if price is None or name not in ("yes", "no"):
                        continue
                    rows.append({
                        "bookmaker": bm_key,
                        "market_type": "btts",
                        "outcome": name,
                        "odds": float(price),
                    })

    return rows


async def ingest_match_odds_for_league(
    league: str,
    api_key: str | None = None,
) -> tuple[list[MatchOddsRow], list[dict[str, Any]]]:
    """Fetch and parse match-level odds for all upcoming events in a league.

    Returns:
        (rows, events) — rows ready for DB insert, raw events for fixture matching.
    """
    client = OddsAPIClient(api_key)
    sport_key = SPORT_KEYS.get(league)
    if not sport_key:
        logger.warning("ingest_match_odds_for_league: unknown league %s", league)
        return [], []

    events = await client.get_events(sport_key)
    now = datetime.now(UTC)
    all_rows: list[MatchOddsRow] = []

    async with httpx.AsyncClient() as http:
        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue
            try:
                # Respect quota guard before each call
                client._check_quota()

                response = await http.get(
                    f"{ODDS_API_BASE}/sports/{sport_key}/events/{event_id}/odds",
                    params={
                        "apiKey": client.api_key,
                        "markets": MATCH_MARKET_KEYS,
                        "regions": "eu,uk",
                        "bookmakers": ",".join(MATCH_BOOKMAKERS),
                    },
                    timeout=30.0,
                )

                # Update quota counter from response headers
                client._update_quota(response)

                if response.status_code != 200:
                    logger.warning(
                        "Match odds fetch failed for %s: HTTP %d",
                        event_id,
                        response.status_code,
                    )
                    continue
                data = response.json()
                # Merge bookmakers from response into event dict for parsing
                full_event = {**event, "bookmakers": data.get("bookmakers", [])}
                parsed = parse_match_odds_event(full_event)
                for row_dict in parsed:
                    all_rows.append(
                        MatchOddsRow(
                            event_id=event_id,
                            bookmaker=row_dict["bookmaker"],
                            market_type=row_dict["market_type"],
                            outcome=row_dict["outcome"],
                            odds=row_dict["odds"],
                            snapshot_utc=now,
                        )
                    )
            except Exception as exc:
                logger.warning("Error fetching match odds for event %s: %s", event_id, exc)

    # Note: get_events() returns only upcoming fixtures by default (Odds API behaviour).
    # No explicit 7-day filter needed here — the API only surfaces upcoming events.
    return all_rows, events
```

- [ ] **Step 4: Ajouter `import pytest` en tête du fichier de test**

Vérifier que `import pytest` est présent en ligne 1 de `tests/test_match_odds_ingestion.py`.

- [ ] **Step 5: Vérifier que les tests passent**

```bash
cd backend && uv run pytest tests/test_match_odds_ingestion.py -v
```
Expected: 6 PASS (test_match_odds_snapshot_model_importable + 5 parse tests — le test ingest sera vérifié après Task 3 Step 4)

- [ ] **Step 6: Lint**

```bash
cd backend && uv run ruff check app/ingestion/match_odds.py
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/ingestion/match_odds.py backend/tests/test_match_odds_ingestion.py
git commit -m "feat: add match odds ingestion for h2h/totals/btts markets"
```

---

### Task 3: Intégrer l'ingestion dans `job_snapshot_odds`

**Files:**
- Modify: `backend/app/worker.py`
- Modify: `backend/tests/test_match_odds_ingestion.py`

Ajouter un appel à `ingest_match_odds_for_league` dans `job_snapshot_odds`, après la boucle des player props existante. Stocker les résultats dans `match_odds_snapshots` via SQLAlchemy.

- [ ] **Step 1: Écrire un test pour `ingest_match_odds_for_league`**

```python
# Ajouter à backend/tests/test_match_odds_ingestion.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_ingest_match_odds_returns_rows_with_event_id():
    """ingest_match_odds_for_league doit retourner des MatchOddsRow avec event_id."""
    from app.ingestion.match_odds import ingest_match_odds_for_league

    fake_events = [{"id": "evt_42", "home_team": "PSG", "away_team": "Lyon"}]
    fake_response_data = {
        "bookmakers": [{
            "key": "betfair",
            "markets": [{
                "key": "totals",
                "outcomes": [
                    {"name": "Over", "point": 2.5, "price": 1.85},
                    {"name": "Under", "point": 2.5, "price": 2.00},
                ],
            }]
        }]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_response_data
    mock_resp.headers = {}

    with patch("app.ingestion.match_odds.OddsAPIClient") as MockClient:
        instance = MockClient.return_value
        instance.get_events = AsyncMock(return_value=fake_events)
        instance.api_key = "test_key"
        instance._check_quota = MagicMock()
        instance._update_quota = MagicMock()

        with patch("httpx.AsyncClient") as MockHttp:
            mock_http_instance = AsyncMock()
            mock_http_instance.get = AsyncMock(return_value=mock_resp)
            MockHttp.return_value.__aenter__ = AsyncMock(return_value=mock_http_instance)
            MockHttp.return_value.__aexit__ = AsyncMock(return_value=False)

            rows, events = await ingest_match_odds_for_league("ligue_1")

    assert len(rows) == 2  # over_2.5 + under_2.5
    assert all(r.event_id == "evt_42" for r in rows)
    assert {r.outcome for r in rows} == {"over_2.5", "under_2.5"}
    assert len(events) == 1
```

- [ ] **Step 1b: Vérifier que le test échoue**

```bash
cd backend && uv run pytest tests/test_match_odds_ingestion.py::test_ingest_match_odds_returns_rows_with_event_id -v
```
Expected: FAIL (MatchOddsRow n'a pas de champ `event_id` à ce stade)

- [ ] **Step 2: Ajouter l'import en tête de `worker.py`**

Après `from app.ingestion.odds import fetch_events_for_league, ingest_odds_for_league, normalize_league_key`, ajouter :

```python
from app.ingestion.match_odds import ingest_match_odds_for_league
from app.models.match_odds import MatchOddsSnapshot
```

- [ ] **Step 3: Placeholder — ne pas commit**

> ⚠️ **NE PAS COMMIT à cette étape.** On corrige `MatchOddsRow` en Step 4 avant de finaliser le worker.

Ajouter les imports en tête de `worker.py` seulement (pas de logique encore) :

```python
from app.ingestion.match_odds import ingest_match_odds_for_league
from app.models.match_odds import MatchOddsSnapshot
```

Note : `MatchOddsRow` ne porte pas encore `event_id` → on le corrige au Step 4 dans `match_odds.py` avant d'écrire le bloc worker.

- [ ] **Step 4: Modifier `MatchOddsRow` pour porter `event_id`**

Dans `backend/app/ingestion/match_odds.py`, modifier le dataclass :

```python
@dataclass
class MatchOddsRow:
    event_id: str  # Odds API event ID for fixture matching
    bookmaker: str
    market_type: str
    outcome: str
    odds: float
    snapshot_utc: datetime = field(default_factory=lambda: datetime.now(UTC))
```

Et dans `ingest_match_odds_for_league`, mettre à jour la création des rows :

```python
                all_rows.append(
                    MatchOddsRow(
                        event_id=event_id,  # ADD THIS
                        bookmaker=row_dict["bookmaker"],
                        market_type=row_dict["market_type"],
                        outcome=row_dict["outcome"],
                        odds=row_dict["odds"],
                        snapshot_utc=now,
                    )
                )
```

- [ ] **Step 5: Vérifier que tous les tests du fichier passent**

```bash
cd backend && uv run pytest tests/test_match_odds_ingestion.py -v
```
Expected: 7 PASS (6 tests Task 1+2 + le test ingest_match_odds_for_league de Task 3 Step 1)

- [ ] **Step 6: Écrire le vrai bloc job dans `worker.py`**

Remplacer le bloc ajouté au Step 3 (avec le `pass`) par :

```python
    # ── Match-level odds (h2h / totals / btts) for market-implied xG ──
    logger.info("Fetching match-level odds for market xG...")
    for league in leagues:
        try:
            match_rows, match_events = await ingest_match_odds_for_league(league)
            if not match_rows:
                continue

            from app.ingestion.fixture_matcher import match_odds_event_to_fixture

            async with async_session() as session:
                from app.models.fixtures import Fixture
                db_result = await session.execute(
                    select(Fixture).where(Fixture.league == league)
                )
                db_fixtures = list(db_result.scalars().all())

                # Index raw events by id for fixture matching
                events_by_id = {e["id"]: e for e in match_events if e.get("id")}

                # Group rows by event_id
                rows_by_event: dict[str, list] = {}
                for row in match_rows:
                    rows_by_event.setdefault(row.event_id, []).append(row)

                stored = 0
                for event_id, rows in rows_by_event.items():
                    raw_event = events_by_id.get(event_id)
                    if not raw_event:
                        continue
                    fixture = match_odds_event_to_fixture(raw_event, db_fixtures)
                    if not fixture:
                        continue

                    for row in rows:
                        snap = MatchOddsSnapshot(
                            fixture_id=fixture.id,
                            bookmaker=row.bookmaker,
                            market_type=row.market_type,
                            outcome=row.outcome,
                            odds=row.odds,
                            snapshot_utc=row.snapshot_utc,
                        )
                        try:
                            session.add(snap)
                            await session.flush()
                            stored += 1
                        except Exception:
                            await session.rollback()
                            # Known limitation: rollback drops previously flushed rows
                            # for this session. Matches existing job_snapshot_odds pattern.
                            # Future improvement: use savepoints (session.begin_nested()).
                            continue

                await session.commit()
                logger.info(
                    "Stored %d match odds rows for %s", stored, league
                )

        except Exception as exc:
            logger.error("Match odds ingestion failed for league %s: %s", league, exc)
```

- [ ] **Step 7: Vérifier que les tests existants passent toujours**

```bash
cd backend && uv run pytest tests/ -x -q --ignore=tests/test_market_xg_solvers.py --ignore=tests/test_market_xg_integration.py
```
Expected: aucun test cassé

- [ ] **Step 8: Lint**

```bash
cd backend && uv run ruff check app/worker.py app/ingestion/match_odds.py
```

- [ ] **Step 9: Commit**

```bash
git add backend/app/worker.py backend/app/ingestion/match_odds.py
git commit -m "feat: add match odds ingestion to job_snapshot_odds"
```

---

## Chunk 2: MarketXgService + Intégration Pricing

### Task 4: Solvers (Over 2.5, BTTS, cross-validation)

**Files:**
- Create: `backend/app/services/market_xg.py`
- Create: `backend/tests/test_market_xg_solvers.py`

Les valeurs de référence pour les tests :

| Input | Calcul | Résultat attendu |
|-------|--------|-----------------|
| λt = 2.5 | P(X≥3) = 1 - e^(-2.5)(1 + 2.5 + 3.125) = 0.4562 | solve_lambda_t(0.4562) ≈ 2.5 |
| λh = 1.3, λa = 1.2 | P(BTTS) = (1-e^(-1.3))(1-e^(-1.2)) = 0.5084 | solve_lambda_home(2.5, 0.5084, is_home_stronger=True) ≈ 1.3 (home stronger = larger λh) |

- [ ] **Step 1: Écrire les tests des solvers**

```python
# backend/tests/test_market_xg_solvers.py
import math

import pytest


def test_solve_lambda_t_known_value():
    """λt=2.5 → P(total ≥ 3)=0.4562, solver doit retrouver λt≈2.5."""
    from app.services.market_xg import solve_lambda_t

    p_over = 1 - math.exp(-2.5) * (1 + 2.5 + 2.5**2 / 2)  # ≈ 0.4562
    result = solve_lambda_t(p_over)
    assert result == pytest.approx(2.5, abs=1e-4)


def test_solve_lambda_t_low_scoring():
    """Match défensif λt≈1.0 → P(total ≥ 3)≈0.0803."""
    from app.services.market_xg import solve_lambda_t

    p_over = 1 - math.exp(-1.0) * (1 + 1.0 + 0.5)  # ≈ 0.0803
    result = solve_lambda_t(p_over)
    assert result == pytest.approx(1.0, abs=1e-4)


def test_solve_lambda_home_home_stronger():
    """λh=1.2, λa=1.3 (away plus fort), is_home_stronger=False → λh≈1.2."""
    from app.services.market_xg import solve_lambda_home

    lambda_t = 2.5
    p_btts = (1 - math.exp(-1.2)) * (1 - math.exp(-1.3))  # ≈ 0.5084
    result = solve_lambda_home(lambda_t, p_btts, is_home_stronger=False)
    assert result == pytest.approx(1.2, abs=1e-4)


def test_solve_lambda_home_away_stronger():
    """λh=1.3, λa=1.2 (home plus fort), is_home_stronger=True → λh≈1.3."""
    from app.services.market_xg import solve_lambda_home

    lambda_t = 2.5
    p_btts = (1 - math.exp(-1.3)) * (1 - math.exp(-1.2))  # même valeur, symétrie
    result = solve_lambda_home(lambda_t, p_btts, is_home_stronger=True)
    assert result == pytest.approx(1.3, abs=1e-4)


def test_solve_lambda_t_invalid_probability():
    """P ≤ 0 ou ≥ 1 → ValueError."""
    from app.services.market_xg import solve_lambda_t

    with pytest.raises(ValueError):
        solve_lambda_t(0.0)
    with pytest.raises(ValueError):
        solve_lambda_t(1.0)


def test_cross_validate_passes():
    """Valeurs cohérentes → aucun flag."""
    from app.services.market_xg import cross_validate

    lambda_h, lambda_a = 1.2, 1.3
    p_over_market = 1 - math.exp(-2.5) * (1 + 2.5 + 2.5**2 / 2)
    p_btts_market = (1 - math.exp(-1.2)) * (1 - math.exp(-1.3))
    result = cross_validate(lambda_h, lambda_a, p_over_market, p_btts_market)
    assert result is None  # None = pas de flag


def test_cross_validate_flags_when_over_discrepancy():
    """Écart >8% sur Over 2.5 → flag avec raison."""
    from app.services.market_xg import cross_validate

    lambda_h, lambda_a = 1.2, 1.3
    p_over_market = 0.9  # volontairement faux
    p_btts_market = (1 - math.exp(-1.2)) * (1 - math.exp(-1.3))
    result = cross_validate(lambda_h, lambda_a, p_over_market, p_btts_market)
    assert result is not None
    assert "over_2.5" in result.lower()
```

- [ ] **Step 2: Vérifier que les tests échouent**

```bash
cd backend && uv run pytest tests/test_market_xg_solvers.py -v
```
Expected: FAIL avec ImportError

- [ ] **Step 3: Créer `backend/app/services/market_xg.py` — solvers uniquement**

```python
# backend/app/services/market_xg.py
"""Market-implied xG service.

Computes (xg_home, xg_away) from betting market odds using:
  1. Match Totals Over 2.5 → λt = λh + λa
  2. BTTS Yes → separate λh from λa given λt
  3. H2H → resolve which team has larger xG
  4. Cross-validation: compare predicted vs market probabilities (>8% gap = flag)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from scipy.optimize import brentq
from sqlalchemy import desc, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ── Constants ──────────────────────────────────────────────────────

CROSS_VAL_THRESHOLD = 0.08  # flag if predicted vs market > 8% absolute gap
MAX_SNAPSHOT_AGE_HOURS = 24


# ── Dataclass ──────────────────────────────────────────────────────

@dataclass
class MarketXgResult:
    xg_home: float
    xg_away: float
    xg_source: Literal["market_implied", "market_implied_flagged", "dixon_coles"]
    flagged_reason: str | None = None


# ── Devigging ──────────────────────────────────────────────────────

def multiplicative_devig(odds: list[float]) -> list[float]:
    """Remove bookmaker margin multiplicatively.

    P_true(i) = (1/O_i) / Σ(1/O_j)
    """
    if not odds or any(o <= 0 for o in odds):
        raise ValueError(f"Invalid odds: {odds}")
    total = sum(1 / o for o in odds)
    return [(1 / o) / total for o in odds]


# ── Solvers ────────────────────────────────────────────────────────

def solve_lambda_t(p_over_2_5: float) -> float:
    """Solve λt such that P(Poisson(λt) ≥ 3) = p_over_2_5.

    Uses Poisson CDF: P(X ≤ 2) = e^(-λ)(1 + λ + λ²/2)
    So P(X ≥ 3) = 1 - e^(-λ)(1 + λ + λ²/2)

    Args:
        p_over_2_5: True probability (post-devig) that total goals ≥ 3.

    Returns:
        λt (xG total for the match).

    Raises:
        ValueError: if p_over_2_5 not in (0, 1) or solver fails.
    """
    if not 0 < p_over_2_5 < 1:
        raise ValueError(f"p_over_2_5 must be in (0, 1), got {p_over_2_5}")

    def f(lam: float) -> float:
        return (1 - math.exp(-lam) * (1 + lam + lam**2 / 2)) - p_over_2_5

    return brentq(f, 0.01, 15.0, xtol=1e-6)


def solve_lambda_home(
    lambda_t: float,
    p_btts: float,
    is_home_stronger: bool,
) -> float:
    """Solve λh given λt and P(BTTS Yes).

    P(BTTS) = (1 - e^(-λh)) × (1 - e^(-(λt - λh)))
    Two symmetric solutions exist. Use is_home_stronger to pick the right one.

    Args:
        lambda_t: Total match xG (λh + λa).
        p_btts: True probability (post-devig) of both teams scoring.
        is_home_stronger: True if home team has higher P(win) than away.

    Returns:
        λh (home team xG).

    Raises:
        ValueError: if no root found in bracket (degenerate case).
    """
    if not 0 < p_btts < 1:
        raise ValueError(f"p_btts must be in (0, 1), got {p_btts}")

    lo = 0.05
    hi = lambda_t - 0.05

    if lo >= hi:
        raise ValueError(f"lambda_t={lambda_t} too small to split meaningfully")

    def f(lh: float) -> float:
        la = lambda_t - lh
        return (1 - math.exp(-lh)) * (1 - math.exp(-la)) - p_btts

    # Check if a root exists in [lo, hi]
    if f(lo) * f(hi) > 0:
        raise ValueError(
            f"BTTS solver: no root in [{lo}, {hi}] for λt={lambda_t}, p_btts={p_btts}"
        )

    # brentq gives the root closest to lo (i.e., the smaller λh)
    lh_small = brentq(f, lo, hi, xtol=1e-6)
    lh_large = lambda_t - lh_small

    return lh_large if is_home_stronger else lh_small


# ── Cross-validation ───────────────────────────────────────────────

def _poisson_draw_prob(lambda_h: float, lambda_a: float, k_max: int = 5) -> float:
    """P(X_h == X_a) for independent Poisson(λh) and Poisson(λa).

    Truncated at k_max (error < 0.1% for λ < 3).
    """
    total = 0.0
    for k in range(k_max + 1):
        p_h = math.exp(-lambda_h) * (lambda_h**k) / math.factorial(k)
        p_a = math.exp(-lambda_a) * (lambda_a**k) / math.factorial(k)
        total += p_h * p_a
    return total


def cross_validate(
    lambda_h: float,
    lambda_a: float,
    p_over_market: float,
    p_btts_market: float,
) -> str | None:
    """Compare predicted probabilities vs market. Return flag reason or None.

    Checks Over 2.5 and BTTS. If either exceeds CROSS_VAL_THRESHOLD (8%),
    returns a description of the discrepancy.
    """
    lambda_t = lambda_h + lambda_a

    p_over_pred = 1 - math.exp(-lambda_t) * (1 + lambda_t + lambda_t**2 / 2)
    p_btts_pred = (1 - math.exp(-lambda_h)) * (1 - math.exp(-lambda_a))

    reasons = []

    if abs(p_over_pred - p_over_market) > CROSS_VAL_THRESHOLD:
        reasons.append(
            f"over_2.5: predicted={p_over_pred:.3f} market={p_over_market:.3f} "
            f"(gap={abs(p_over_pred - p_over_market):.3f})"
        )

    if abs(p_btts_pred - p_btts_market) > CROSS_VAL_THRESHOLD:
        reasons.append(
            f"btts: predicted={p_btts_pred:.3f} market={p_btts_market:.3f} "
            f"(gap={abs(p_btts_pred - p_btts_market):.3f})"
        )

    return "; ".join(reasons) if reasons else None
```

- [ ] **Step 4: Vérifier que les tests passent**

```bash
cd backend && uv run pytest tests/test_market_xg_solvers.py -v
```
Expected: 8 PASS

- [ ] **Step 5: Lint**

```bash
cd backend && uv run ruff check app/services/market_xg.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/market_xg.py backend/tests/test_market_xg_solvers.py
git commit -m "feat: add market xG solvers (Over 2.5, BTTS, cross-validation)"
```

---

### Task 5: `MarketXgService.compute()` — pipeline complet avec DB

**Files:**
- Modify: `backend/app/services/market_xg.py` (ajouter la classe)
- Modify: `backend/tests/test_market_xg_solvers.py` (ajouter tests compute)

- [ ] **Step 1: Écrire les tests de `compute()`**

```python
# Ajouter à backend/tests/test_market_xg_solvers.py
import math
from unittest.mock import AsyncMock, MagicMock, patch


def _make_snap(market_type: str, outcome: str, odds: float, hours_ago: float = 1.0):
    """Helper: create a mock MatchOddsSnapshot."""
    from datetime import UTC, datetime, timedelta
    snap = MagicMock()
    snap.market_type = market_type
    snap.outcome = outcome
    snap.odds = odds
    snap.snapshot_utc = datetime.now(UTC) - timedelta(hours=hours_ago)
    return snap


def _make_fixture(kickoff_hours: float = 20.0):
    from datetime import UTC, datetime, timedelta
    f = MagicMock()
    f.kickoff_utc = datetime.now(UTC) + timedelta(hours=kickoff_hours)
    return f


@pytest.mark.asyncio
async def test_compute_returns_market_implied():
    """Avec cotes valides → xg_source = 'market_implied'."""
    from app.services.market_xg import MarketXgService

    # λt=2.5, λh≈1.3 (home stronger), λa≈1.2
    p_over = 1 - math.exp(-2.5) * (1 + 2.5 + 2.5**2 / 2)
    p_btts = (1 - math.exp(-1.3)) * (1 - math.exp(-1.2))

    # Devigged: odds are exact (overround = 1.0)
    over_odds = 1 / p_over
    under_odds = 1 / (1 - p_over)
    btts_yes_odds = 1 / p_btts
    btts_no_odds = 1 / (1 - p_btts)
    home_odds = 1 / 0.55   # home stronger
    draw_odds = 1 / 0.25
    away_odds = 1 / 0.20

    snaps = [
        _make_snap("totals", "over_2.5", over_odds),
        _make_snap("totals", "under_2.5", under_odds),
        _make_snap("btts", "yes", btts_yes_odds),
        _make_snap("btts", "no", btts_no_odds),
        _make_snap("h2h", "home", home_odds),
        _make_snap("h2h", "draw", draw_odds),
        _make_snap("h2h", "away", away_odds),
    ]

    mock_session = AsyncMock()
    mock_execute = AsyncMock()
    mock_execute.scalars.return_value.all.return_value = snaps
    mock_session.execute.return_value = mock_execute

    fixture = _make_fixture(kickoff_hours=20.0)

    svc = MarketXgService()
    result = await svc.compute(fixture_id=1, session=mock_session, fixture=fixture, dixon_fallback=(1.4, 1.1))

    assert result.xg_source == "market_implied"
    assert result.xg_home == pytest.approx(1.3, abs=0.05)
    assert result.xg_away == pytest.approx(1.2, abs=0.05)


@pytest.mark.asyncio
async def test_compute_falls_back_on_no_odds():
    """Sans cotes → fallback Dixon-Coles."""
    from app.services.market_xg import MarketXgService

    mock_session = AsyncMock()
    mock_execute = AsyncMock()
    mock_execute.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_execute

    fixture = _make_fixture()

    svc = MarketXgService()
    result = await svc.compute(fixture_id=1, session=mock_session, fixture=fixture, dixon_fallback=(1.4, 1.1))

    assert result.xg_source == "dixon_coles"
    assert result.xg_home == pytest.approx(1.4)
    assert result.xg_away == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_compute_falls_back_on_stale_odds():
    """Cotes trop vieilles (fetched >24h avant kickoff) → fallback Dixon-Coles.

    Spec: "rejeter tout snapshot plus vieux que 24h avant kickoff"
    = kickoff_utc - snapshot_utc > 24h.

    Cas : kickoff dans 2h, snapshot pris il y a 25h → kickoff - snap = 27h > 24h → stale.
    """
    from app.services.market_xg import MarketXgService

    # Snap pris il y a 25h, kickoff dans 2h → gap = 27h > 24h → stale
    snaps = [
        _make_snap("totals", "over_2.5", 1.9, hours_ago=25.0),
    ]

    mock_session = AsyncMock()
    mock_execute = AsyncMock()
    mock_execute.scalars.return_value.all.return_value = snaps
    mock_session.execute.return_value = mock_execute

    fixture = _make_fixture(kickoff_hours=2.0)

    svc = MarketXgService()
    result = await svc.compute(fixture_id=1, session=mock_session, fixture=fixture, dixon_fallback=(1.4, 1.1))

    assert result.xg_source == "dixon_coles"


@pytest.mark.asyncio
async def test_compute_keeps_recent_odds_for_close_kickoff():
    """Snap récent (2h avant maintenant) pour match qui commence dans 3h → pas stale.

    kickoff - snap = 5h < 24h → fraîcheur OK → ne pas fallback pour staleness.
    (Le test vérifie qu'on ne tombe pas en Dixon-Coles à cause de la fraîcheur seule.)
    """
    from app.services.market_xg import MarketXgService

    # Snap pris il y a 2h, kickoff dans 3h → gap = 5h < 24h → frais
    snaps = [
        _make_snap("totals", "over_2.5", 1.85, hours_ago=2.0),
    ]

    mock_session = AsyncMock()
    mock_execute = AsyncMock()
    mock_execute.scalars.return_value.all.return_value = snaps
    mock_session.execute.return_value = mock_execute

    fixture = _make_fixture(kickoff_hours=3.0)

    svc = MarketXgService()
    result = await svc.compute(fixture_id=1, session=mock_session, fixture=fixture, dixon_fallback=(1.4, 1.1))

    # Pas de fallback pour staleness — tombera en Dixon-Coles pour missing under_2.5 à la place
    # Ce test vérifie que la raison n'est PAS "stale"
    assert "stale" not in (result.flagged_reason or "")
```

- [ ] **Step 2: Vérifier que les tests échouent (MarketXgService absent)**

```bash
cd backend && uv run pytest tests/test_market_xg_solvers.py -k "compute" -v
```
Expected: FAIL

- [ ] **Step 3: Ajouter `MarketXgService` à `market_xg.py`**

Ajouter à la fin du fichier `backend/app/services/market_xg.py` :

```python
# ── Service ────────────────────────────────────────────────────────

class MarketXgService:
    """Compute market-implied (xg_home, xg_away) from match_odds_snapshots."""

    async def compute(
        self,
        fixture_id: int,
        session: "AsyncSession",
        fixture: object,
        dixon_fallback: tuple[float, float],
    ) -> MarketXgResult:
        """Full pipeline: fetch odds → devig → solve → cross-validate.

        Args:
            fixture_id: DB fixture ID to look up in match_odds_snapshots.
            session: Async SQLAlchemy session.
            fixture: Fixture ORM object (needs kickoff_utc).
            dixon_fallback: (xg_home, xg_away) from Dixon-Coles, used if no valid odds.

        Returns:
            MarketXgResult with xg_source indicating how the values were computed.
        """
        from app.models.match_odds import MatchOddsSnapshot

        fallback_home, fallback_away = dixon_fallback

        def _dixon_result(reason: str = "") -> MarketXgResult:
            return MarketXgResult(
                xg_home=fallback_home,
                xg_away=fallback_away,
                xg_source="dixon_coles",
                flagged_reason=reason or None,
            )

        # 1. Fetch latest snapshots for this fixture
        result = await session.execute(
            select(MatchOddsSnapshot)
            .where(MatchOddsSnapshot.fixture_id == fixture_id)
            .order_by(desc(MatchOddsSnapshot.snapshot_utc))
        )
        snaps = result.scalars().all()

        if not snaps:
            return _dixon_result("no match odds available")

        # 2. Staleness check: reject if freshest snap was fetched > 24h BEFORE kickoff.
        # Spec: "rejeter tout snapshot plus vieux que 24h avant kickoff".
        # Condition: kickoff_utc - snapshot_utc > 24h (not: now - snapshot_utc > 24h).
        freshest = max(s.snapshot_utc for s in snaps)
        # Make timezone-aware if naive
        if freshest.tzinfo is None:
            freshest = freshest.replace(tzinfo=UTC)
        kickoff = getattr(fixture, "kickoff_utc", None)
        if kickoff is not None:
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=UTC)
            if (kickoff - freshest) > timedelta(hours=MAX_SNAPSHOT_AGE_HOURS):
                return _dixon_result("match odds are stale (fetched > 24h before kickoff)")

        # 3. Select best bookmaker (betfair > pinnacle)
        snap_by_market: dict[str, dict[str, float]] = {}
        for bm_priority in ("betfair", "pinnacle"):
            bm_snaps = [s for s in snaps if s.bookmaker == bm_priority]
            if bm_snaps:
                for s in bm_snaps:
                    snap_by_market.setdefault(s.market_type, {})[s.outcome] = s.odds
                break

        if not snap_by_market:
            return _dixon_result("no supported bookmaker in odds snapshots")

        totals = snap_by_market.get("totals", {})
        btts = snap_by_market.get("btts", {})
        h2h = snap_by_market.get("h2h", {})

        # Need both sides of totals and btts to devig correctly
        if (
            "over_2.5" not in totals
            or "under_2.5" not in totals
            or "yes" not in btts
            or "no" not in btts
        ):
            return _dixon_result("missing totals (over+under) or btts (yes+no) odds")

        # 4. Devig — requires both sides for unbiased multiplicative devig
        try:
            p_over_true = multiplicative_devig([totals["over_2.5"], totals["under_2.5"]])[0]
            p_btts_true = multiplicative_devig([btts["yes"], btts["no"]])[0]
        except ValueError as exc:
            return _dixon_result(f"devig error: {exc}")

        # 5. H2H: determine which team is stronger
        is_home_stronger = True
        if "home" in h2h and "away" in h2h and "draw" in h2h:
            try:
                h2h_probs = multiplicative_devig([h2h["home"], h2h["draw"], h2h["away"]])
                is_home_stronger = h2h_probs[0] > h2h_probs[2]
            except ValueError:
                pass  # keep default True if H2H devig fails

        # 6. Solve λt from Over 2.5
        try:
            lambda_t = solve_lambda_t(p_over_true)
        except (ValueError, Exception) as exc:
            return _dixon_result(f"Over 2.5 solver failed: {exc}")

        # 7. Solve λh from BTTS + H2H sign
        try:
            lambda_h = solve_lambda_home(lambda_t, p_btts_true, is_home_stronger)
        except ValueError as exc:
            return _dixon_result(f"BTTS solver failed: {exc}")

        lambda_a = lambda_t - lambda_h

        # 8. Clamp minimum xG
        lambda_h = max(0.05, lambda_h)
        lambda_a = max(0.05, lambda_a)

        # 9. Cross-validate
        flag_reason = cross_validate(lambda_h, lambda_a, p_over_true, p_btts_true)

        return MarketXgResult(
            xg_home=round(lambda_h, 3),
            xg_away=round(lambda_a, 3),
            xg_source="market_implied_flagged" if flag_reason else "market_implied",
            flagged_reason=flag_reason,
        )
```

- [ ] **Step 4: Vérifier que les tests passent**

```bash
cd backend && uv run pytest tests/test_market_xg_solvers.py -v
```
Expected: tous PASS (11+ tests)

- [ ] **Step 5: Lint**

```bash
cd backend && uv run ruff check app/services/market_xg.py
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/market_xg.py backend/tests/test_market_xg_solvers.py
git commit -m "feat: add MarketXgService.compute() with DB query and fallback"
```

---

### Task 6: Intégration dans le pipeline de pricing

**Files:**
- Modify: `backend/app/pricing/team_xg.py` (MatchPricingResult + load_match_pricing)
- Modify: `backend/app/api/pricing.py` (MatchPriceResponse)
- Create: `backend/tests/test_market_xg_integration.py`

- [ ] **Step 1: Écrire les tests d'intégration**

```python
# backend/tests/test_market_xg_integration.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_match_pricing_result_has_xg_source():
    """MatchPricingResult doit avoir un champ xg_source."""
    from app.pricing.team_xg import MatchPricingResult
    r = MatchPricingResult(
        fixture_id=1,
        home_team="PSG",
        away_team="Lyon",
        home_match_xg=1.3,
        away_match_xg=1.1,
        xg_source="market_implied",
    )
    assert r.xg_source == "market_implied"


def test_match_pricing_result_default_xg_source():
    """xg_source défaut = 'dixon_coles'."""
    from app.pricing.team_xg import MatchPricingResult
    r = MatchPricingResult(
        fixture_id=1,
        home_team="PSG",
        away_team="Lyon",
        home_match_xg=1.3,
        away_match_xg=1.1,
    )
    assert r.xg_source == "dixon_coles"


def test_match_price_response_has_xg_source():
    """MatchPriceResponse (Pydantic) doit exposer xg_source."""
    from app.api.pricing import MatchPriceResponse
    r = MatchPriceResponse(
        fixture_id=1,
        home_team="PSG",
        away_team="Lyon",
        home_match_xg=1.3,
        away_match_xg=1.1,
        home_players=[],
        away_players=[],
        xg_source="market_implied",
    )
    assert r.xg_source == "market_implied"


@pytest.mark.asyncio
async def test_load_match_pricing_uses_market_xg():
    """load_match_pricing appelle MarketXgService et utilise ses valeurs."""
    from app.services.market_xg import MarketXgResult

    market_result = MarketXgResult(
        xg_home=1.5,
        xg_away=0.9,
        xg_source="market_implied",
    )

    with patch(
        "app.pricing.team_xg.MarketXgService"
    ) as MockSvc:
        instance = MockSvc.return_value
        instance.compute = AsyncMock(return_value=market_result)

        with patch("app.pricing.team_xg.compute_team_stats", new_callable=AsyncMock) as mock_stats:
            mock_stats.return_value = {}

            with patch("app.pricing.team_xg._load_team_players", new_callable=AsyncMock) as mock_players:
                mock_players.return_value = []

                from app.pricing.team_xg import load_match_pricing

                fixture = MagicMock()
                fixture.id = 42
                fixture.home_team = "PSG"
                fixture.away_team = "Lyon"

                db = AsyncMock()

                result = await load_match_pricing(db, fixture)

    assert result.home_match_xg == pytest.approx(1.5)
    assert result.away_match_xg == pytest.approx(0.9)
    assert result.xg_source == "market_implied"
```

- [ ] **Step 2: Vérifier que les tests échouent**

```bash
cd backend && uv run pytest tests/test_market_xg_integration.py -v
```
Expected: `test_match_pricing_result_has_xg_source` FAIL (champ absent)

- [ ] **Step 3: Ajouter `xg_source` à `MatchPricingResult`**

Dans `backend/app/pricing/team_xg.py`, modifier le dataclass `MatchPricingResult` (ligne ~138) :

```python
@dataclass
class MatchPricingResult:
    fixture_id: int
    home_team: str
    away_team: str
    home_match_xg: float
    away_match_xg: float
    home_players: list[PlayerAllocation] = field(default_factory=list)
    away_players: list[PlayerAllocation] = field(default_factory=list)
    home_lineup_players: list[PlayerAllocation] | None = None
    away_lineup_players: list[PlayerAllocation] | None = None
    xg_source: str = "dixon_coles"  # ADD THIS
```

- [ ] **Step 4: Ajouter `xg_source` à `MatchPriceResponse`**

Dans `backend/app/api/pricing.py`, modifier `MatchPriceResponse` (ligne ~114) :

```python
class MatchPriceResponse(BaseModel):
    fixture_id: int
    home_team: str
    away_team: str
    home_match_xg: float
    away_match_xg: float
    home_players: list[PlayerAllocationOut]
    away_players: list[PlayerAllocationOut]
    home_lineup_players: list[PlayerAllocationOut] | None = None
    away_lineup_players: list[PlayerAllocationOut] | None = None
    xg_source: str = "dixon_coles"  # ADD THIS
```

- [ ] **Step 5: Intégrer `MarketXgService` dans `load_match_pricing()`**

Dans `backend/app/pricing/team_xg.py`, modifier la fonction `load_match_pricing` :

1. Ajouter l'import en tête du fichier (après les imports existants) :
```python
from app.services.market_xg import MarketXgService
```

2. Dans le corps de `load_match_pricing`, remplacer le bloc qui calcule `home_match_xg` et `away_match_xg` via Dixon-Coles (lignes ~536-556) par :

```python
    # ── Stage 1: Team xG ───────────────────────────────────────────
    # Compute Dixon-Coles fallback values first
    all_ts = list(team_stats.values())
    league_avg_xg = (
        sum(ts.attack_xg_per_match for ts in all_ts) / len(all_ts) if all_ts else 1.2
    )
    xga_values = [ts.defense_xga_per_match for ts in all_ts if ts.defense_xga_per_match > 0]
    league_avg_xga = sum(xga_values) / len(xga_values) if xga_values else league_avg_xg

    if home_xg_override is not None:
        dixon_home = home_xg_override
    elif home_ts:
        dixon_home = estimate_team_match_xg(
            home_ts.attack_xg_per_match,
            away_ts.defense_xga_per_match if away_ts else league_avg_xga,
            league_avg_xg, league_avg_xga, is_home=True,
        )
    else:
        dixon_home = league_avg_xg * HOME_ADVANTAGE

    if away_xg_override is not None:
        dixon_away = away_xg_override
    elif away_ts:
        dixon_away = estimate_team_match_xg(
            away_ts.attack_xg_per_match,
            home_ts.defense_xga_per_match if home_ts else league_avg_xga,
            league_avg_xg, league_avg_xga, is_home=False,
        )
    else:
        dixon_away = league_avg_xg

    # Try market-implied xG (only when no override is supplied)
    xg_source = "dixon_coles"
    home_match_xg = dixon_home
    away_match_xg = dixon_away

    if home_xg_override is None and away_xg_override is None:
        try:
            market_result = await MarketXgService().compute(
                fixture_id=fixture.id,
                session=db,
                fixture=fixture,
                dixon_fallback=(dixon_home, dixon_away),
            )
            home_match_xg = market_result.xg_home
            away_match_xg = market_result.xg_away
            xg_source = market_result.xg_source
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "MarketXgService failed for fixture %d, using Dixon-Coles: %s",
                fixture.id,
                exc,
            )
```

3. Mettre à jour le `return` final pour inclure `xg_source` :

```python
    return MatchPricingResult(
        fixture_id=fixture.id,
        home_team=home_team,
        away_team=away_team,
        home_match_xg=round(home_match_xg, 3),
        away_match_xg=round(away_match_xg, 3),
        home_players=home_allocs,
        away_players=away_allocs,
        home_lineup_players=home_lineup or None,
        away_lineup_players=away_lineup or None,
        xg_source=xg_source,  # ADD THIS
    )
```

- [ ] **Step 6: Propager `xg_source` dans `price_match` (API)**

Dans `backend/app/api/pricing.py`, dans la fonction `price_match`, modifier le `return` :

```python
    return MatchPriceResponse(
        fixture_id=pricing.fixture_id,
        home_team=pricing.home_team,
        away_team=pricing.away_team,
        home_match_xg=pricing.home_match_xg,
        away_match_xg=pricing.away_match_xg,
        home_players=_to_out(pricing.home_players),
        away_players=_to_out(pricing.away_players),
        home_lineup_players=_to_out(pricing.home_lineup_players) if pricing.home_lineup_players else None,
        away_lineup_players=_to_out(pricing.away_lineup_players) if pricing.away_lineup_players else None,
        xg_source=pricing.xg_source,  # ADD THIS
    )
```

- [ ] **Step 7: Vérifier que les tests passent**

```bash
cd backend && uv run pytest tests/test_market_xg_integration.py -v
```
Expected: 4 PASS

- [ ] **Step 8: Vérifier que la suite de tests complète passe**

```bash
cd backend && uv run pytest tests/ -x -q
```
Expected: aucun test cassé

- [ ] **Step 9: Lint**

```bash
cd backend && uv run ruff check app/pricing/team_xg.py app/api/pricing.py
```

- [ ] **Step 10: Mettre à jour la doc**

Dans `docs/user-guide/01-how-ev0-works.md`, ajouter une section ou mettre à jour la section sur le pricing du match pour mentionner que les xG sont désormais calculés depuis les cotes du marché (Over 2.5 + BTTS + H2H) avec fallback Dixon-Coles si les cotes ne sont pas disponibles.

- [ ] **Step 11: Commit final**

```bash
git add backend/app/pricing/team_xg.py backend/app/api/pricing.py backend/tests/test_market_xg_integration.py docs/user-guide/01-how-ev0-works.md
git commit -m "feat: integrate MarketXgService into match pricing pipeline"
```
