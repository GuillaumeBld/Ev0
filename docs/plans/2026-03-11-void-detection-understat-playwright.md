# VOID Detection via Understat Playwright Rosters

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable automatic VOID settlement by scraping per-match player minutes from Understat match pages via Playwright (local machine), importing the data into a new `PlayerMatchMinutes` table, and updating `auto_settle.py` to use it.

**Architecture:** (1) New `PlayerMatchMinutes` model stores fixture_id + player_name + minutes_played. (2) `ops/fetch_understat_rosters.py` uses Playwright to render Understat match pages (SPA), evaluate `window.rostersData` from JavaScript, and save to JSON. (3) `ops/import_understat_rosters.py` correlates saved JSON with DB fixtures by team name + date, then inserts PlayerMatchMinutes rows. (4) `auto_settle.py` gains a VOID path: if PlayerMatchMinutes data exists for a fixture and the player has 0 minutes or is absent → VOID; otherwise falls back to MatchEvents for WON/LOST.

**Tech Stack:** SQLAlchemy async, Alembic, Playwright (Python, local), asyncio, JSON

---

## Context

- **Current `auto_settle.py`** (`backend/app/ingestion/auto_settle.py`): Settles WON/LOST using the `MatchEvents` table (goals/assists). Cannot detect VOID (no minutes data). Already deployed and working.
- **Understat is a SPA**: Match pages (`https://understat.com/match/{id}`) and league pages no longer embed `rostersData`/`datesData` as `JSON.parse()` vars in the HTML. After JavaScript renders, data is available via `window.datesData` / `window.rostersData`.
- **Local-only fetch**: Like `ops/fetch_sofascore.py`, the Playwright script runs on the local machine (not VPS — Understat may also be blocked from VPS IPs). Output JSON is SCP'd to VPS and imported via docker exec.
- **No Understat IDs in DB**: We map Understat matches to our fixtures using home_team + away_team + kickoff_date. Understat team names differ from our DB names (see TEAM_NAME_MAP below).
- **Migration sequence**: Latest migration is `010_model_c_sofascore.py`. Next must be `011`.
- **Model pattern**: See `backend/app/models/match_events.py` for structure. Models must be added to `backend/app/models/__init__.py`.
- **Alembic migrations** are handwritten (not auto-generated). Run `alembic upgrade head` inside the backend container on VPS.

---

## Task 1: `PlayerMatchMinutes` model + migration

**Files:**
- Create: `backend/app/models/player_match_minutes.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/011_add_player_match_minutes.py`

**Step 1: Create the model**

```python
# backend/app/models/player_match_minutes.py
"""PlayerMatchMinutes model — stores per-player minutes played per fixture."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PlayerMatchMinutes(Base, TimestampMixin):
    """Minutes played by a player in a specific fixture (from Understat rostersData)."""

    __tablename__ = "player_match_minutes"

    id: Mapped[int] = mapped_column(primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), index=True)
    player_name: Mapped[str] = mapped_column(String(200))
    minutes_played: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("fixture_id", "player_name", name="uq_player_match_minutes"),
    )

    def __repr__(self) -> str:
        return f"<PlayerMatchMinutes {self.player_name} {self.minutes_played}min fixture={self.fixture_id}>"
```

**Step 2: Add to `__init__.py`**

Add import:
```python
from app.models.player_match_minutes import PlayerMatchMinutes
```

Add to `__all__`:
```python
"PlayerMatchMinutes",
```

**Step 3: Create the migration**

```python
# backend/alembic/versions/011_add_player_match_minutes.py
"""Add player_match_minutes table

Revision ID: 011
Revises: 010
Create Date: 2026-03-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "player_match_minutes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("player_name", sa.String(200), nullable=False),
        sa.Column("minutes_played", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fixture_id", "player_name", name="uq_player_match_minutes"),
    )
    op.create_index("ix_player_match_minutes_fixture_id", "player_match_minutes", ["fixture_id"])


def downgrade() -> None:
    op.drop_index("ix_player_match_minutes_fixture_id")
    op.drop_table("player_match_minutes")
```

