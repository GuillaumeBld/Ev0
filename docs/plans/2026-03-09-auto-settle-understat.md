# Auto-Settlement via Understat Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Automatically settle approved recommendations by scraping per-match player data from Understat — VOID if player had 0 minutes, WON/LOST based on goals/assists.

**Architecture:** (1) New ingestion module `understat_match.py` fetches league match calendars (datesData) and per-match rosters (rostersData) from Understat HTML. (2) New `auto_settle.py` module loops over finished fixtures with unsettled approved recs, maps to Understat match IDs via team name + date, fetches rosters, and applies VOID/WON/LOST logic. (3) APScheduler job runs every 3 hours.

**Tech Stack:** FastAPI, SQLAlchemy async, httpx, APScheduler, Understat HTML JSON extraction (already used in `app/ingestion/understat.py`)

---

### Task 1 : Understat match-level scraper

**Files:**
- Create: `backend/app/ingestion/understat_match.py`

Understat embeds two JS variables in `/league/{slug}/{year}`:
- `datesData` — dict keyed by understat match ID, fields: `id`, `isResult`, `h` (home: `{id, title}`), `a` (away: `{id, title}`), `datetime` (ISO string)

In `/match/{understat_id}`:
- `rostersData` — dict with `h` and `a`, each a dict keyed by player_id. Each player has: `player` (name), `time` (minutes played as string), `goals` (string), `assists` (string), `key_passes` (string)

**Step 1: Create the file**

```python
"""understat_match.py — fetch per-match player data from Understat.

Provides:
  - fetch_league_match_ids(league, season) → list of MatchRef
  - fetch_match_roster(understat_id) → list of PlayerMatchRow
"""

import asyncio
from dataclasses import dataclass
from datetime import date

import httpx

from app.ingestion.understat import LEAGUE_SLUGS, _extract_json_var, get_understat_season

UNDERSTAT_BASE_URL = "https://understat.com"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
RATE_LIMIT = 2.0  # seconds between requests


@dataclass
class MatchRef:
    understat_id: str
    home_team: str
    away_team: str
    match_date: date  # UTC date of kickoff


@dataclass
class PlayerMatchRow:
    player_name: str
    team_side: str       # "h" or "a"
    minutes: int
    goals: int
    assists: int


async def fetch_league_match_ids(
    league: str,
    season: str = "2025-2026",
) -> list[MatchRef]:
    """Fetch all finished match refs for a league+season from Understat.

    Returns only matches where isResult=True (finished).
    """
    slug = LEAGUE_SLUGS.get(league)
    if not slug:
        raise ValueError(f"Unknown league: {league}")

    season_year = get_understat_season(season)
    url = f"{UNDERSTAT_BASE_URL}/league/{slug}/{season_year}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        html = resp.text

    dates_data = _extract_json_var(html, "datesData")
    if not dates_data:
        return []

    matches: list[MatchRef] = []
    for match_id, m in dates_data.items():
        if not m.get("isResult"):
            continue
        dt_str = m.get("datetime", "")  # "2025-08-17 18:00:00"
        try:
            match_date = date.fromisoformat(dt_str[:10])
        except ValueError:
            continue
        matches.append(
            MatchRef(
                understat_id=str(match_id),
                home_team=m["h"]["title"],
                away_team=m["a"]["title"],
                match_date=match_date,
            )
        )

    return matches


async def fetch_match_roster(understat_id: str) -> list[PlayerMatchRow]:
    """Fetch per-player stats for a single finished match from Understat.

    Returns both home and away squads (players listed in rostersData).
    Minutes=0 means player did not play (unused sub or not in squad).
    """
    url = f"{UNDERSTAT_BASE_URL}/match/{understat_id}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_HEADERS)
        resp.raise_for_status()
        html = resp.text

    rosters_data = _extract_json_var(html, "rostersData")
    if not rosters_data:
        return []

    rows: list[PlayerMatchRow] = []
    for side in ("h", "a"):
        side_data = rosters_data.get(side, {})
        for _pid, p in side_data.items():
            rows.append(
                PlayerMatchRow(
                    player_name=p.get("player", ""),
                    team_side=side,
                    minutes=int(p.get("time", 0) or 0),
                    goals=int(p.get("goals", 0) or 0),
                    assists=int(p.get("assists", 0) or 0),
                )
            )

    return rows
```

