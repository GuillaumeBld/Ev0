# OddsPortal URL Auto-Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remplir automatiquement `oddsportal_poll_state` en scrappant les pages listing OddsPortal quotidiennement, sans intervention manuelle.

**Architecture:** Un scraper Playwright de pages listing (scaffold avec placeholders) produit des `OddsPortalMatchItem`. Un matcher temporel + fuzzy (difflib, scipy assignment) les associe aux `Fixture` DB en réutilisant `normalize_team_name()` et `CanonicalTeam.aliases`. Les nouveaux alias confirmés sont persistés. Un job worker quotidien à 8h UTC orchestre tout.

**Tech Stack:** Playwright async, difflib (stdlib), scipy.optimize.linear_sum_assignment, numpy, SQLAlchemy async, APScheduler CronTrigger.

---

## Structure des fichiers

| Fichier | Rôle |
|---------|------|
| `backend/app/ingestion/oddsportal_league_discoverer.py` | Créer — scraper Playwright pages listing, retourne `OddsPortalMatchItem` |
| `backend/app/ingestion/oddsportal_fixture_matcher.py` | Créer — matching temporal+fuzzy+assignment, upsert poll_state |
| `backend/app/worker.py` | Modifier — ajouter `job_discover_oddsportal_urls` |
| `backend/tests/ingestion/test_oddsportal_fixture_matcher.py` | Créer — tests unitaires du matcher (pas de Playwright) |

---

## Task 1: OddsPortalMatchItem + League Discoverer scaffold

**Files:**
- Create: `backend/app/ingestion/oddsportal_league_discoverer.py`

> Note : les sélecteurs CSS sont des placeholders comme dans `oddsportal_scraper.py`. Ils doivent être vérifiés sur le DOM live en production.

- [ ] **Step 1: Créer `backend/app/ingestion/oddsportal_league_discoverer.py`**