**Step 4: Verify imports work (no DB connection needed)**

```bash
cd /Users/yohan.resin/Ev0/backend
uv run python -c "from app.models.player_match_minutes import PlayerMatchMinutes; print('OK')"
```

Expected: `OK`

---

## Task 2: `ops/fetch_understat_rosters.py` — Playwright local fetch

**Files:**
- Create: `ops/fetch_understat_rosters.py`

**Purpose:** Render each Understat match page with Playwright, evaluate `window.rostersData` after the SPA loads, collect all finished matches for the current season across all 5 leagues, and save to `/tmp/understat_rosters.json`.

**Output JSON structure:**
```json
{
  "understat_match_id": {
    "home": "Team Name",
    "away": "Team Name",
    "date": "2025-08-17",
    "players": [
      {"name": "Player Name", "team_side": "h", "minutes": 90, "goals": 1, "assists": 0}
    ]
  }
}
```

**Step 1: Create the script**

```python
#!/usr/bin/env python3
"""fetch_understat_rosters.py — scrape per-match player minutes from Understat via Playwright.

Understat is a React SPA. HTML is a 27KB shell; data is in window.datesData / window.rostersData
after JavaScript renders. Playwright renders the SPA and we evaluate JS to get the data.

Usage:
    python ops/fetch_understat_rosters.py [output.json]
    python ops/fetch_understat_rosters.py --limit 20  # fetch only first 20 matches (for testing)

Output: /tmp/understat_rosters.json (or specified path)
"""

import argparse
import asyncio
import json
import sys
from datetime import date

from playwright.async_api import async_playwright

OUTPUT = "/tmp/understat_rosters.json"
RATE_LIMIT = 2.0  # seconds between requests

# Understat league slugs
LEAGUES = {
    "ligue_1": ("Ligue_1", 2025),
    "premier_league": ("EPL", 2025),
    "bundesliga": ("Bundesliga", 2025),
    "la_liga": ("La_liga", 2025),
    "serie_a": ("Serie_A", 2025),
}


async def get_dates_data(page, slug: str, year: int) -> dict:
    """Fetch datesData for a league/season via Playwright JS evaluation."""
    url = f"https://understat.com/league/{slug}/{year}"
    print(f"  Loading {url}...")
    await page.goto(url, wait_until="networkidle", timeout=60000)
    # Understat injects data into window variables after React mounts
    data = await page.evaluate("() => window.datesData ? JSON.parse(JSON.stringify(window.datesData)) : null")
    if data is None:
        # Fallback: try to extract from script tags (old embed format)
        content = await page.content()
        import re
        match = re.search(r"datesData\s*=\s*JSON\.parse\('(.+?)'\)", content)
        if match:
            import urllib.parse
            raw = match.group(1).encode().decode("unicode_escape")
            data = json.loads(raw)
    return data or {}


async def get_rosters_data(page, understat_id: str) -> dict:
    """Fetch rostersData for a single match via Playwright JS evaluation."""
    url = f"https://understat.com/match/{understat_id}"
    await page.goto(url, wait_until="networkidle", timeout=60000)
    data = await page.evaluate("() => window.rostersData ? JSON.parse(JSON.stringify(window.rostersData)) : null")
    if data is None:
        # Fallback: try to extract from script tags
        content = await page.content()
        import re
        match = re.search(r"rostersData\s*=\s*JSON\.parse\('(.+?)'\)", content)
        if match:
            raw = match.group(1).encode().decode("unicode_escape")
            data = json.loads(raw)
    return data or {}


def parse_roster(rosters_data: dict, understat_id: str, home: str, away: str, match_date: str) -> dict:
    """Convert raw rostersData into our output format."""
    players = []
    for side in ("h", "a"):
        side_data = rosters_data.get(side, {})
        for _pid, p in side_data.items():
            players.append({
                "name": p.get("player", ""),
                "team_side": side,
                "minutes": int(p.get("time", 0) or 0),
                "goals": int(p.get("goals", 0) or 0),
                "assists": int(p.get("assists", 0) or 0),
            })
    return {
        "home": home,
        "away": away,
        "date": match_date,
        "players": players,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=OUTPUT)
    parser.add_argument("--limit", type=int, default=None, help="Max matches per league (for testing)")
    args = parser.parse_args()

    results: dict[str, dict] = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        for league_key, (slug, year) in LEAGUES.items():
            print(f"\n=== {league_key} ({slug}/{year}) ===")
            dates_data = await get_dates_data(page, slug, year)

            # Filter to finished matches only
            finished = {
                mid: m for mid, m in dates_data.items()
                if m.get("isResult")
            }
            print(f"  {len(finished)} finished matches found")

            match_ids = list(finished.keys())
            if args.limit:
                match_ids = match_ids[:args.limit]

            for i, mid in enumerate(match_ids):
                m = finished[mid]
                home = m.get("h", {}).get("title", "")
                away = m.get("a", {}).get("title", "")
                dt_str = m.get("datetime", "")[:10]  # "2025-08-17"

                if mid in results:
                    continue  # already fetched

                print(f"  [{i+1}/{len(match_ids)}] {home} vs {away} ({dt_str}) id={mid}")
                try:
                    rosters_data = await get_rosters_data(page, mid)
                    if not rosters_data:
                        print(f"    WARNING: empty rostersData for match {mid}")
                        continue
                    results[mid] = parse_roster(rosters_data, mid, home, away, dt_str)
                    print(f"    → {len(results[mid]['players'])} players")
                except Exception as e:
                    print(f"    ERROR: {e}")
                    continue

                await asyncio.sleep(RATE_LIMIT)

        await browser.close()

    with open(args.output, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(results)} matches to {args.output}")


asyncio.run(main())
```

