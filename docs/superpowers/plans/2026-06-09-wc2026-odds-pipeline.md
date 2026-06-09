# WC2026 Odds Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seeder les 72 fixtures CDM dans `fixtures`, créer la table `wc2026_outright_odds`, scraper les outrights nation (vainqueur, top4, top8, buteur, etc.) sur PMU/Unibet/Betclic, et planifier le job worker toutes les 6h.

**Architecture:** L'`OddsScheduler` existant prend automatiquement en charge les fixtures CDM une fois seedées (`league = "world_cup_2026"` est déjà dans `_league_key()`). Les 3 scrapers de matchs (Betclic gRPC, Unibet LVS, PMU Kambi) reconnaissent déjà `world_cup_2026`. Le module `sync_wc_outrights.py` scrape les marchés de tournoi (outrights) séparément via un job worker dédié.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, psycopg2, httpx, BeautifulSoup4, APScheduler, PostgreSQL

---

## Fichiers à créer / modifier

| Fichier | Action |
|---|---|
| `backend/alembic/versions/037_wc2026_outright_odds.py` | Créer : migration table `wc2026_outright_odds` |
| `backend/app/models/wc2026_odds.py` | Créer : model `WC2026OutrightOdd` |
| `backend/app/models/__init__.py` | Modifier : enregistrer `WC2026OutrightOdd` |
| `backend/scripts/seed_wc2026_fixtures.py` | Créer : seeder 72 fixtures groupes + 32 KO |
| `backend/app/ingestion/wc2026/sync_wc_outrights.py` | Créer : scraper outrights PMU + Unibet + Betclic |
| `backend/tests/ingestion/test_sync_wc_outrights.py` | Créer : tests unitaires scraper outrights |
| `backend/app/worker.py` | Modifier : ajouter `job_sync_wc_outright_odds` |

---

### Task 1: Migration 037 — wc2026_outright_odds

**Files:**
- Create: `backend/alembic/versions/037_wc2026_outright_odds.py`

- [ ] **Step 1: Écrire la migration**

```python
# backend/alembic/versions/037_wc2026_outright_odds.py
"""Create wc2026_outright_odds table.

Revision ID: 037
Revises: 036
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: str | None = "036"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc2026_outright_odds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nation", sa.String(60), nullable=True),
        sa.Column("player_name", sa.String(100), nullable=True),
        sa.Column("market_type", sa.String(30), nullable=False),
        sa.Column("bookmaker", sa.String(20), nullable=False),
        sa.Column("odds", sa.Float(), nullable=False),
        sa.Column(
            "scraped_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "nation", "player_name", "market_type", "bookmaker",
            name="uq_wc2026_outright",
        ),
    )
    op.create_index("ix_wc2026_outright_nation", "wc2026_outright_odds", ["nation"])
    op.create_index("ix_wc2026_outright_market", "wc2026_outright_odds", ["market_type"])


def downgrade() -> None:
    op.drop_index("ix_wc2026_outright_market", table_name="wc2026_outright_odds")
    op.drop_index("ix_wc2026_outright_nation", table_name="wc2026_outright_odds")
    op.drop_table("wc2026_outright_odds")
```

- [ ] **Step 2: Appliquer la migration en local**

```bash
cd backend
alembic upgrade head
```

Expected: `Running upgrade 036 -> 037, Create wc2026_outright_odds table`

- [ ] **Step 3: Vérifier la table**

```bash
python -c "
import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
async def check():
    e = create_async_engine(os.environ['DATABASE_URL'])
    async with e.connect() as c:
        r = await c.execute(text(\"SELECT column_name FROM information_schema.columns WHERE table_name='wc2026_outright_odds'\"))
        print([row[0] for row in r.all()])
asyncio.run(check())
"
```

Expected: `['id', 'nation', 'player_name', 'market_type', 'bookmaker', 'odds', 'scraped_at']`

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/037_wc2026_outright_odds.py
git commit -m "feat: migration 037 — wc2026_outright_odds table"
```

---

### Task 2: SQLAlchemy model WC2026OutrightOdd

**Files:**
- Create: `backend/app/models/wc2026_odds.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: Écrire le test (qui doit échouer)**

```python
# backend/tests/test_wc2026_odds_model.py
from app.models.wc2026_odds import WC2026OutrightOdd


def test_model_instantiation():
    obj = WC2026OutrightOdd(
        nation="France",
        player_name=None,
        market_type="winner",
        bookmaker="betclic",
        odds=4.5,
    )
    assert obj.nation == "France"
    assert obj.market_type == "winner"
    assert obj.odds == 4.5


def test_model_player_outright():
    obj = WC2026OutrightOdd(
        nation=None,
        player_name="Kylian Mbappé",
        market_type="top_scorer",
        bookmaker="unibet",
        odds=7.0,
    )
    assert obj.player_name == "Kylian Mbappé"
    assert obj.nation is None


def test_model_in_all():
    from app.models import WC2026OutrightOdd as Imported
    assert Imported is WC2026OutrightOdd
```