```python
"""OddsPortal league listing scraper — auto-discovers upcoming match URLs.

Playwright scrapes the OddsPortal league listing pages (React SPA) to
extract upcoming match metadata (teams, kickoff time, URL).

SELECTORS: Placeholders — must be verified against live OddsPortal DOM.
To inspect: playwright open https://www.oddsportal.com/football/france/ligue-1/
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from playwright.async_api import Browser, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

_NAV_TIMEOUT_MS = 30_000
_LOAD_TIMEOUT_MS = 10_000
_DISCOVERY_WINDOW_DAYS = 7

# --- League listing URLs ---
ODDSPORTAL_LEAGUE_URLS: dict[str, str] = {
    "ligue_1":          "https://www.oddsportal.com/football/france/ligue-1/",
    "premier_league":   "https://www.oddsportal.com/football/england/premier-league/",
    "bundesliga":       "https://www.oddsportal.com/football/germany/bundesliga/",
    "la_liga":          "https://www.oddsportal.com/football/spain/laliga/",
    "serie_a":          "https://www.oddsportal.com/football/italy/serie-a/",
    "champions_league": "https://www.oddsportal.com/football/europe/champions-league/",
}

# --- Selectors (MUST be verified against live OddsPortal DOM) ---
# OddsPortal is a React SPA — class names may be hashed/unstable.
# Preferred: use data-testid or aria attributes where available.
_SEL_MATCH_ROW = "div[data-testid='match-row']"          # each upcoming match
_SEL_HOME_TEAM = "[data-testid='home-team-name']"         # home team within row
_SEL_AWAY_TEAM = "[data-testid='away-team-name']"         # away team within row
_SEL_MATCH_LINK = "a[href*='/football/']"                 # link to match page
_SEL_KICKOFF_TIME = "p[class*='date']"                   # kickoff datetime text


@dataclass
class OddsPortalMatchItem:
    home_raw: str          # nom brut affiché sur OddsPortal
    away_raw: str
    kickoff_utc: datetime  # converti en UTC
    match_url: str         # URL complète du match
    league: str            # clé interne (ex. "ligue_1")


async def discover_league(
    league: str,
    page: Page,
) -> list[OddsPortalMatchItem]:
    """Scrape the OddsPortal listing page for one league.

    Returns upcoming matches within the next DISCOVERY_WINDOW_DAYS days.
    Returns [] on any error — caller continues with other leagues.
    """
    url = ODDSPORTAL_LEAGUE_URLS[league]
    cutoff = datetime.now(timezone.utc) + timedelta(days=_DISCOVERY_WINDOW_DAYS)

    try:
        await page.goto(url, wait_until="networkidle", timeout=_NAV_TIMEOUT_MS)
        await page.wait_for_load_state("domcontentloaded", timeout=_LOAD_TIMEOUT_MS)
    except PlaywrightTimeout:
        logger.warning("discoverer: navigation timeout for league=%s url=%s", league, url)
        return []
    except Exception as exc:
        logger.warning("discoverer: failed to load league=%s: %s", league, exc)
        return []

    items: list[OddsPortalMatchItem] = []

    try:
        rows = await page.query_selector_all(_SEL_MATCH_ROW)
        logger.info("discoverer: league=%s found %d rows", league, len(rows))

        for row in rows:
            try:
                home_el = await row.query_selector(_SEL_HOME_TEAM)
                away_el = await row.query_selector(_SEL_AWAY_TEAM)
                link_el = await row.query_selector(_SEL_MATCH_LINK)
                time_el = await row.query_selector(_SEL_KICKOFF_TIME)

                if not (home_el and away_el and link_el):
                    continue

                home_raw = (await home_el.text_content() or "").strip()
                away_raw = (await away_el.text_content() or "").strip()
                href = await link_el.get_attribute("href") or ""
                match_url = f"https://www.oddsportal.com{href}" if href.startswith("/") else href

                if not home_raw or not away_raw or not match_url:
                    continue

                # Parse kickoff time — prefer datetime attribute, fall back to text
                kickoff_utc = await _parse_kickoff(time_el)
                if kickoff_utc is None:
                    logger.debug("discoverer: no kickoff time for %s vs %s", home_raw, away_raw)
                    continue

                if kickoff_utc > cutoff:
                    continue

                items.append(OddsPortalMatchItem(
                    home_raw=home_raw,
                    away_raw=away_raw,
                    kickoff_utc=kickoff_utc,
                    match_url=match_url,
                    league=league,
                ))

            except Exception as exc:
                logger.debug("discoverer: error parsing row: %s", exc)
                continue

    except Exception as exc:
        logger.warning("discoverer: error iterating rows for league=%s: %s", league, exc)

    logger.info("discoverer: league=%s discovered %d items", league, len(items))
    return items


async def _parse_kickoff(el: object | None) -> datetime | None:
    """Extract kickoff UTC from a page element.

    Tries: 1) datetime attribute (ISO), 2) data-kickoff attribute (Unix timestamp).
    Returns None if unparseable.
    """
    if el is None:
        return None
    try:
        # Try ISO datetime attribute first
        dt_attr = await el.get_attribute("datetime")
        if dt_attr:
            dt = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)

        # Try Unix timestamp attribute
        ts_attr = await el.get_attribute("data-kickoff")
        if ts_attr and ts_attr.isdigit():
            return datetime.fromtimestamp(int(ts_attr), tz=timezone.utc)

    except Exception:
        pass
    return None


async def discover_all_leagues(browser: Browser) -> list[OddsPortalMatchItem]:
    """Scrape all configured leagues. Each league gets its own page.

    Leagues that fail are skipped — the rest are returned.
    """
    all_items: list[OddsPortalMatchItem] = []

    for league in ODDSPORTAL_LEAGUE_URLS:
        page = await browser.new_page()
        try:
            items = await discover_league(league, page)
            all_items.extend(items)
        finally:
            await page.close()

    logger.info("discoverer: total=%d items across all leagues", len(all_items))
    return all_items
```

- [ ] **Step 2: Vérifier que le module s'importe sans erreur**

```bash
cd /path/to/worktree/backend && /path/to/.venv/bin/python -c "from app.ingestion.oddsportal_league_discoverer import OddsPortalMatchItem, ODDSPORTAL_LEAGUE_URLS, discover_all_leagues; print('OK', len(ODDSPORTAL_LEAGUE_URLS), 'leagues')"
```

Attendu : `OK 6 leagues`

- [ ] **Step 3: Commit**

```bash
git add backend/app/ingestion/oddsportal_league_discoverer.py
git commit -m "feat: OddsPortalMatchItem dataclass + league discoverer scaffold (selectors to verify on live site)"
```

---

## Task 2: OddsPortalFixtureMatcher (TDD)