**Step 2: Test with --limit 3 (quick sanity check)**

```bash
cd /Users/yohan.resin/Ev0
python ops/fetch_understat_rosters.py /tmp/test_rosters.json --limit 3
```

Expected:
- Script runs without crashing
- Output file contains 3 matches with `players` arrays
- Each player entry has `name`, `minutes`, `goals`, `assists`

**Step 3: Inspect the output**

```bash
python3 -c "
import json
data = json.load(open('/tmp/test_rosters.json'))
mid = list(data.keys())[0]
m = data[mid]
print(f'Match: {m[\"home\"]} vs {m[\"away\"]} ({m[\"date\"]})')
print(f'Players: {len(m[\"players\"])}')
for p in m['players'][:5]:
    print(f'  {p[\"name\"]} ({p[\"team_side\"]}) {p[\"minutes\"]}min g={p[\"goals\"]} a={p[\"assists\"]}')
"
```

Expected: readable player data with non-zero minutes for most players.

If `window.rostersData` returns `null`, it means the SPA approach needs debugging — check if the fallback regex approach works by looking at the raw page content. Adjust wait strategy (e.g., wait for `#match-rosters` element or a longer networkidle timeout).

---

## Task 3: `ops/import_understat_rosters.py` — DB import

**Files:**
- Create: `ops/import_understat_rosters.py`

**Purpose:** Read `/tmp/understat_rosters.json`, correlate each Understat match with a DB fixture (by home_team + away_team + kickoff_date, with team name normalization), then insert/upsert `PlayerMatchMinutes` rows.

**Team name mapping** — Understat names → our DB names (lowercase comparison):