**Step 2: Verify imports work**

```bash
cd /Users/yohan.resin/Ev0/backend
uv run python -c "from app.ingestion.understat_match import fetch_league_match_ids; print('OK')"
```
Expected: `OK`

---

### Task 2 : Auto-settlement module

**Files:**
- Create: `backend/app/ingestion/auto_settle.py`

**Settlement logic:**
- Player absent from rostersData (not in squad) → VOID
- Player in rostersData but `minutes == 0` → VOID
- Player played (`minutes > 0`) + `goals >= 1` (goalscorer market) → WON
- Player played (`minutes > 0`) + `assists >= 1` (assist market) → WON
- Player played (`minutes > 0`) but no goal/assist → LOST
- P&L: won = `10.0 * (best_odds - 1)`, lost = `-10.0`, void = `0.0`

**Team name normalization** — Understat names differ from our DB names:

```python
TEAM_NAME_MAP = {
    # Premier League
    "Manchester City": "Man City",
    "Manchester United": "Man Utd",
    "Nottingham Forest": "Nott'm Forest",
    "Newcastle United": "Newcastle",
    "Wolverhampton Wanderers": "Wolves",
    "Tottenham Hotspur": "Tottenham",
    "Brighton & Hove Albion": "Brighton",
    "West Ham United": "West Ham",
    "Leicester City": "Leicester",
    "Ipswich Town": "Ipswich",
    # Ligue 1
    "Paris Saint-Germain": "PSG",
    "Olympique de Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon",
    "Stade de Reims": "Reims",
    "Stade Brestois 29": "Brest",
    "RC Strasbourg Alsace": "Strasbourg",
    "Stade Rennais FC": "Rennes",
    "FC Nantes": "Nantes",
    "OGC Nice": "Nice",
    "Montpellier HSC": "Montpellier",
    "RC Lens": "Lens",
    "Toulouse FC": "Toulouse",
    # Bundesliga
    "Bayern Munich": "Bayern München",
    "Borussia Dortmund": "Dortmund",
    "RB Leipzig": "RB Leipzig",
    "Bayer Leverkusen": "Leverkusen",
    "Eintracht Frankfurt": "Frankfurt",
    "VfB Stuttgart": "Stuttgart",
    "SC Freiburg": "Freiburg",
    "1. FC Union Berlin": "Union Berlin",
    "1. FSV Mainz 05": "Mainz",
    "FC Augsburg": "Augsburg",
    "1. FC Heidenheim 1846": "Heidenheim",
    "SV Werder Bremen": "Werder Bremen",
    "Borussia Mönchengladbach": "Gladbach",
    "VfL Wolfsburg": "Wolfsburg",
    "VfL Bochum": "Bochum",
    "Holstein Kiel": "Kiel",
    "FC St. Pauli": "St. Pauli",
    # La Liga
    "Real Madrid": "Real Madrid",
    "FC Barcelona": "Barcelona",
    "Atletico Madrid": "Atlético Madrid",
    "Athletic Club": "Athletic Club",
    "Real Sociedad": "Real Sociedad",
    "Villarreal": "Villarreal",
    "Real Betis": "Real Betis",
    "Valencia": "Valencia",
    "Sevilla": "Sevilla",
    "Celta Vigo": "Celta Vigo",
    "Girona": "Girona",
    "Getafe": "Getafe",
    "Osasuna": "Osasuna",
    "Las Palmas": "Las Palmas",
    "Deportivo Alaves": "Alavés",
    "Leganes": "Leganés",
    "Mallorca": "Mallorca",
    "Rayo Vallecano": "Rayo Vallecano",
    "Espanyol": "Espanyol",
    "Real Valladolid": "Valladolid",
    # Serie A
    "Inter": "Inter",
    "Juventus": "Juventus",
    "AC Milan": "Milan",
    "Napoli": "Napoli",
    "Atalanta": "Atalanta",
    "Fiorentina": "Fiorentina",
    "Lazio": "Lazio",
    "Roma": "Roma",
    "Torino": "Torino",
    "Bologna": "Bologna",
    "Udinese": "Udinese",
    "Genoa": "Genoa",
    "Monza": "Monza",
    "Empoli": "Empoli",
    "Hellas Verona": "Verona",
    "Parma": "Parma",
    "Cagliari": "Cagliari",
    "Como": "Como",
    "Venezia": "Venezia",
    "Lecce": "Lecce",
}
```