**Files:**
- Create: `backend/tests/ingestion/test_oddsportal_fixture_matcher.py`
- Create: `backend/app/ingestion/oddsportal_fixture_matcher.py`

Le matcher est pur Python + SQLAlchemy — entièrement testable sans Playwright.

### Contexte critique à connaître

- `normalize_team_name(name)` de `app.ingestion.fixture_matcher` : normalise, consulte `TEAM_ALIASES`, retourne un slug ex. `"manchester-city"`
- `CanonicalTeam.aliases` est `ARRAY(Text)` — ex. `["man-city", "man city", "manchester-city"]`
- `Fixture.home_team` / `.away_team` contiennent les noms raw FotMob — ex. `"Manchester City"`
- `scipy.optimize.linear_sum_assignment(cost_matrix)` **minimise** → passer `-score_matrix`
- Seuil d'acceptation : score moyen home+away ≥ 75.0 (sur 100)

- [ ] **Step 1: Créer les tests (doivent échouer)**

Créer `backend/tests/ingestion/test_oddsportal_fixture_matcher.py` :

```python
"""Tests for OddsPortal fixture matcher."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ingestion.oddsportal_fixture_matcher import (
    _pair_score,
    _token_sort_ratio,
    match_items_to_fixtures,
)
from app.ingestion.oddsportal_league_discoverer import OddsPortalMatchItem


def _make_item(home, away, league="ligue_1", offset_min=0):
    return OddsPortalMatchItem(
        home_raw=home,
        away_raw=away,
        kickoff_utc=datetime(2026, 4, 12, 17, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_min),
        match_url=f"https://www.oddsportal.com/test/{home.lower().replace(' ', '-')}/",
        league=league,
    )


def _make_fixture(home, away, league="ligue_1", offset_min=0, fid=1):
    fix = MagicMock()
    fix.id = fid
    fix.home_team = home
    fix.away_team = away
    fix.league = league
    fix.kickoff_utc = datetime(2026, 4, 12, 17, 0, tzinfo=timezone.utc) + timedelta(minutes=offset_min)
    return fix


class TestTokenSortRatio:
    def test_identical_strings(self):
        assert _token_sort_ratio("manchester city", "manchester city") == 100.0

    def test_token_order_independent(self):
        # token_sort_ratio sorts tokens before comparing
        score = _token_sort_ratio("city manchester", "manchester city")
        assert score == 100.0

    def test_partial_match(self):
        score = _token_sort_ratio("manchester city", "man city")
        assert score > 70.0

    def test_completely_different(self):
        score = _token_sort_ratio("psg", "lyon")
        assert score < 50.0


class TestPairScore:
    def test_exact_match_via_normalize(self):
        # "Man City" normalizes to "manchester-city" via TEAM_ALIASES
        # fixture has "Manchester City" which normalizes to "manchester-city"
        item = _make_item("Man City", "PSG")
        fixture = _make_fixture("Manchester City", "Paris Saint-Germain")
        alias_index = {}
        score = _pair_score(item, fixture, alias_index)
        assert score >= 75.0

    def test_low_score_for_wrong_fixture(self):
        item = _make_item("PSG", "Lyon")
        fixture = _make_fixture("Manchester City", "Arsenal")
        alias_index = {}
        score = _pair_score(item, fixture, alias_index)
        assert score < 50.0

    def test_alias_index_boosts_score(self):
        # "psg" in alias_index → 100 for home
        item = _make_item("PSG", "Lyon")
        fixture = _make_fixture("Paris Saint-Germain", "Olympique Lyonnais")
        # Simulate alias_index: normalize("PSG") = "psg" → maps to CT for PSG
        ct = MagicMock()
        ct.name_fr = "Paris Saint-Germain"
        alias_index = {"psg": ct}
        score = _pair_score(item, fixture, alias_index)
        # Even without alias boost working perfectly, normalize handles this via TEAM_ALIASES
        assert score >= 75.0


class TestMatchItemsToFixtures:
    @pytest.mark.asyncio
    async def test_simple_match(self):
        """One item matches one fixture."""
        item = _make_item("Man City", "Arsenal", league="premier_league")
        fixture = _make_fixture("Manchester City", "Arsenal", league="premier_league", fid=42)

        session = AsyncMock()
        # Mock DB queries
        session.execute = AsyncMock(side_effect=_make_execute_side_effects(
            canonical_teams=[],
            fixtures=[fixture],
        ))
        session.commit = AsyncMock()

        results = await match_items_to_fixtures([item], session)

        assert len(results) == 1
        assert results[0][0] == 42
        assert "man-city" in results[0][1] or "oddsportal.com" in results[0][1]

    @pytest.mark.asyncio
    async def test_below_threshold_rejected(self):
        """Item with score < 75 returns no match."""
        item = _make_item("Real Madrid", "Barcelona", league="premier_league")
        fixture = _make_fixture("Manchester City", "Arsenal", league="premier_league", fid=1)

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_make_execute_side_effects(
            canonical_teams=[],
            fixtures=[fixture],
        ))
        session.commit = AsyncMock()

        results = await match_items_to_fixtures([item], session)
        assert results == []

    @pytest.mark.asyncio
    async def test_three_way_assignment(self):
        """3 items at same time → optimal assignment via linear_sum_assignment."""
        now = datetime(2026, 4, 12, 17, 0, tzinfo=timezone.utc)

        items = [
            _make_item("Man City", "Arsenal", league="premier_league", offset_min=0),
            _make_item("Liverpool", "Chelsea", league="premier_league", offset_min=2),
            _make_item("Tottenham", "Man Utd", league="premier_league", offset_min=3),
        ]
        fixtures = [
            _make_fixture("Manchester City", "Arsenal", league="premier_league", fid=1, offset_min=0),
            _make_fixture("Liverpool", "Chelsea", league="premier_league", fid=2, offset_min=0),
            _make_fixture("Tottenham", "Manchester United", league="premier_league", fid=3, offset_min=0),
        ]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_make_execute_side_effects(
            canonical_teams=[],
            fixtures=fixtures,
        ))
        session.commit = AsyncMock()

        results = await match_items_to_fixtures(items, session)

        matched_ids = {r[0] for r in results}
        assert matched_ids == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_empty_items_returns_empty(self):
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_make_execute_side_effects(
            canonical_teams=[], fixtures=[],
        ))
        session.commit = AsyncMock()

        results = await match_items_to_fixtures([], session)
        assert results == []


def _make_execute_side_effects(canonical_teams, fixtures):
    """Helper: return mock execute results for (canonical_teams, fixtures) queries."""
    call_count = 0

    async def side_effect(query):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # First query: canonical_teams
            result.scalars.return_value.all.return_value = canonical_teams
        else:
            # Second query: fixtures
            result.scalars.return_value.all.return_value = fixtures
        return result

    return side_effect
```