```python
TEAM_NAME_MAP = {
    # Premier League
    "manchester city": "man city",
    "manchester united": "man utd",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves",
    "tottenham hotspur": "tottenham",
    "brighton & hove albion": "brighton",
    "west ham united": "west ham",
    "leicester city": "leicester",
    "ipswich town": "ipswich",
    # Ligue 1
    "paris saint-germain": "psg",
    "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon",
    "stade de reims": "reims",
    "stade brestois 29": "brest",
    "rc strasbourg alsace": "strasbourg",
    "stade rennais fc": "rennes",
    "fc nantes": "nantes",
    "ogc nice": "nice",
    "montpellier hsc": "montpellier",
    "rc lens": "lens",
    "toulouse fc": "toulouse",
    # Bundesliga
    "borussia dortmund": "dortmund",
    "bayer leverkusen": "leverkusen",
    "eintracht frankfurt": "frankfurt",
    "vfb stuttgart": "stuttgart",
    "sc freiburg": "freiburg",
    "1. fc union berlin": "union berlin",
    "1. fsv mainz 05": "mainz",
    "fc augsburg": "augsburg",
    "1. fc heidenheim 1846": "heidenheim",
    "sv werder bremen": "werder bremen",
    "borussia mönchengladbach": "gladbach",
    "vfl wolfsburg": "wolfsburg",
    "vfl bochum": "bochum",
    "fc st. pauli": "st. pauli",
    # La Liga
    "atletico madrid": "atlético madrid",
    "deportivo alaves": "alavés",
    "leganes": "leganés",
    "real valladolid": "valladolid",
    # Serie A
    "ac milan": "milan",
    "hellas verona": "verona",
}
```

**Step 1: Create the script**

```python
"""import_understat_rosters.py — import Understat player minutes into PlayerMatchMinutes table.

Run inside the backend container:
    docker exec -e PYTHONPATH=/app <container> python /tmp/import_understat_rosters.py

Reads /tmp/understat_rosters.json and correlates each match with a DB fixture
by home_team + away_team + kickoff_date (with team name normalization).
Inserts PlayerMatchMinutes rows (skips existing on conflict).
"""

import asyncio
import json
from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session
from app.models.fixtures import Fixture
from app.models.player_match_minutes import PlayerMatchMinutes

DATA_PATH = "/tmp/understat_rosters.json"

TEAM_NAME_MAP = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves",
    "tottenham hotspur": "tottenham",
    "brighton & hove albion": "brighton",
    "west ham united": "west ham",
    "leicester city": "leicester",
    "ipswich town": "ipswich",
    "paris saint-germain": "psg",
    "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon",
    "stade de reims": "reims",
    "stade brestois 29": "brest",
    "rc strasbourg alsace": "strasbourg",
    "stade rennais fc": "rennes",
    "fc nantes": "nantes",
    "ogc nice": "nice",
    "montpellier hsc": "montpellier",
    "rc lens": "lens",
    "toulouse fc": "toulouse",
    "borussia dortmund": "dortmund",
    "bayer leverkusen": "leverkusen",
    "eintracht frankfurt": "frankfurt",
    "vfb stuttgart": "stuttgart",
    "sc freiburg": "freiburg",
    "1. fc union berlin": "union berlin",
    "1. fsv mainz 05": "mainz",
    "fc augsburg": "augsburg",
    "1. fc heidenheim 1846": "heidenheim",
    "sv werder bremen": "werder bremen",
    "borussia mönchengladbach": "gladbach",
    "vfl wolfsburg": "wolfsburg",
    "vfl bochum": "bochum",
    "fc st. pauli": "st. pauli",
    "atletico madrid": "atlético madrid",
    "deportivo alaves": "alavés",
    "leganes": "leganés",
    "real valladolid": "valladolid",
    "ac milan": "milan",
    "hellas verona": "verona",
}


def norm_team(name: str) -> str:
    n = name.lower().strip()
    return TEAM_NAME_MAP.get(n, n)


def teams_match(understat_name: str, db_name: str) -> bool:
    return norm_team(understat_name) == db_name.lower().strip()


async def find_fixture(session, home: str, away: str, match_date_str: str) -> Fixture | None:
    """Find a DB fixture by team names + date (±1 day tolerance for timezone shifts)."""
    match_date = date.fromisoformat(match_date_str)
    # Load fixtures in ±1 day window then match by name
    result = await session.execute(
        select(Fixture).where(
            Fixture.kickoff_utc >= (match_date - timedelta(days=1)).isoformat(),
            Fixture.kickoff_utc <= (match_date + timedelta(days=2)).isoformat(),
            Fixture.status == "finished",
        )
    )
    fixtures = result.scalars().all()
    for fx in fixtures:
        if teams_match(home, fx.home_team) and teams_match(away, fx.away_team):
            return fx
    return None


async def main():
    with open(DATA_PATH) as f:
        rosters: dict = json.load(f)

    print(f"Loaded {len(rosters)} matches from {DATA_PATH}")

    total_inserted = 0
    total_skipped = 0
    not_found = 0

    async with async_session() as session:
        for mid, match in rosters.items():
            home = match["home"]
            away = match["away"]
            match_date = match["date"]
            players = match["players"]

            fixture = await find_fixture(session, home, away, match_date)
            if fixture is None:
                print(f"  SKIP: no fixture for {home} vs {away} ({match_date})")
                not_found += 1
                continue

            inserted = 0
            for p in players:
                if not p["name"]:
                    continue
                # Use INSERT ... ON CONFLICT DO NOTHING for idempotency
                stmt = pg_insert(PlayerMatchMinutes).values(
                    fixture_id=fixture.id,
                    player_name=p["name"],
                    minutes_played=p["minutes"],
                ).on_conflict_do_nothing(constraint="uq_player_match_minutes")
                result = await session.execute(stmt)
                if result.rowcount > 0:
                    inserted += 1
                else:
                    total_skipped += 1

            total_inserted += inserted
            print(f"  {home} vs {away} ({match_date}) fixture={fixture.id}: {inserted} rows inserted")

        await session.commit()

    print(f"\nDone — {total_inserted} rows inserted, {total_skipped} skipped (already exist), {not_found} matches not found in DB")


asyncio.run(main())
```