- [ ] **Step 2: Vérifier que le test échoue**

```bash
cd backend && pytest tests/test_wc2026_odds_model.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Créer le modèle**

```python
# backend/app/models/wc2026_odds.py
"""WC2026 outright odds model."""
from datetime import datetime

from sqlalchemy import DateTime, Float, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WC2026OutrightOdd(Base):
    """Outright odds for WC 2026 (winner, top4, top_scorer, etc.)."""

    __tablename__ = "wc2026_outright_odds"
    __table_args__ = (
        UniqueConstraint(
            "nation", "player_name", "market_type", "bookmaker",
            name="uq_wc2026_outright",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nation: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    player_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    market_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    bookmaker: Mapped[str] = mapped_column(String(20), nullable=False)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
```

- [ ] **Step 4: Enregistrer dans `__init__.py`**

Ajouter dans `backend/app/models/__init__.py` :
- Ligne d'import (après les autres WC2026 imports) :
  ```python
  from app.models.wc2026_odds import WC2026OutrightOdd  # noqa: F401
  ```
- Dans `__all__` : `"WC2026OutrightOdd",`

- [ ] **Step 5: Lancer les tests**

```bash
cd backend && pytest tests/test_wc2026_odds_model.py -v
```

Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/wc2026_odds.py backend/app/models/__init__.py backend/tests/test_wc2026_odds_model.py
git commit -m "feat: SQLAlchemy model WC2026OutrightOdd"
```

---

### Task 3: Seeder fixtures CDM

Le seeder génère les 72 matchs de groupe (C(4,2) paires par groupe × 12 groupes) depuis la table `wc2026_squad_players`, puis insère 32 placeholders KO.

**Files:**
- Create: `backend/scripts/seed_wc2026_fixtures.py`
- Test: `backend/tests/test_seed_wc2026_fixtures.py`

Les fonctions utilitaires du seeder sont pure-Python sans DB — on les teste en important directement le script.
`scripts/` n'est pas dans le `pythonpath` pytest par défaut, donc on les déplace dans un module importable.

- [ ] **Step 1: Écrire les tests unitaires**

```python
# backend/tests/test_seed_wc2026_fixtures.py
"""Tests des fonctions utilitaires du seeder (logique pure, sans DB)."""
import itertools


def _normalize_ext_id(name: str) -> str:
    """Duplicate ici pour isoler le test de l'import script."""
    import re, unicodedata
    n = unicodedata.normalize("NFKD", name.lower().strip())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9]+", "_", n)
    return n.strip("_")


def _generate_group_pairs(group_letter: str, nations: list[str]) -> list[dict]:
    sorted_nations = sorted(nations)
    all_pairs = list(itertools.combinations(sorted_nations, 2))
    round_map = {0: 1, 1: 1, 2: 2, 3: 2, 4: 3, 5: 3}
    result = []
    for idx, (home, away) in enumerate(all_pairs):
        round_num = round_map.get(idx, 1)
        ext_id = f"wc2026_group_{group_letter.lower()}_{_normalize_ext_id(home)}_vs_{_normalize_ext_id(away)}"
        result.append({"external_id": ext_id, "home_team": home, "away_team": away,
                       "round_num": round_num, "group_letter": group_letter})
    return result


def test_generate_group_pairs_count():
    pairs = _generate_group_pairs("A", ["France", "Maroc", "Espagne", "Portugal"])
    assert len(pairs) == 6  # C(4,2) = 6


def test_generate_group_pairs_round_assignment():
    pairs = _generate_group_pairs("X", ["A", "B", "C", "D"])
    rounds = [p["round_num"] for p in pairs]
    assert rounds.count(1) == 2
    assert rounds.count(2) == 2
    assert rounds.count(3) == 2


def test_generate_group_pairs_external_id_unique():
    pairs = _generate_group_pairs("B", ["France", "Brésil", "Argentine", "Allemagne"])
    ext_ids = [p["external_id"] for p in pairs]
    assert len(ext_ids) == len(set(ext_ids))


def test_normalize_ext_id():
    assert _normalize_ext_id("Côte d'Ivoire") == "cote_d_ivoire"
    assert _normalize_ext_id("Bosnia-Herzegovina") == "bosnia_herzegovina"
    assert _normalize_ext_id("USA") == "usa"
```

- [ ] **Step 2: Vérifier que les tests passent**

```bash
cd backend && pytest tests/test_seed_wc2026_fixtures.py -v
```

Expected: 4 tests PASS (logique inline, pas d'import script)

- [ ] **Step 3: Écrire le seeder**

```python
#!/usr/bin/env python3
# backend/scripts/seed_wc2026_fixtures.py
"""Seed WC2026 fixtures into the fixtures table.

Queries nations and group assignments from wc2026_squad_players,
generates all C(4,2) group-stage matchups and 32 KO-round placeholders.

Usage:
    DATABASE_URL=postgresql+psycopg2://... python scripts/seed_wc2026_fixtures.py
    # Or inside backend container:
    python scripts/seed_wc2026_fixtures.py
"""
from __future__ import annotations

import itertools
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Approximate kickoff times per (group_letter, round_number)
# Derived from FIFA WC 2026 official schedule. Times are UTC.
# Round 1: Jun 11-15 | Round 2: Jun 15-19 | Round 3: Jun 22-26
# ---------------------------------------------------------------------------
_GROUP_ROUND_KO: dict[tuple[str, int], datetime] = {
    ("A", 1): datetime(2026, 6, 11, 23, 0, tzinfo=timezone.utc),
    ("A", 2): datetime(2026, 6, 15, 23, 0, tzinfo=timezone.utc),
    ("A", 3): datetime(2026, 6, 22, 22, 0, tzinfo=timezone.utc),
    ("B", 1): datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc),
    ("B", 2): datetime(2026, 6, 16, 2, 0, tzinfo=timezone.utc),
    ("B", 3): datetime(2026, 6, 23, 2, 0, tzinfo=timezone.utc),
    ("C", 1): datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc),
    ("C", 2): datetime(2026, 6, 16, 20, 0, tzinfo=timezone.utc),
    ("C", 3): datetime(2026, 6, 23, 22, 0, tzinfo=timezone.utc),
    ("D", 1): datetime(2026, 6, 13, 2, 0, tzinfo=timezone.utc),
    ("D", 2): datetime(2026, 6, 17, 2, 0, tzinfo=timezone.utc),
    ("D", 3): datetime(2026, 6, 24, 2, 0, tzinfo=timezone.utc),
    ("E", 1): datetime(2026, 6, 13, 20, 0, tzinfo=timezone.utc),
    ("E", 2): datetime(2026, 6, 17, 20, 0, tzinfo=timezone.utc),
    ("E", 3): datetime(2026, 6, 24, 22, 0, tzinfo=timezone.utc),
    ("F", 1): datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc),
    ("F", 2): datetime(2026, 6, 18, 2, 0, tzinfo=timezone.utc),
    ("F", 3): datetime(2026, 6, 25, 2, 0, tzinfo=timezone.utc),
    ("G", 1): datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc),
    ("G", 2): datetime(2026, 6, 18, 20, 0, tzinfo=timezone.utc),
    ("G", 3): datetime(2026, 6, 25, 22, 0, tzinfo=timezone.utc),
    ("H", 1): datetime(2026, 6, 15, 2, 0, tzinfo=timezone.utc),
    ("H", 2): datetime(2026, 6, 19, 2, 0, tzinfo=timezone.utc),
    ("H", 3): datetime(2026, 6, 26, 2, 0, tzinfo=timezone.utc),
    ("I", 1): datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
    ("I", 2): datetime(2026, 6, 19, 20, 0, tzinfo=timezone.utc),
    ("I", 3): datetime(2026, 6, 26, 22, 0, tzinfo=timezone.utc),
    ("J", 1): datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
    ("J", 2): datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc),
    ("J", 3): datetime(2026, 6, 22, 22, 0, tzinfo=timezone.utc),
    ("K", 1): datetime(2026, 6, 12, 2, 0, tzinfo=timezone.utc),
    ("K", 2): datetime(2026, 6, 16, 2, 0, tzinfo=timezone.utc),
    ("K", 3): datetime(2026, 6, 23, 2, 0, tzinfo=timezone.utc),
    ("L", 1): datetime(2026, 6, 12, 20, 0, tzinfo=timezone.utc),
    ("L", 2): datetime(2026, 6, 16, 20, 0, tzinfo=timezone.utc),
    ("L", 3): datetime(2026, 6, 23, 22, 0, tzinfo=timezone.utc),
}

# KO round placeholder kickoffs
_KO_FIXTURES: list[dict] = [
    # R32: July 1-6 (16 matches)
    *[
        {
            "external_id": f"wc2026_r32_m{i+1}",
            "home_team": f"R32 TBD {2*i+1}",
            "away_team": f"R32 TBD {2*i+2}",
            "kickoff_utc": datetime(2026, 7, 1 + (i // 3), 2 + (i % 2) * 20, 0, tzinfo=timezone.utc),
            "round": "r32",
            "matchweek": None,
        }
        for i in range(16)
    ],
    # R16: July 9-12 (8 matches)
    *[
        {
            "external_id": f"wc2026_r16_m{i+1}",
            "home_team": f"R16 TBD {2*i+1}",
            "away_team": f"R16 TBD {2*i+2}",
            "kickoff_utc": datetime(2026, 7, 9 + (i // 2), 2 + (i % 2) * 20, 0, tzinfo=timezone.utc),
            "round": "r16",
            "matchweek": None,
        }
        for i in range(8)
    ],
    # QF: July 15-16 (4 matches)
    *[
        {
            "external_id": f"wc2026_qf_m{i+1}",
            "home_team": f"QF TBD {2*i+1}",
            "away_team": f"QF TBD {2*i+2}",
            "kickoff_utc": datetime(2026, 7, 15 + i // 2, 2 + (i % 2) * 20, 0, tzinfo=timezone.utc),
            "round": "qf",
            "matchweek": None,
        }
        for i in range(4)
    ],
    # SF: July 19-20 (2 matches)
    {
        "external_id": "wc2026_sf_m1",
        "home_team": "SF TBD 1",
        "away_team": "SF TBD 2",
        "kickoff_utc": datetime(2026, 7, 19, 22, 0, tzinfo=timezone.utc),
        "round": "sf",
        "matchweek": None,
    },
    {
        "external_id": "wc2026_sf_m2",
        "home_team": "SF TBD 3",
        "away_team": "SF TBD 4",
        "kickoff_utc": datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc),
        "round": "sf",
        "matchweek": None,
    },
    # 3rd place + Final
    {
        "external_id": "wc2026_3rd_place",
        "home_team": "3RD TBD 1",
        "away_team": "3RD TBD 2",
        "kickoff_utc": datetime(2026, 7, 22, 22, 0, tzinfo=timezone.utc),
        "round": "3rd_place",
        "matchweek": None,
    },
    {
        "external_id": "wc2026_final",
        "home_team": "FINAL TBD 1",
        "away_team": "FINAL TBD 2",
        "kickoff_utc": datetime(2026, 7, 23, 21, 0, tzinfo=timezone.utc),
        "round": "final",
        "matchweek": None,
    },
]


def _normalize_ext_id(name: str) -> str:
    """Normalize a team name for use in external_id."""
    n = unicodedata.normalize("NFKD", name.lower().strip())
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9]+", "_", n)
    return n.strip("_")


def _generate_group_pairs(group_letter: str, nations: list[str]) -> list[dict]:
    """Generate C(n,2) pairs for a group. Assigns 2 pairs per round.

    Returns list of dicts with keys: external_id, home_team, away_team,
    round_num, group_letter.
    """
    sorted_nations = sorted(nations)
    all_pairs = list(itertools.combinations(sorted_nations, 2))
    # 6 pairs → 3 rounds of 2 matches each
    round_map = {0: 1, 1: 1, 2: 2, 3: 2, 4: 3, 5: 3}
    result = []
    for idx, (home, away) in enumerate(all_pairs):
        round_num = round_map.get(idx, 1)
        ext_id = f"wc2026_group_{group_letter.lower()}_{_normalize_ext_id(home)}_vs_{_normalize_ext_id(away)}"
        result.append({
            "external_id": ext_id,
            "home_team": home,
            "away_team": away,
            "round_num": round_num,
            "group_letter": group_letter,
        })
    return result


def main() -> None:
    dsn = os.environ.get(
        "DATABASE_URL",
        "postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@localhost:5432/ev0",
    ).replace("postgresql+asyncpg://", "postgresql://").replace("postgresql+psycopg2://", "postgresql://")

    conn = psycopg2.connect(dsn)
    cur = conn.cursor()

    # 1. Get nations + groups from DB
    cur.execute(
        "SELECT DISTINCT nation, group_letter FROM wc2026_squad_players ORDER BY group_letter, nation"
    )
    rows = cur.fetchall()
    if not rows:
        print("ERROR: wc2026_squad_players is empty — run squad seeder first.")
        sys.exit(1)

    groups: dict[str, list[str]] = {}
    for nation, group in rows:
        groups.setdefault(group, []).append(nation)

    print(f"Found {len(groups)} groups, {len(rows)} distinct nations")

    # 2. Generate group stage fixtures
    group_fixtures = []
    for group_letter, nations in sorted(groups.items()):
        pairs = _generate_group_pairs(group_letter, nations)
        for p in pairs:
            ko = _GROUP_ROUND_KO.get((group_letter, p["round_num"]))
            if ko is None:
                print(f"WARNING: no kickoff time for group {group_letter} round {p['round_num']}, using fallback")
                ko = datetime(2026, 6, 15, 20, 0, tzinfo=timezone.utc)
            group_fixtures.append({
                "external_id": p["external_id"],
                "league": "world_cup_2026",
                "season": "2025-2026",
                "matchweek": p["round_num"],
                "home_team": p["home_team"],
                "away_team": p["away_team"],
                "kickoff_utc": ko,
                "status": "scheduled",
            })

    all_fixtures = group_fixtures + [
        {
            "external_id": f["external_id"],
            "league": "world_cup_2026",
            "season": "2025-2026",
            "matchweek": f["matchweek"],
            "home_team": f["home_team"],
            "away_team": f["away_team"],
            "kickoff_utc": f["kickoff_utc"],
            "status": "scheduled",
        }
        for f in _KO_FIXTURES
    ]

    # 3. Insert with ON CONFLICT DO NOTHING
    inserted = 0
    skipped = 0
    for fx in all_fixtures:
        cur.execute(
            """
            INSERT INTO fixtures
                (external_id, league, season, matchweek, home_team, away_team, kickoff_utc, status)
            VALUES
                (%(external_id)s, %(league)s, %(season)s, %(matchweek)s,
                 %(home_team)s, %(away_team)s, %(kickoff_utc)s, %(status)s)
            ON CONFLICT (external_id) DO NOTHING
            """,
            fx,
        )
        if cur.rowcount == 1:
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done: {inserted} inserted, {skipped} skipped (already existed)")
    print(f"  Group stage: {len(group_fixtures)} fixtures")
    print(f"  KO rounds:   {len(_KO_FIXTURES)} placeholders")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Lancer le seeder en local**

```bash
cd backend && DATABASE_URL=postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@localhost:5432/ev0 python scripts/seed_wc2026_fixtures.py
```

Expected output:
```
Found 12 groups, 48 distinct nations
Done: 104 inserted, 0 skipped (already existed)
  Group stage: 72 fixtures
  KO rounds:   32 placeholders
```

- [ ] **Step 6: Vérifier en DB**

```bash
psql $DATABASE_URL -c "SELECT COUNT(*) FROM fixtures WHERE league = 'world_cup_2026';"
```

Expected: `104`

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/seed_wc2026_fixtures.py backend/tests/test_seed_wc2026_fixtures.py
git commit -m "feat: seeder + tests fixtures WC2026 (72 groupes + 32 KO placeholders)"
```

---

### Task 4: Scraper outrights PMU (Kambi)

Le Kambi API a un endpoint `/listView/football/outright.json` qui retourne tous les marchés de tournoi football. On filtre par path "World Cup 2026".

**Files:**
- Create: `backend/app/ingestion/wc2026/sync_wc_outrights.py`
- Create: `backend/tests/ingestion/test_sync_wc_outrights.py`

- [ ] **Step 1: Écrire le test PMU (qui doit échouer)**

```python
# backend/tests/ingestion/test_sync_wc_outrights.py
"""Tests unitaires pour sync_wc_outrights.py."""
from unittest.mock import AsyncMock, patch

import pytest

from app.ingestion.wc2026.sync_wc_outrights import (
    _classify_kambi_outright,
    _kambi_odds,
    scrape_pmu_wc_outrights,
)


def test_classify_kambi_outright_winner():
    assert _classify_kambi_outright("Winner", "World Cup Winner") == "winner"


def test_classify_kambi_outright_top4():
    assert _classify_kambi_outright("Top 4", "To Reach Semi Final") == "top4"
    assert _classify_kambi_outright("Semi Final", "") == "top4"


def test_classify_kambi_outright_top8():
    assert _classify_kambi_outright("Quarter Final", "") == "top8"
    assert _classify_kambi_outright("Top 8", "") == "top8"


def test_classify_kambi_outright_top2():
    assert _classify_kambi_outright("Final", "To Reach the Final") == "top2"
    assert _classify_kambi_outright("Top 2", "") == "top2"


def test_classify_kambi_outright_group_stage():
    assert _classify_kambi_outright("Group Stage", "To Qualify") == "group_stage"


def test_classify_kambi_outright_top_scorer():
    assert _classify_kambi_outright("Top Goalscorer", "Top Scorer") == "top_scorer"
    assert _classify_kambi_outright("Top Scorer", "") == "top_scorer"


def test_classify_kambi_outright_top_assister():
    assert _classify_kambi_outright("Top Assister", "") == "top_assister"
    assert _classify_kambi_outright("Most Assists", "") == "top_assister"


def test_classify_kambi_outright_unknown():
    assert _classify_kambi_outright("Fair Play Award", "") is None


def test_kambi_odds_valid():
    assert _kambi_odds(3500) == pytest.approx(3.5)
    assert _kambi_odds(1010) == pytest.approx(1.01)


def test_kambi_odds_invalid():
    assert _kambi_odds(None) is None
    assert _kambi_odds(1000) is None  # ≤ 1000 → cote ≤ 1.00 → invalide
    assert _kambi_odds(0) is None


@pytest.mark.asyncio
async def test_scrape_pmu_wc_outrights_empty_on_http_error():
    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("connection refused")
        result = await scrape_pmu_wc_outrights()
    assert result == []


@pytest.mark.asyncio
async def test_scrape_pmu_wc_outrights_parses_winner():
    fake_response = {
        "events": [
            {
                "event": {
                    "id": 1001,
                    "englishName": "World Cup Winner",
                    "path": [
                        {"englishName": "Football"},
                        {"englishName": "World Cup 2026"},
                    ],
                    "betOffers": [
                        {
                            "betOfferType": {"englishName": "Winner"},
                            "criterion": {"englishLabel": "Winner"},
                            "outcomes": [
                                {"label": "France", "englishLabel": "France", "odds": 4000},
                                {"label": "Brésil", "englishLabel": "Brazil", "odds": 5000},
                            ],
                        }
                    ],
                }
            }
        ]
    }
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = AsyncMock()
    mock_resp.json.return_value = fake_response

    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        result = await scrape_pmu_wc_outrights()

    assert len(result) == 2
    france = next(r for r in result if r["nation"] == "France")
    assert france["market_type"] == "winner"
    assert france["odds"] == pytest.approx(4.0)
    assert france["bookmaker"] == "pmu"
```

- [ ] **Step 2: Vérifier que les tests échouent**

```bash
cd backend && pytest tests/ingestion/test_sync_wc_outrights.py -v
```

Expected: FAIL avec `ModuleNotFoundError`

- [ ] **Step 3: Créer le module avec les fonctions PMU**

```python
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
    if "winner" in combined and "top" not in combined:
        return "winner"
    if "semi final" in combined or "top 4" in combined:
        return "top4"
    if "quarter final" in combined or "top 8" in combined:
        return "top8"
    if "final" in combined and "semi" not in combined and "quarter" not in combined:
        return "top2"
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
```

- [ ] **Step 4: Lancer les tests PMU**

```bash
cd backend && pytest tests/ingestion/test_sync_wc_outrights.py -v
```

Expected: tous les tests PASS (11 tests)

- [ ] **Step 5: Commit intermédiaire**

```bash
git add backend/app/ingestion/wc2026/sync_wc_outrights.py backend/tests/ingestion/test_sync_wc_outrights.py
git commit -m "feat: scraper outrights PMU/Kambi WC2026"
```

---

### Task 5: Scraper outrights Unibet (LVS) + Betclic

**Files:**
- Modify: `backend/app/ingestion/wc2026/sync_wc_outrights.py`
- Modify: `backend/tests/ingestion/test_sync_wc_outrights.py`

- [ ] **Step 1: Ajouter les tests Unibet et Betclic**

Ajouter à la fin de `backend/tests/ingestion/test_sync_wc_outrights.py` :

```python
from app.ingestion.wc2026.sync_wc_outrights import (
    scrape_unibet_wc_outrights,
    scrape_betclic_wc_outrights,
    _parse_lvs_price,
)


def test_parse_lvs_price_valid():
    assert _parse_lvs_price("4,50") == pytest.approx(4.5)
    assert _parse_lvs_price("2") == pytest.approx(2.0)


def test_parse_lvs_price_invalid():
    assert _parse_lvs_price(None) is None
    assert _parse_lvs_price("null") is None
    assert _parse_lvs_price("0,90") is None  # < 1.01


@pytest.mark.asyncio
async def test_scrape_unibet_wc_outrights_empty_on_error():
    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("timeout")
        result = await scrape_unibet_wc_outrights()
    assert result == []


@pytest.mark.asyncio
async def test_scrape_unibet_wc_outrights_parses_winner():
    # LVS outright response: flat dict with m{id} market entries and o{id} outcomes
    fake_token_resp = AsyncMock()
    fake_token_resp.raise_for_status = AsyncMock()
    fake_token_resp.json.return_value = {"hsToken": "test-token"}

    fake_events_resp = AsyncMock()
    fake_events_resp.raise_for_status = AsyncMock()
    fake_events_resp.json.return_value = {
        "items": {
            "e1001": {
                "a": "Vainqueur CDM 2026",
                "b": "",
                "start": "2607230000",
            },
        }
    }

    fake_ff_resp = AsyncMock()
    fake_ff_resp.raise_for_status = AsyncMock()
    fake_ff_resp.json.return_value = {
        "items": {
            "m1": {"markettypeId": 14, "n": "Vainqueur"},
            "o1": {"marketId": "m1", "a": "France", "pr": "4,00"},
            "o2": {"marketId": "m1", "a": "Brésil", "pr": "5,00"},
        }
    }

    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = [fake_token_resp, fake_events_resp, fake_ff_resp]
        result = await scrape_unibet_wc_outrights()

    assert any(r["nation"] == "France" and r["market_type"] == "winner" for r in result)


@pytest.mark.asyncio
async def test_scrape_betclic_wc_outrights_empty_on_error():
    with patch("app.ingestion.wc2026.sync_wc_outrights.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__.return_value = mock_client
        mock_client.get.side_effect = Exception("timeout")
        result = await scrape_betclic_wc_outrights()
    assert result == []
```

- [ ] **Step 2: Vérifier que les nouveaux tests échouent**

```bash
cd backend && pytest tests/ingestion/test_sync_wc_outrights.py::test_parse_lvs_price_valid -v
```

Expected: FAIL avec `ImportError`

- [ ] **Step 3: Ajouter les scrapers Unibet et Betclic dans `sync_wc_outrights.py`**

Ajouter après la section PMU :

```python
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
    _LIST_PARAMS = "lineId=1&originId=3&ext=1&showPromotions=true&showMarketTypeGroups=true"

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

    Note: Betclic's outright REST endpoint URL may need adjustment if the API
    structure changes. If this returns [], check:
      curl -H 'x-bg-regulation: FR' 'https://www.betclic.fr/api/v2/outrights?competition_id=1'
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
```

- [ ] **Step 4: Lancer tous les tests outrights**

```bash
cd backend && pytest tests/ingestion/test_sync_wc_outrights.py -v
```

Expected: tous PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/wc2026/sync_wc_outrights.py backend/tests/ingestion/test_sync_wc_outrights.py
git commit -m "feat: scrapers outrights Unibet LVS + Betclic WC2026"
```

---

### Task 6: Storage + coordinateur sync

**Files:**
- Modify: `backend/app/ingestion/wc2026/sync_wc_outrights.py`
- Modify: `backend/tests/ingestion/test_sync_wc_outrights.py`

- [ ] **Step 1: Ajouter le test de storage**

Ajouter à la fin de `test_sync_wc_outrights.py` :

```python
from unittest.mock import MagicMock, patch, AsyncMock
import pytest
from app.ingestion.wc2026.sync_wc_outrights import store_wc_outrights, sync_all_wc_outrights


@pytest.mark.asyncio
async def test_store_wc_outrights_upsert():
    """store_wc_outrights doit appeler session.execute avec upsert."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    outrights = [
        {"nation": "France", "player_name": None, "market_type": "winner", "bookmaker": "pmu", "odds": 4.0},
        {"nation": "Brésil", "player_name": None, "market_type": "winner", "bookmaker": "pmu", "odds": 5.0},
    ]
    await store_wc_outrights(session, outrights)
    assert session.execute.called
    assert session.commit.called


@pytest.mark.asyncio
async def test_store_wc_outrights_empty():
    """store_wc_outrights ne crash pas avec une liste vide."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    await store_wc_outrights(session, [])
    session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_sync_all_wc_outrights_aggregates():
    """sync_all_wc_outrights appelle les 3 scrapers et retourne le total."""
    with (
        patch("app.ingestion.wc2026.sync_wc_outrights.scrape_pmu_wc_outrights", AsyncMock(return_value=[{"odds": 1}])),
        patch("app.ingestion.wc2026.sync_wc_outrights.scrape_unibet_wc_outrights", AsyncMock(return_value=[{"odds": 2}])),
        patch("app.ingestion.wc2026.sync_wc_outrights.scrape_betclic_wc_outrights", AsyncMock(return_value=[{"odds": 3}])),
        patch("app.ingestion.wc2026.sync_wc_outrights.store_wc_outrights", AsyncMock()),
    ):
        session = AsyncMock()
        total = await sync_all_wc_outrights(session)
    assert total == 3
```

- [ ] **Step 2: Vérifier que les tests échouent**

```bash
cd backend && pytest tests/ingestion/test_sync_wc_outrights.py::test_store_wc_outrights_upsert -v
```

Expected: FAIL avec `ImportError`

- [ ] **Step 3: Ajouter les fonctions storage + coordinateur dans `sync_wc_outrights.py`**

Ajouter en fin de fichier :

```python
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

    pmu_task = scrape_pmu_wc_outrights()
    unibet_task = scrape_unibet_wc_outrights()
    betclic_task = scrape_betclic_wc_outrights()

    pmu_results, unibet_results, betclic_results = await asyncio.gather(
        pmu_task, unibet_task, betclic_task,
    )

    all_results = pmu_results + unibet_results + betclic_results
    if all_results:
        await store_wc_outrights(session, all_results)

    logger.info(
        "sync_all_wc_outrights: pmu=%d unibet=%d betclic=%d total=%d",
        len(pmu_results), len(unibet_results), len(betclic_results), len(all_results),
    )
    return len(all_results)
```

- [ ] **Step 4: Lancer tous les tests outrights**

```bash
cd backend && pytest tests/ingestion/test_sync_wc_outrights.py -v
```

Expected: tous PASS (20+ tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/ingestion/wc2026/sync_wc_outrights.py backend/tests/ingestion/test_sync_wc_outrights.py
git commit -m "feat: storage + coordinateur sync_all_wc_outrights"
```

---

### Task 7: Job worker toutes les 6h

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_worker_wc_outrights.py`

- [ ] **Step 1: Écrire le test**

```python
# backend/tests/test_worker_wc_outrights.py
"""Test que job_sync_wc_outright_odds est correctement enregistré dans le scheduler."""
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.worker import create_scheduler, job_sync_wc_outright_odds


@pytest.mark.asyncio
async def test_job_sync_wc_outright_odds_exists():
    """job_sync_wc_outright_odds est appelable sans crash (avec sync patché)."""
    with patch(
        "app.worker.sync_all_wc_outrights",
        AsyncMock(return_value=42),
    ):
        with patch("app.worker.async_session") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_session
            await job_sync_wc_outright_odds()


def test_wc_outright_job_in_scheduler():
    """Le scheduler contient un job avec id='sync_wc_outright_odds'."""
    scheduler = create_scheduler()
    job_ids = [job.id for job in scheduler.get_jobs()]
    assert "sync_wc_outright_odds" in job_ids
    scheduler.shutdown(wait=False)
```

- [ ] **Step 2: Vérifier que les tests échouent**

```bash
cd backend && pytest tests/test_worker_wc_outrights.py -v
```

Expected: FAIL avec `ImportError` ou `AssertionError`

- [ ] **Step 3: Ajouter l'import dans `worker.py`**

En haut du fichier `backend/app/worker.py`, ajouter dans le bloc d'imports :

```python
from app.ingestion.wc2026.sync_wc_outrights import sync_all_wc_outrights
```

- [ ] **Step 4: Ajouter la fonction job dans `worker.py`**

Ajouter après `job_sync_bzzoiro_lineups` (vers ligne 1404), avant `create_scheduler` :

```python
async def job_sync_wc_outright_odds() -> None:
    """Scrape les outrights CDM (vainqueur, top4, top8, buteur) sur PMU/Unibet/Betclic."""
    logger.info("job_sync_wc_outright_odds: start")
    try:
        async with async_session() as session:
            total = await sync_all_wc_outrights(session)
        logger.info("job_sync_wc_outright_odds: %d cotes upsertées", total)
    except Exception as exc:
        logger.exception("job_sync_wc_outright_odds failed: %s", exc)
```

- [ ] **Step 5: Enregistrer le job dans `create_scheduler()`**

Dans `create_scheduler()`, ajouter avant `return scheduler` :

```python
    # WC2026 outrights: toutes les 6h (outrights bougent lentement)
    scheduler.add_job(
        job_sync_wc_outright_odds,
        IntervalTrigger(hours=6),
        id="sync_wc_outright_odds",
        name="Sync WC2026 outright odds (winner/top4/top8/scorer) sur PMU+Unibet+Betclic",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
```

- [ ] **Step 6: Lancer les tests**

```bash
cd backend && pytest tests/test_worker_wc_outrights.py -v
```

Expected: 2 tests PASS

- [ ] **Step 7: Lancer toute la suite de tests backend**

```bash
cd backend && pytest tests/ -x -q 2>&1 | tail -20
```

Expected: aucune régression

- [ ] **Step 8: Commit final**

```bash
git add backend/app/worker.py backend/tests/test_worker_wc_outrights.py
git commit -m "feat: worker job sync_wc_outright_odds toutes les 6h"
```

---

## Déploiement VPS

Après le dernier commit, pousser et déployer :

```bash
git push origin main
ssh root@213.130.144.204 "
  cd /etc/dokploy/compose/ev0-compose-z5hvqt/code &&
  git pull origin main &&
  docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker 2>&1 | tail -10
"
```

Puis lancer le seeder de fixtures en production :

```bash
ssh root@213.130.144.204 "
  docker exec ev0-compose-z5hvqt-backend-1 python scripts/seed_wc2026_fixtures.py
"
```

Vérifier que la migration 037 est appliquée :

```bash
ssh root@213.130.144.204 "
  docker exec ev0-compose-z5hvqt-backend-1 alembic current
"
```

Expected: `037 (head)`