- [ ] **Step 2: Vérifier que les tests échouent**

```bash
cd /path/to/worktree && /path/to/.venv/bin/python -m pytest backend/tests/ingestion/test_oddsportal_fixture_matcher.py -v 2>&1 | tail -15
```

Attendu : `ImportError: cannot import name '_pair_score'`

- [ ] **Step 3: Créer `backend/app/ingestion/oddsportal_fixture_matcher.py`**

```python
"""OddsPortal fixture matcher — maps discovered match items to DB fixtures.

Algorithm:
1. Load fixtures from DB (league + 7-day window)
2. Load CanonicalTeam aliases for exact-match acceleration
3. Group OddsPortal items by kickoff window (±5min) to detect conflicts
4. For lone items: pick best candidate (score ≥ 75)
5. For groups: solve as assignment problem (scipy linear_sum_assignment)
6. Persist new alias mappings to CanonicalTeam.aliases
7. Upsert matched (fixture_id, oddsportal_url) into oddsportal_poll_state
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.optimize import linear_sum_assignment
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.fixture_matcher import normalize_team_name
from app.ingestion.oddsportal_league_discoverer import OddsPortalMatchItem
from app.models.canonical_teams import CanonicalTeam
from app.models.fixtures import Fixture
from app.models.poll_state import OddsPortalPollState

logger = logging.getLogger(__name__)

_MATCH_WINDOW = timedelta(minutes=30)
_CONFLICT_WINDOW = timedelta(minutes=5)
_DISCOVERY_WINDOW_DAYS = 8
SCORE_THRESHOLD = 75.0   # 0-100


def _token_sort_ratio(s1: str, s2: str) -> float:
    """Token-sort similarity using difflib (0-100). Sorts tokens before comparing."""
    t1 = " ".join(sorted(s1.split("-")))
    t2 = " ".join(sorted(s2.split("-")))
    return difflib.SequenceMatcher(None, t1, t2).ratio() * 100


def _pair_score(
    item: OddsPortalMatchItem,
    fixture: Fixture,
    alias_index: dict[str, CanonicalTeam],
) -> float:
    """Score for (item, fixture) pair. 0-100. Average of home+away similarity."""
    home_norm = normalize_team_name(item.home_raw)
    away_norm = normalize_team_name(item.away_raw)
    fix_home = normalize_team_name(fixture.home_team or "")
    fix_away = normalize_team_name(fixture.away_team or "")

    # Home score
    if home_norm == fix_home:
        home_score = 100.0
    elif home_norm in alias_index and normalize_team_name(alias_index[home_norm].name_fr) == fix_home:
        home_score = 100.0
    else:
        home_score = _token_sort_ratio(home_norm, fix_home)

    # Away score
    if away_norm == fix_away:
        away_score = 100.0
    elif away_norm in alias_index and normalize_team_name(alias_index[away_norm].name_fr) == fix_away:
        away_score = 100.0
    else:
        away_score = _token_sort_ratio(away_norm, fix_away)

    return (home_score + away_score) / 2.0


def _build_alias_index(canonical_teams: list[CanonicalTeam]) -> dict[str, CanonicalTeam]:
    """Build normalized_alias → CanonicalTeam index."""
    index: dict[str, CanonicalTeam] = {}
    for ct in canonical_teams:
        index[normalize_team_name(ct.name_fr)] = ct
        for alias in (ct.aliases or []):
            index[normalize_team_name(alias)] = ct
    return index


def _time_delta_seconds(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds())


async def match_items_to_fixtures(
    items: list[OddsPortalMatchItem],
    session: AsyncSession,
) -> list[tuple[int, str]]:
    """Match OddsPortal items to DB fixtures. Returns (fixture_id, match_url) pairs."""
    if not items:
        return []

    leagues = {item.league for item in items}
    now = datetime.now(timezone.utc)

    # Load canonical teams for alias lookup
    canonical_teams = (await session.execute(select(CanonicalTeam))).scalars().all()
    alias_index = _build_alias_index(canonical_teams)

    # Load fixtures for relevant leagues within discovery window
    fixtures = (await session.execute(
        select(Fixture).where(
            Fixture.league.in_(leagues),
            Fixture.kickoff_utc >= now - timedelta(hours=2),
            Fixture.kickoff_utc <= now + timedelta(days=_DISCOVERY_WINDOW_DAYS),
        )
    )).scalars().all()

    results: list[tuple[int, str]] = []
    new_aliases: dict[int, list[str]] = {}  # ct.id → new alias strings to append
    processed: set[int] = set()

    for i, item in enumerate(items):
        if i in processed:
            continue

        # Find all items in the same kickoff conflict window (same league, ±5min)
        group_indices = [
            j for j, other in enumerate(items)
            if other.league == item.league
            and _time_delta_seconds(other.kickoff_utc, item.kickoff_utc) <= _CONFLICT_WINDOW.total_seconds()
        ]
        for idx in group_indices:
            processed.add(idx)

        group_items = [items[j] for j in group_indices]

        # Find fixture candidates in match window (±30min from first item in group)
        candidates = [
            f for f in fixtures
            if f.league == item.league
            and _time_delta_seconds(f.kickoff_utc, item.kickoff_utc) <= _MATCH_WINDOW.total_seconds()
        ]

        if not candidates:
            for git in group_items:
                logger.warning(
                    "discoverer_match: no_candidates league=%s %s vs %s @ %s",
                    git.league, git.home_raw, git.away_raw, git.kickoff_utc,
                )
            continue

        if len(group_items) == 1:
            # Simple case: pick best candidate
            best_fix, best_score = _best_candidate(group_items[0], candidates, alias_index)
            if best_fix is not None and best_score >= SCORE_THRESHOLD:
                results.append((best_fix.id, group_items[0].match_url))
                _collect_aliases(group_items[0], best_fix, alias_index, new_aliases)
            else:
                logger.warning(
                    "discoverer_match: low_score league=%s %s vs %s best=%.1f",
                    item.league, item.home_raw, item.away_raw, best_score,
                )
        else:
            # Assignment problem: maximize total score
            score_matrix = np.zeros((len(group_items), len(candidates)))
            for gi, git in enumerate(group_items):
                for ci, cand in enumerate(candidates):
                    score_matrix[gi, ci] = _pair_score(git, cand, alias_index)

            row_ind, col_ind = linear_sum_assignment(-score_matrix)  # negate to maximize
            for gi, ci in zip(row_ind, col_ind):
                score = score_matrix[gi, ci]
                git = group_items[gi]
                fix = candidates[ci]
                if score >= SCORE_THRESHOLD:
                    results.append((fix.id, git.match_url))
                    _collect_aliases(git, fix, alias_index, new_aliases)
                else:
                    logger.warning(
                        "discoverer_match: low_score_assignment league=%s %s vs %s score=%.1f",
                        git.league, git.home_raw, git.away_raw, score,
                    )

    # Persist new alias mappings
    for ct_id, aliases in new_aliases.items():
        await session.execute(
            update(CanonicalTeam)
            .where(CanonicalTeam.id == ct_id)
            .values(aliases=func.array_cat(CanonicalTeam.aliases, aliases))
        )

    # Upsert poll_state
    if results:
        stmt = pg_insert(OddsPortalPollState).values([
            {
                "fixture_id": fid,
                "oddsportal_url": url,
                "next_due_at_utc": datetime.now(timezone.utc),
                "error_streak": 0,
                "stopped": False,
                "stopped_reason": None,
            }
            for fid, url in results
        ])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_poll_state_fixture",
            set_={"oddsportal_url": stmt.excluded.oddsportal_url},
        )
        await session.execute(stmt)

    await session.commit()

    logger.info(
        "discoverer_match: matched %d/%d items to fixtures",
        len(results), len(items),
    )
    return results


def _best_candidate(
    item: OddsPortalMatchItem,
    candidates: list[Fixture],
    alias_index: dict[str, CanonicalTeam],
) -> tuple[Fixture | None, float]:
    best: Fixture | None = None
    best_score = 0.0
    for cand in candidates:
        s = _pair_score(item, cand, alias_index)
        if s > best_score:
            best_score = s
            best = cand
    return best, best_score


def _collect_aliases(
    item: OddsPortalMatchItem,
    fixture: Fixture,
    alias_index: dict[str, CanonicalTeam],
    new_aliases: dict[int, list[str]],
) -> None:
    """If OddsPortal name wasn't a known alias, add it to the matching CanonicalTeam."""
    for raw, team_name in [
        (item.home_raw, fixture.home_team),
        (item.away_raw, fixture.away_team),
    ]:
        norm = normalize_team_name(raw)
        if norm in alias_index:
            continue  # already known
        fix_norm = normalize_team_name(team_name or "")
        ct = alias_index.get(fix_norm)
        if ct is not None and ct.id is not None:
            new_aliases.setdefault(ct.id, []).append(norm)
            alias_index[norm] = ct  # update in-memory for rest of run
            logger.info(
                "discoverer_match: new_alias '%s' → CanonicalTeam(id=%s, name=%s)",
                norm, ct.id, ct.name_fr,
            )
```