**Step 2: Verify imports work locally (no DB connection needed)**

```bash
cd /Users/yohan.resin/Ev0/backend
uv run python -c "from app.models.player_match_minutes import PlayerMatchMinutes; print('OK')"
```

Expected: `OK`

---

## Task 4: Update `auto_settle.py` for VOID detection

**Files:**
- Modify: `backend/app/ingestion/auto_settle.py`

**New settlement logic:**
1. Check if `PlayerMatchMinutes` data exists for this fixture at all (i.e., we imported the roster)
2. If roster data IS available:
   - Player absent (no row) OR `minutes_played == 0` → **VOID** (pnl = 0.0)
   - Player present + `minutes_played > 0` → check MatchEvents for WON/LOST
3. If roster data NOT available → fall back to current logic (MatchEvents only, no VOID)

This is purely additive: when `PlayerMatchMinutes` is empty for a fixture, behaviour is unchanged from the current implementation.

**Step 1: Rewrite `auto_settle.py`**

```python
"""auto_settle.py — automatic settlement of approved recommendations via MatchEvents.

For each approved recommendation with result=None:
  1. Check if the fixture is finished
  2. If PlayerMatchMinutes data is available for the fixture:
     - Player absent or 0 minutes → VOID
     - Player played (>0 min) → WON/LOST from MatchEvents
  3. If no PlayerMatchMinutes data:
     - WON (goal or assist event found), LOST (no event)
     - VOID must be set manually
  4. Update recommendation: result, pnl, settled_utc
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent
from app.models.player_match_minutes import PlayerMatchMinutes
from app.models.recommendations import Recommendation

logger = logging.getLogger(__name__)

# market_type → list of valid MatchEvent.event_type values
_MARKET_TO_EVENTS: dict[str, list[str]] = {
    "goalscorer": ["goal", "penalty_goal"],
    "anytime_score": ["goal", "penalty_goal"],
    "assist": ["assist"],
    "anytime_assist": ["assist"],
}


async def settle_approved_recommendations(db: AsyncSession) -> int:
    """Settle all unsettled approved recommendations for finished fixtures.

    Uses PlayerMatchMinutes for VOID detection when available.
    Uses MatchEvents (goals/assists) for WON/LOST.
    Returns the number of recommendations settled.
    """
    # 1. Find all approved recs with result=None + finished fixture
    stmt = (
        select(Recommendation, Fixture)
        .join(Fixture, Recommendation.fixture_id == Fixture.id)
        .where(
            Recommendation.status == "approved",
            Recommendation.result.is_(None),
            Fixture.status == "finished",
        )
    )
    rows = (await db.execute(stmt)).all()

    if not rows:
        logger.info("auto_settle: no unsettled approved recs with finished fixtures")
        return 0

    logger.info("auto_settle: %d recs to settle", len(rows))

    settled = 0
    for rec, fixture in rows:
        event_types = _MARKET_TO_EVENTS.get(rec.market_type)
        if event_types is None:
            logger.warning("auto_settle: unknown market_type '%s' for rec %d", rec.market_type, rec.id)
            continue

        # --- VOID detection via PlayerMatchMinutes ---
        # Check if we have minutes data for this fixture
        any_pmm = await db.execute(
            select(PlayerMatchMinutes)
            .where(PlayerMatchMinutes.fixture_id == fixture.id)
            .limit(1)
        )
        has_minutes_data = any_pmm.scalar_one_or_none() is not None

        if has_minutes_data:
            # Look up this specific player's minutes
            pmm_row = await db.execute(
                select(PlayerMatchMinutes).where(
                    PlayerMatchMinutes.fixture_id == fixture.id,
                    PlayerMatchMinutes.player_name == rec.player_name,
                )
            )
            pmm = pmm_row.scalar_one_or_none()

            if pmm is None or pmm.minutes_played == 0:
                # Player didn't play (not in squad or 0 minutes) → VOID
                rec.result = "void"
                rec.pnl = 0.0
                rec.settled_utc = datetime.now(UTC)
                settled += 1
                logger.info(
                    "auto_settle: rec %d (%s %s) → VOID (minutes=%s)",
                    rec.id, rec.player_name, rec.market_type,
                    pmm.minutes_played if pmm else "absent",
                )
                continue
            # Player played — fall through to MatchEvents check

        # --- WON/LOST via MatchEvents ---
        # Check if any MatchEvents exist for this fixture at all
        any_event = await db.execute(
            select(MatchEvent).where(MatchEvent.fixture_id == fixture.id).limit(1)
        )
        if any_event.scalar_one_or_none() is None:
            logger.info(
                "auto_settle: no MatchEvents for fixture %d (%s vs %s) — skipping",
                fixture.id, fixture.home_team, fixture.away_team,
            )
            continue

        player_event = await db.execute(
            select(MatchEvent).where(
                MatchEvent.fixture_id == fixture.id,
                MatchEvent.player_name == rec.player_name,
                MatchEvent.event_type.in_(event_types),
            ).limit(1)
        )
        won = player_event.scalar_one_or_none() is not None

        result = "won" if won else "lost"
        pnl = round(10.0 * (rec.best_odds - 1), 2) if won else -10.0

        rec.result = result
        rec.pnl = pnl
        rec.settled_utc = datetime.now(UTC)
        settled += 1

        logger.info(
            "auto_settle: rec %d (%s %s) → %s pnl=%.2f",
            rec.id, rec.player_name, rec.market_type, result, pnl,
        )

    await db.commit()
    logger.info("auto_settle: committed %d settlements", settled)
    return settled
```

