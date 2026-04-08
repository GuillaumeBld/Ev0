"""OddsPortal fixture matcher — maps discovered match items to DB fixtures.

Algorithm:
1. Load CanonicalTeam aliases for exact-match acceleration
2. Load fixtures from DB (relevant leagues + 8-day window)
3. Group items by kickoff conflict window (±5min, same league)
4. Lone items: pick best candidate (score ≥ SCORE_THRESHOLD)
5. Groups: scipy linear_sum_assignment to maximize total score
6. Persist new alias mappings to CanonicalTeam.aliases
7. Upsert matched (fixture_id, oddsportal_url) into oddsportal_poll_state
"""

from __future__ import annotations

import difflib
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.optimize import linear_sum_assignment
from sqlalchemy import func, select, text as sql_text, update
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
_KICKOFF_LOOKBACK = timedelta(hours=2)  # catch matches already in progress at discovery time
SCORE_THRESHOLD = 70.0


def _token_sort_ratio(s1: str, s2: str) -> float:
    """Token-sort similarity using difflib (0-100). Sorts tokens before comparing."""
    t1 = " ".join(sorted(s1.split("-")))
    t2 = " ".join(sorted(s2.split("-")))
    return difflib.SequenceMatcher(None, t1, t2).ratio() * 100


def _build_alias_index(canonical_teams: list[CanonicalTeam]) -> dict[str, CanonicalTeam]:
    """Build normalized_alias → CanonicalTeam lookup."""
    index: dict[str, CanonicalTeam] = {}
    for ct in canonical_teams:
        index[normalize_team_name(ct.name_fr)] = ct
        for alias in (ct.aliases or []):
            index[normalize_team_name(alias)] = ct
    return index


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

    def _side_score(norm: str, fix_norm: str) -> float:
        if norm == fix_norm:
            return 100.0
        ct = alias_index.get(norm)
        if ct is not None and normalize_team_name(ct.name_fr) == fix_norm:
            return 100.0
        return _token_sort_ratio(norm, fix_norm)

    return (_side_score(home_norm, fix_home) + _side_score(away_norm, fix_away)) / 2.0


def _time_delta_s(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds())


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
    """Add new OddsPortal name to CanonicalTeam.aliases if not already known."""
    for raw, team_name in [
        (item.home_raw, fixture.home_team),
        (item.away_raw, fixture.away_team),
    ]:
        norm = normalize_team_name(raw)
        if norm in alias_index:
            continue
        fix_norm = normalize_team_name(team_name or "")
        ct = alias_index.get(fix_norm)
        if ct is not None and ct.id is not None:
            new_aliases.setdefault(ct.id, []).append(norm)
            alias_index[norm] = ct
            logger.info("new_alias '%s' → CanonicalTeam(id=%s, name=%s)", norm, ct.id, ct.name_fr)


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
            Fixture.kickoff_utc >= now - _KICKOFF_LOOKBACK,
            Fixture.kickoff_utc <= now + timedelta(days=_DISCOVERY_WINDOW_DAYS),
        )
    )).scalars().all()

    results: list[tuple[int, str]] = []
    new_aliases: dict[int, list[str]] = {}
    processed: set[int] = set()

    for i, item in enumerate(items):
        if i in processed:
            continue

        # Group items with same kickoff within conflict window
        group_indices = [
            j for j, other in enumerate(items)
            if other.league == item.league
            and _time_delta_s(other.kickoff_utc, item.kickoff_utc) <= _CONFLICT_WINDOW.total_seconds()
        ]
        for idx in group_indices:
            processed.add(idx)

        group_items = [items[j] for j in group_indices]

        # Find fixture candidates in match window
        candidates = [
            f for f in fixtures
            if f.league == item.league
            and _time_delta_s(f.kickoff_utc, item.kickoff_utc) <= _MATCH_WINDOW.total_seconds()
        ]

        if not candidates:
            for g_item in group_items:
                logger.warning(
                    "no_candidates league=%s %s vs %s @ %s",
                    g_item.league, g_item.home_raw, g_item.away_raw, g_item.kickoff_utc,
                )
            continue

        if len(group_items) == 1:
            best_fix, best_score = _best_candidate(group_items[0], candidates, alias_index)
            if best_fix is not None and best_score >= SCORE_THRESHOLD:
                results.append((best_fix.id, group_items[0].match_url))
                _collect_aliases(group_items[0], best_fix, alias_index, new_aliases)
            else:
                fix_desc = f"{best_fix.home_team} vs {best_fix.away_team}" if best_fix else "no_candidate"
                logger.warning(
                    "low_score league=%s item='%s vs %s' best_fixture='%s' score=%.1f",
                    item.league, item.home_raw, item.away_raw, fix_desc, best_score,
                )
        else:
            score_matrix = np.zeros((len(group_items), len(candidates)))
            for gi, g_item in enumerate(group_items):
                for ci, cand in enumerate(candidates):
                    score_matrix[gi, ci] = _pair_score(g_item, cand, alias_index)

            row_ind, col_ind = linear_sum_assignment(-score_matrix)
            for gi, ci in zip(row_ind, col_ind):
                score = score_matrix[gi, ci]
                g_item = group_items[gi]
                fix = candidates[ci]
                if score >= SCORE_THRESHOLD:
                    results.append((fix.id, g_item.match_url))
                    _collect_aliases(g_item, fix, alias_index, new_aliases)
                else:
                    logger.warning(
                        "low_score_assignment league=%s item='%s vs %s' fixture='%s vs %s' score=%.1f",
                        g_item.league, g_item.home_raw, g_item.away_raw, fix.home_team, fix.away_team, score,
                    )

    # Persist new aliases (raw SQL to avoid SQLAlchemy type coercion on text[])
    for ct_id, aliases in new_aliases.items():
        await session.execute(
            sql_text(
                "UPDATE canonical_teams "
                "SET aliases = COALESCE(aliases, ARRAY[]::text[]) || :new_aliases "
                "WHERE id = :ct_id"
            ),
            {"new_aliases": aliases, "ct_id": ct_id},
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
    logger.info("matched %d/%d items to fixtures", len(results), len(items))
    return results