- [ ] **Step 4: Vérifier que les tests passent**

```bash
cd /path/to/worktree && /path/to/.venv/bin/python -m pytest backend/tests/ingestion/test_oddsportal_fixture_matcher.py -v 2>&1 | tail -20
```

Attendu : tous verts. Si `test_three_way_assignment` échoue à cause du mock `session.execute` (ordre des appels), ajuster `_make_execute_side_effects` pour retourner les fixtures sur le deuxième appel et les canonical_teams sur le premier.

- [ ] **Step 5: Vérifier la suite complète**

```bash
cd /path/to/worktree && /path/to/.venv/bin/python -m pytest backend/tests/ -q 2>&1 | tail -5
```

Attendu : tous verts.

- [ ] **Step 6: Commit**

```bash
git add backend/app/ingestion/oddsportal_fixture_matcher.py backend/tests/ingestion/test_oddsportal_fixture_matcher.py
git commit -m "feat: OddsPortal fixture matcher — temporal+fuzzy+assignment, alias learning (TDD)"
```

---

## Task 3: Worker job

**Files:**
- Modify: `backend/app/worker.py`

- [ ] **Step 1: Lire worker.py**

Lire `backend/app/worker.py` — repérer le dernier `scheduler.add_job(...)` et le bloc `create_scheduler()`. Les imports existants incluent déjà `CronTrigger` (ligne 17) et `async_playwright` (ligne ~738).