**Step 2: Verify imports work**

```bash
cd /Users/yohan.resin/Ev0/backend
uv run python -c "from app.ingestion.auto_settle import settle_approved_recommendations; print('OK')"
```

Expected: `OK`

---

## Task 5: Deploy to VPS

**Context:** Backend runs from Docker image (not volume mount). Any file change requires rebuilding. Frontend also requires a rebuild. DB migration must run inside the backend container.

**Step 1: Commit and push all changes**

```bash
cd /Users/yohan.resin/Ev0
git add backend/app/models/player_match_minutes.py \
        backend/app/models/__init__.py \
        backend/alembic/versions/011_add_player_match_minutes.py \
        backend/app/ingestion/auto_settle.py \
        ops/fetch_understat_rosters.py \
        ops/import_understat_rosters.py
git commit -m "feat: VOID detection via Understat Playwright rosters (PlayerMatchMinutes)"
git push
```

**Step 2: SSH to VPS and pull + rebuild backend**

```bash
ssh root@213.130.144.204
cd /etc/dokploy/compose/ev0-compose-z5hvqt/code
git pull
docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker
```

**Step 3: Run the migration**

```bash
ssh root@213.130.144.204 "docker exec ev0-compose-z5hvqt-backend-1 alembic upgrade head"
```