**Step 1: Create the file**

```python
"""auto_settle.py — automatic settlement of approved recommendations via Understat.

For each approved recommendation with result=None:
  1. Check if the fixture is finished
  2. Find the corresponding Understat match (by team names + date)
  3. Fetch the match roster from Understat
  4. Determine result: VOID (0 minutes), WON (goal or assist), LOST (played but no event)
  5. Update recommendation: result, pnl, settled_utc
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.understat_match import (
    MatchRef,
    PlayerMatchRow,
    fetch_league_match_ids,
    fetch_match_roster,
)
from app.models.fixtures import Fixture
from app.models.recommendations import Recommendation

logger = logging.getLogger(__name__)

RATE_LIMIT = 2.0  # seconds between Understat match requests

# Leagues to auto-settle (must match fixture.league values in DB)
LEAGUES = ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a"]

# Understat team name → our DB team name (reverse of TEAM_NAME_MAP)
# We normalize both sides to lowercase for matching
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


def _normalize(name: str) -> str:
    """Lowercase + strip for fuzzy matching."""
    return name.lower().strip()


def _match_team(understat_name: str, db_name: str) -> bool:
    """Check if an Understat team name corresponds to a DB team name."""
    un = _normalize(understat_name)
    db = _normalize(db_name)
    # Direct match
    if un == db:
        return True
    # Map Understat → normalized form and compare
    mapped = TEAM_NAME_MAP.get(un, un)
    return mapped == db


def _find_understat_match(
    refs: list[MatchRef],
    home_team: str,
    away_team: str,
    kickoff_date,  # datetime.date
) -> MatchRef | None:
    """Find the Understat match ref for a given fixture."""
    for ref in refs:
        if ref.match_date != kickoff_date:
            continue
        if _match_team(ref.home_team, home_team) and _match_team(ref.away_team, away_team):
            return ref
    return None


def _determine_result(
    player_name: str,
    market_type: str,
    roster: list[PlayerMatchRow],
) -> tuple[str, float | None]:
    """Return (result, pnl_if_known) for a recommendation given the match roster.

    result is one of: 'won', 'lost', 'void', or None (player not found → skip)
    """
    name_lower = player_name.lower()
    # Find player in roster (case-insensitive substring match as fallback)
    player_row: PlayerMatchRow | None = None
    for row in roster:
        if row.player_name.lower() == name_lower:
            player_row = row
            break
    # Fuzzy fallback: check if our name is contained in Understat name or vice versa
    if player_row is None:
        for row in roster:
            rn = row.player_name.lower()
            if name_lower in rn or rn in name_lower:
                player_row = row
                break

    # Player completely absent from squad (not on team sheet) → VOID
    if player_row is None:
        logger.debug("Player '%s' not found in roster → VOID", player_name)
        return "void", 0.0

    # In squad but 0 minutes → VOID
    if player_row.minutes == 0:
        return "void", 0.0

    # Played — check event
    if market_type in ("goalscorer", "anytime_score"):
        won = player_row.goals >= 1
    elif market_type in ("assist", "anytime_assist"):
        won = player_row.assists >= 1
    else:
        # Unknown market — skip
        return None, None

    return ("won" if won else "lost"), None  # pnl computed from odds later


async def settle_approved_recommendations(db: AsyncSession) -> int:
    """Settle all unsettled approved recommendations for finished fixtures.

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

    # 2. Group by (league, date) to batch Understat fetches
    by_league: dict[str, list[MatchRef]] = {}

    # Collect unique leagues needed
    leagues_needed = {rec.fixture.league if hasattr(rec, 'fixture') else fixture.league
                      for rec, fixture in rows}
    # rows is list of (Recommendation, Fixture) tuples
    leagues_needed = {fixture.league for _, fixture in rows}

    for league in leagues_needed:
        if league not in LEAGUES:
            continue
        try:
            refs = await fetch_league_match_ids(league)
            by_league[league] = refs
            logger.info("auto_settle: fetched %d match refs for %s", len(refs), league)
        except Exception as e:
            logger.warning("auto_settle: failed to fetch Understat matches for %s: %s", league, e)

    # 3. Cache fetched rosters to avoid duplicate requests
    roster_cache: dict[str, list[PlayerMatchRow]] = {}

    settled = 0
    for rec, fixture in rows:
        refs = by_league.get(fixture.league, [])
        match_ref = _find_understat_match(
            refs,
            fixture.home_team,
            fixture.away_team,
            fixture.kickoff_utc.date(),
        )

        if match_ref is None:
            logger.warning(
                "auto_settle: no Understat match found for %s vs %s on %s",
                fixture.home_team,
                fixture.away_team,
                fixture.kickoff_utc.date(),
            )
            continue

        # Fetch roster (cached)
        if match_ref.understat_id not in roster_cache:
            try:
                roster = await fetch_match_roster(match_ref.understat_id)
                roster_cache[match_ref.understat_id] = roster
                await asyncio.sleep(RATE_LIMIT)
            except Exception as e:
                logger.warning(
                    "auto_settle: failed to fetch roster for match %s: %s",
                    match_ref.understat_id,
                    e,
                )
                continue
        roster = roster_cache[match_ref.understat_id]

        result, pnl_override = _determine_result(rec.player_name, rec.market_type, roster)
        if result is None:
            continue  # unknown market — skip

        if pnl_override is not None:
            pnl = pnl_override
        elif result == "won":
            pnl = round(10.0 * (rec.best_odds - 1), 2)
        elif result == "lost":
            pnl = -10.0
        else:  # void
            pnl = 0.0

        rec.result = result
        rec.pnl = pnl
        rec.settled_utc = datetime.now(UTC)
        settled += 1
        logger.info(
            "auto_settle: settled rec %d (%s %s) → %s (pnl=%.2f)",
            rec.id,
            rec.player_name,
            rec.market_type,
            result,
            pnl,
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

### Task 3 : Worker job

**Files:**
- Modify: `backend/app/worker.py`

**Step 1: Add the import** at the top of `worker.py` with the other ingestion imports:

```python
from app.ingestion.auto_settle import settle_approved_recommendations
```

**Step 2: Add the job function** after `job_expire_recommendations`:

```python
async def job_auto_settle():
    """Every 3 hours: auto-settle approved recommendations via Understat."""
    logger.info("=== Starting auto-settle job ===")
    try:
        async with async_session() as session:
            count = await settle_approved_recommendations(session)
        logger.info("auto_settle: settled %d recommendations", count)
    except Exception:
        logger.exception("auto_settle job failed")