- [ ] **Step 2: Ajouter le job discover**

Ajouter après le dernier `scheduler.add_job(...)` existant dans la fonction `create_scheduler()` (ou `main()` selon la structure) :

```python
# --- OddsPortal URL auto-discovery ---

async def job_discover_oddsportal_urls() -> None:
    """Daily job — scrapes OddsPortal league listings and seeds oddsportal_poll_state."""
    from playwright.async_api import async_playwright

    from app.ingestion.oddsportal_fixture_matcher import match_items_to_fixtures
    from app.ingestion.oddsportal_league_discoverer import discover_all_leagues

    logger.info("job_discover_oddsportal_urls: starting")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                items = await discover_all_leagues(browser)
            finally:
                await browser.close()

        async with async_session() as session:
            results = await match_items_to_fixtures(items, session)

        logger.info("job_discover_oddsportal_urls: done, seeded %d fixtures", len(results))
    except Exception as exc:
        logger.error("job_discover_oddsportal_urls error: %s", exc, exc_info=True)


scheduler.add_job(
    job_discover_oddsportal_urls,
    CronTrigger(hour=8, minute=0),
    id="job_discover_oddsportal_urls",
    max_instances=1,
)
```

- [ ] **Step 3: Vérifier les imports worker**

```bash
cd /path/to/worktree/backend && /path/to/.venv/bin/python -c "import app.worker; print('worker imports OK')" 2>&1 | tail -3
```