Expected: `Running upgrade 010 -> 011, Add player_match_minutes table`

**Step 4: Run the full Playwright fetch locally**

```bash
cd /Users/yohan.resin/Ev0
python ops/fetch_understat_rosters.py /tmp/understat_rosters.json
```

Expected: output shows N matches per league, all with player counts ~22-28.

**Step 5: Copy JSON to VPS and import**

```bash
scp /tmp/understat_rosters.json root@213.130.144.204:/tmp/understat_rosters.json
scp ops/import_understat_rosters.py root@213.130.144.204:/tmp/import_understat_rosters.py
ssh root@213.130.144.204 "docker cp /tmp/understat_rosters.json ev0-compose-z5hvqt-backend-1:/tmp/ && docker cp /tmp/import_understat_rosters.py ev0-compose-z5hvqt-backend-1:/tmp/ && docker exec -e PYTHONPATH=/app ev0-compose-z5hvqt-backend-1 python /tmp/import_understat_rosters.py"
```

Expected: `Done — N rows inserted, M skipped, K matches not found`

**Step 6: Test auto-settle with VOID detection**

```bash
ssh root@213.130.144.204 "docker compose -p ev0-compose-z5hvqt exec backend curl -s -X POST http://localhost:8000/api/v1/history/settle | python3 -m json.tool"
```

Expected: `{"settled": N}` — check that some bets are now settled as "void" (player not in team sheet).

**Step 7: Check logs for VOID settlements**

```bash
ssh root@213.130.144.204 "docker logs ev0-compose-z5hvqt-backend-1 --tail 50 | grep -i 'auto_settle'"
```

Look for lines like: `auto_settle: rec N (Player Name goalscorer) → VOID (minutes=absent)`

---

## Troubleshooting

**`window.rostersData` returns `null`:** The SPA may not have finished loading. Increase timeout, or wait for a specific DOM element: `await page.wait_for_selector(".match-rosters", timeout=30000)` before evaluating. Alternatively, intercept network responses using `page.on("response", ...)` to capture the XHR call that loads the data.

**Team name not matched:** Add new entries to `TEAM_NAME_MAP` in `import_understat_rosters.py`. The import script prints `SKIP: no fixture for ...` for unmatched teams — check those and add mappings.

**`uq_player_match_minutes` conflict during import:** Script uses `ON CONFLICT DO NOTHING` — safe to rerun.

**VOID too aggressive (player matched by absent name):** Player name in Understat may differ from our DB. Check `PlayerMatchMinutes` for that fixture: `SELECT player_name FROM player_match_minutes WHERE fixture_id = N`. If the player's name is slightly different, the VOID logic won't find them (will fall through to MatchEvents WON/LOST check). This is acceptable — it's better to miss a VOID than to void a real bet.