```

**Step 3: Register the trigger** in the `main()` scheduler setup section (where other IntervalTrigger jobs are added):

```python
scheduler.add_job(
    job_auto_settle,
    IntervalTrigger(hours=3),
    id="auto_settle",
    max_instances=1,
    coalesce=True,
)
```

**Step 4: Verify no syntax errors**

```bash
cd /Users/yohan.resin/Ev0/backend
uv run python -c "from app.worker import job_auto_settle; print('OK')"
```
Expected: `OK`

---

### Task 4 : Manual trigger endpoint (optional but useful)

**Files:**
- Modify: `backend/app/api/history.py`

Add a POST endpoint to manually trigger settlement (useful for testing and retroactive settlement):

**Step 1: Add import** at the top of `history.py`:

```python
from app.ingestion.auto_settle import settle_approved_recommendations
```

**Step 2: Add endpoint** after `get_autoflat_history`:

```python
@router.post("/history/settle", response_model=dict)
async def trigger_auto_settle(
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger auto-settlement via Understat for all pending approved recs."""
    count = await settle_approved_recommendations(db)
    return {"settled": count}
```

**Step 3: Verify no syntax errors**

```bash
cd /Users/yohan.resin/Ev0/backend
uv run python -c "from app.api.history import router; print('OK')"
```
Expected: `OK`

---

### Task 5 : Frontend — Settle button in history page

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/dashboard/history/page.tsx`

**Step 1: Add API function** in `api.ts` after `getAutoflatHistory`:

```ts
export async function triggerAutoSettle(): Promise<{ settled: number }> {
  const { data } = await api.post('/api/v1/history/settle')
  return data
}
```

**Step 2: Add button** in `history/page.tsx` header (next to Export CSV):

Add import:
```ts
import { triggerAutoSettle } from '@/lib/api'
```

Add state + mutation:
```ts
const [settling, setSettling] = useState(false)

const handleAutoSettle = async () => {
  setSettling(true)
  try {
    const res = await triggerAutoSettle()
    queryClient.invalidateQueries({ queryKey: ['history-approved'] })
    queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] })
    alert(`${res.settled} paris settlés automatiquement`)
  } finally {
    setSettling(false)
  }
}
```

Add button in header div (after Export CSV button):
```tsx
<button
  onClick={handleAutoSettle}
  disabled={settling}
  className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-500 disabled:opacity-50 text-white rounded-lg transition-colors"
>
  {settling ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
  Auto-settle
</button>
```

---

### Task 6 : Deploy to VPS

**Step 1: Copy backend files**
```bash
VPS="root@213.130.144.204"
scp backend/app/ingestion/understat_match.py "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/backend/app/ingestion/understat_match.py"
scp backend/app/ingestion/auto_settle.py "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/backend/app/ingestion/auto_settle.py"
scp backend/app/worker.py "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/backend/app/worker.py"
scp backend/app/api/history.py "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/backend/app/api/history.py"
```

**Step 2: Copy frontend files**
```bash
scp frontend/src/lib/api.ts "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/frontend/src/lib/api.ts"
scp frontend/src/app/dashboard/history/page.tsx "$VPS:/etc/dokploy/compose/ev0-compose-z5hvqt/code/frontend/src/app/dashboard/history/page.tsx"
```

**Step 3: Restart backend + worker**
```bash
ssh root@213.130.144.204 "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && docker compose -p ev0-compose-z5hvqt --env-file .env up -d --force-recreate --no-build backend worker"
```

**Step 4: Rebuild frontend**
```bash
ssh root@213.130.144.204 "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build frontend 2>&1 | tail -5"
```

**Step 5: Test manually**
```bash
ssh root@213.130.144.204 "docker compose -p ev0-compose-z5hvqt exec backend curl -s -X POST http://localhost:8000/api/v1/history/settle | python3 -m json.tool"
```
Expected: `{"settled": N}` with N ≥ 0

**Step 6: Check logs**
```bash
ssh root@213.130.144.204 "docker logs ev0-compose-z5hvqt-backend-1 --tail 30 | grep -i settle"
```