Attendu : `worker imports OK`

- [ ] **Step 4: Vérifier la suite complète**

```bash
cd /path/to/worktree && /path/to/.venv/bin/python -m pytest backend/tests/ -q 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/worker.py
git commit -m "feat: worker — add daily job_discover_oddsportal_urls (CronTrigger 8h UTC)"
```

---

## Task 4: Push + déploiement VPS

**Files:** Aucun fichier supplémentaire.

- [ ] **Step 1: Run final test suite**

```bash
cd /path/to/worktree && /path/to/.venv/bin/python -m pytest backend/tests/ -q 2>&1 | tail -5
```

Attendu : tous verts, aucune régression.

- [ ] **Step 2: Merge feature branch → main**

```bash
# Depuis la racine du repo (pas le worktree)
git checkout main
git merge feature/oddsportal-autodiscovery --no-ff -m "feat: OddsPortal URL auto-discovery — daily job, temporal+fuzzy+assignment matcher"
```

- [ ] **Step 3: Push GitHub**

```bash
git push origin main
```

- [ ] **Step 4: Rebuild VPS**

```bash
ssh root@213.130.144.204 "cd /etc/dokploy/compose/ev0-compose-z5hvqt/code && docker compose -p ev0-compose-z5hvqt --env-file .env up -d --build --no-deps backend worker 2>&1 | tail -10"
```

- [ ] **Step 5: Vérifier le job dans les logs**

```bash
ssh root@213.130.144.204 "docker logs ev0-compose-z5hvqt-worker-1 --since=2m 2>&1 | grep discover"
```

Attendu au prochain 8h UTC : `job_discover_oddsportal_urls: starting`

---

## Spec Coverage Check

| Requirement spec | Tâche |
|---|---|
| `OddsPortalMatchItem` dataclass | Task 1 |
| `ODDSPORTAL_LEAGUE_URLS` — 6 leagues | Task 1 |
| `discover_league` Playwright scaffold | Task 1 |
| `discover_all_leagues` (toutes leagues, erreurs isolées) | Task 1 |
| `_token_sort_ratio` difflib | Task 2 |
| `_pair_score` (alias_index + fuzzy) | Task 2 |
| Fenêtre temporelle ±30min | Task 2 |
| Groupement conflits ±5min | Task 2 |
| `linear_sum_assignment` pour groupes | Task 2 |
| Seuil 75.0, log WARNING sous seuil | Task 2 |
| Apprentissage alias → `CanonicalTeam.aliases` | Task 2 |
| Upsert `oddsportal_poll_state` sans écraser `next_due_at_utc` | Task 2 |
| Job worker `CronTrigger(hour=8, minute=0)` | Task 3 |
| `max_instances=1` | Task 3 |
| Erreurs loggées, pas propagées | Task 3 |
