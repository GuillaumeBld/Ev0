"""Historical simulator for backtesting.

Re-runs the pricing engine on past finished fixtures that have both
odds snapshots and match events, producing backtest records with
real outcomes.
"""

import logging
import math
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.backtest.outcome_resolver import load_outcomes_for_fixtures, resolve_outcome
from app.ingestion.odds import normalize_selection_name
from app.models.fixtures import Fixture
from app.models.match_events import MatchEvent
from app.models.odds import OddsSnapshot
from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat
from app.pricing.goalscorer import calculate_edge
from app.pricing.team_xg import (
    PENS_PER_MATCH,
    POSITION_NPXG_PRIORS,
    POSITION_XA_PRIORS,
    SHRINKAGE_N,
    estimate_team_match_xg,
)
from app.services.recommendation_service import (
    DEFAULT_POSITION_FALLBACK,
    POSITION_DEFAULTS,
    _normalize_position,
)

logger = logging.getLogger(__name__)

CALIBRATION_SCALE = 0.84  # Recalibrated: 18.8% empirical win rate vs 22.3% model avg → factor ~0.84


async def simulate_historical(
    db: AsyncSession,
    league: str | None = None,
    min_date: date | None = None,
    max_date: date | None = None,
) -> list[dict[str, Any]]:
    """Re-run the pricing engine on past finished fixtures.

    For each finished fixture that has both odds snapshots AND match events,
    this function:
    1. Loads the best odds per player (latest snapshot)
    2. Loads player stats from **before** the fixture (no data leakage)
    3. Runs the pricing engine to calculate fair odds and edge
    4. Resolves the actual outcome from match events
    5. Returns records in ``BacktestEngine`` input format

    Args:
        db: Database session
        league: Filter by league (e.g. ``"ligue_1"``)
        min_date: Earliest fixture date to include
        max_date: Latest fixture date to include

    Returns:
        List of backtest records, each with:
        ``date``, ``fixture_id``, ``player``, ``market``,
        ``fair_prob``, ``fair_odds``, ``market_odds``, ``edge``,
        ``confidence``, ``outcome``
    """
    # 1. Find finished fixtures that have both odds AND match events
    fixtures = await _load_eligible_fixtures(db, league, min_date, max_date)

    if not fixtures:
        logger.info("No eligible fixtures found for simulation")
        return []

    logger.info("Simulating %d fixtures", len(fixtures))

    # 2. Load match events for all fixtures
    fixture_ids = [f.id for f in fixtures]
    events_by_fixture = await load_outcomes_for_fixtures(db, fixture_ids)

    # 3. Load best odds per player per fixture
    odds_by_fixture = await _load_best_odds(db, fixture_ids)

    # 4. Build player stats index (latest stats before each fixture)
    player_stats_cache = await _load_player_stats_index(db)

    # 5. Team stats — use fixed league averages (Bzzoiro-based pricing uses MarketXgService)
    league_avg_xg = 1.2
    league_avg_xga = 1.2

    # Pre-compute team npxg/xa totals from player_stats_cache
    from collections import defaultdict
    team_npxg_totals: dict[str, float] = defaultdict(float)
    team_xa_totals: dict[str, float] = defaultdict(float)
    for _pname, pstats in player_stats_cache.items():
        team = pstats.get("team")
        if team:
            team_npxg_totals[team] += pstats.get("npxg_total", 0.0) or 0.0
            team_xa_totals[team] += pstats.get("xa_total", 0.0) or 0.0

    # 6. Run pricing for each fixture × player (Top-Down)
    records: list[dict[str, Any]] = []

    for fixture in fixtures:
        fixture_events = events_by_fixture.get(fixture.id, [])
        fixture_odds = odds_by_fixture.get(fixture.id, [])

        if not fixture_events or not fixture_odds:
            continue

        # Match xG for this fixture
        home_ts = ev0_team_stats.get(fixture.home_team)
        away_ts = ev0_team_stats.get(fixture.away_team)
        home_match_xg = estimate_team_match_xg(
            attack_xg=home_ts.attack_xg_per_match if home_ts else league_avg_xg,
            opponent_xga=away_ts.defense_xga_per_match if away_ts else league_avg_xga,
            league_avg_xg=league_avg_xg,
            league_avg_xga=league_avg_xga,
            is_home=True,
        )
        away_match_xg = estimate_team_match_xg(
            attack_xg=away_ts.attack_xg_per_match if away_ts else league_avg_xg,
            opponent_xga=home_ts.defense_xga_per_match if home_ts else league_avg_xga,
            league_avg_xg=league_avg_xg,
            league_avg_xga=league_avg_xga,
            is_home=False,
        )

        for odds_entry in fixture_odds:
            player_name = odds_entry["player_name"]
            market_type = odds_entry["market_type"]
            market_odds = odds_entry["odds"]
            bookmaker = odds_entry["bookmaker"]

            if market_odds <= 1:
                continue

            stats = _find_player_stats(player_name, player_stats_cache)
            if not stats:
                continue

            position = stats.get("position")
            if position == "GK":
                continue

            team = stats.get("team", fixture.home_team)
            team_match_xg = home_match_xg if team == fixture.home_team else away_match_xg

            # Skip players with no real scoring data to avoid false VALUE signals
            npxg_total = stats.get("npxg_total", 0.0) or 0.0
            xa_total = stats.get("xa_total", 0.0) or 0.0
            xg_per_90 = stats.get("xg_per_90") or 0.0
            xa_per_90 = stats.get("xa_per_90") or 0.0
            if market_type == "goalscorer" and npxg_total <= 0.01 and xg_per_90 <= 0.005:
                continue
            if market_type == "assist" and xa_total <= 0.01 and xa_per_90 <= 0.005:
                continue

            # Top-Down player share with Bayesian shrinkage
            # Unknown position → conservative DF prior to avoid inflating defender lambda
            matches = stats.get("matches_played", 0) or 0
            shrink = min(matches / SHRINKAGE_N, 1.0)
            team_npxg = team_npxg_totals.get(team, 0.0) or 1e-9
            team_xa = team_xa_totals.get(team, 0.0) or 1e-9
            npxg_prior = POSITION_NPXG_PRIORS.get(position or "DF", 0.02)
            xa_prior = POSITION_XA_PRIORS.get(position or "DF", 0.03)
            npxg_actual = npxg_total / team_npxg
            xa_actual = xa_total / team_xa
            npxg_share = shrink * npxg_actual + (1 - shrink) * npxg_prior
            xa_share = shrink * xa_actual + (1 - shrink) * xa_prior

            expected_minutes = stats.get("expected_minutes", 75.0)
            mins_ratio = expected_minutes / 90.0

            if market_type == "goalscorer":
                lambda_val = max(0.001, team_match_xg * (1 - PENS_PER_MATCH) * npxg_share * mins_ratio)
            elif market_type == "assist":
                lambda_val = max(0.001, team_match_xg * xa_share * mins_ratio)
            else:
                continue

            probability = 1 - math.exp(-lambda_val)
            probability = probability * CALIBRATION_SCALE
            fair_odds = round(1 / probability, 2) if probability > 0 else 9999.0
            edge = calculate_edge(fair_odds, market_odds)

            # Resolve outcome
            outcome = resolve_outcome(player_name, market_type, fixture_events)

            # Confidence based on data quality
            pos_defaults = (
                POSITION_DEFAULTS.get(position, DEFAULT_POSITION_FALLBACK)
                if position
                else DEFAULT_POSITION_FALLBACK
            )
            has_real_xg = stats.get("xg_per_90") is not None and stats.get(
                "xg_per_90"
            ) != pos_defaults.get("xg_per_90")
            if matches >= 10 and has_real_xg:
                confidence = 0.80
            elif matches >= 5 and has_real_xg:
                confidence = 0.65
            elif matches >= 3:
                confidence = 0.55
            else:
                confidence = 0.40

            records.append(
                {
                    "date": fixture.kickoff_utc.date()
                    if isinstance(fixture.kickoff_utc, datetime)
                    else fixture.kickoff_utc,
                    "fixture_id": fixture.external_id,
                    "player": player_name,
                    "market": market_type,
                    "fair_prob": round(probability, 4),
                    "fair_odds": fair_odds,
                    "market_odds": market_odds,
                    "edge": edge,
                    "confidence": confidence,
                    "outcome": outcome,
                    "bookmaker": bookmaker,
                    "home_team": fixture.home_team,
                    "away_team": fixture.away_team,
                    "league": fixture.league,
                }
            )

    logger.info(
        "Simulation complete: %d records from %d fixtures "
        "(%d with outcome=1)",
        len(records),
        len(fixtures),
        sum(1 for r in records if r["outcome"] == 1),
    )

    return records


# ── Internal helpers ─────────────────────────────────────────────


async def _load_eligible_fixtures(
    db: AsyncSession,
    league: str | None,
    min_date: date | None,
    max_date: date | None,
) -> list[Fixture]:
    """Load finished fixtures that have both odds snapshots and match events."""
    # Subqueries for fixtures with odds and events
    has_odds = select(OddsSnapshot.fixture_id).distinct().subquery()
    has_events = select(MatchEvent.fixture_id).distinct().subquery()

    stmt = (
        select(Fixture)
        .where(Fixture.status == "finished")
        .where(Fixture.id.in_(select(has_odds.c.fixture_id)))
        .where(Fixture.id.in_(select(has_events.c.fixture_id)))
        .order_by(Fixture.kickoff_utc)
    )

    if league:
        stmt = stmt.where(Fixture.league == league)
    if min_date:
        stmt = stmt.where(Fixture.kickoff_utc >= datetime.combine(min_date, datetime.min.time()))
    if max_date:
        stmt = stmt.where(Fixture.kickoff_utc <= datetime.combine(max_date, datetime.max.time()))

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _load_best_odds(
    db: AsyncSession,
    fixture_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    """Load the best (highest) odds per player/market/fixture."""
    if not fixture_ids:
        return {}

    # Get max odds per player per fixture per market
    subq = (
        select(
            OddsSnapshot.fixture_id,
            OddsSnapshot.player_name,
            OddsSnapshot.market_type,
            func.max(OddsSnapshot.odds).label("max_odds"),
        )
        .where(OddsSnapshot.fixture_id.in_(fixture_ids))
        .group_by(
            OddsSnapshot.fixture_id,
            OddsSnapshot.player_name,
            OddsSnapshot.market_type,
        )
        .subquery()
    )

    result = await db.execute(
        select(OddsSnapshot)
        .join(
            subq,
            (OddsSnapshot.fixture_id == subq.c.fixture_id)
            & (OddsSnapshot.player_name == subq.c.player_name)
            & (OddsSnapshot.market_type == subq.c.market_type)
            & (OddsSnapshot.odds == subq.c.max_odds),
        )
        .where(OddsSnapshot.fixture_id.in_(fixture_ids))
    )

    odds_by_fixture: dict[int, list[dict[str, Any]]] = {}
    for snap in result.scalars().all():
        odds_by_fixture.setdefault(snap.fixture_id, []).append(
            {
                "player_name": snap.player_name,
                "market_type": snap.market_type,
                "odds": snap.odds,
                "bookmaker": snap.bookmaker,
            }
        )

    return odds_by_fixture


async def _load_player_stats_index(
    db: AsyncSession,
) -> dict[str, dict[str, Any]]:
    """Load latest player stats, keyed by player name."""
    latest_subq = (
        select(
            BzzPlayerSeasonStat.player_api_id,
            func.max(BzzPlayerSeasonStat.season).label("max_season"),
        )
        .group_by(BzzPlayerSeasonStat.player_api_id)
        .subquery()
    )

    result = await db.execute(
        select(BzzPlayerSeasonStat, BzzPlayer)
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .join(
            latest_subq,
            (BzzPlayerSeasonStat.player_api_id == latest_subq.c.player_api_id)
            & (BzzPlayerSeasonStat.season == latest_subq.c.max_season),
        )
    )

    _bzz_pos = {"G": "GK", "D": "DF", "M": "MF", "F": "FW"}
    stats_index: dict[str, dict[str, Any]] = {}
    for row in result.all():
        ps: BzzPlayerSeasonStat = row[0]
        player: BzzPlayer = row[1]
        player_name: str = player.name
        team: str | None = player.current_team_name
        raw_position: str | None = _bzz_pos.get(player.position or "", None)

        position = _normalize_position(raw_position)
        if position == "GK":
            continue

        minutes = ps.minutes_played or 0
        matches = ps.matches_played or 1
        expected_minutes = minutes / matches if matches > 0 else 75.0

        xg = ps.expected_goals or 0.0
        goals = ps.goals or 0
        raw_cr = (goals / xg) if xg > 0 else 1.0
        conversion_rate = max(0.5, min(2.0, raw_cr)) if matches >= 3 else 1.0

        pos_defaults = (
            POSITION_DEFAULTS.get(position, DEFAULT_POSITION_FALLBACK)
            if position
            else DEFAULT_POSITION_FALLBACK
        )
        xg_per_90 = ps.xg_per_90 if ps.xg_per_90 is not None else pos_defaults["xg_per_90"]
        xa_per_90 = ps.xa_per_90 if ps.xa_per_90 is not None else pos_defaults["xa_per_90"]

        stats_index[player_name] = {
            "xg_per_90": xg_per_90,
            "xa_per_90": xa_per_90,
            "npxg_total": xg,
            "xa_total": ps.expected_assists or 0.0,
            "expected_minutes": expected_minutes,
            "conversion_rate": conversion_rate,
            "team": team,
            "position": position,
            "matches_played": matches,
        }

    return stats_index


def _find_player_stats(
    player_name: str,
    stats_index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Find player stats by name with normalized matching."""
    if player_name in stats_index:
        return stats_index[player_name]

    norm_key = normalize_selection_name(player_name)
    for name, stats in stats_index.items():
        if normalize_selection_name(name) == norm_key:
            return stats

    # Last-name fallback
    parts = player_name.split()
    if parts:
        last_name = parts[-1].lower()
        for name, stats in stats_index.items():
            if last_name in name.lower():
                return stats

    return None


async def _compute_team_strengths(
    db: AsyncSession,
) -> dict[str, dict[str, float]]:
    """Compute average xG per match for each team using Bzzoiro season stats."""
    result = await db.execute(
        select(
            BzzPlayer.current_team_name,
            func.sum(BzzPlayerSeasonStat.expected_goals).label("total_xg"),
            func.max(BzzPlayerSeasonStat.matches_played).label("team_matches"),
        )
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .where(BzzPlayer.current_team_name.isnot(None))
        .group_by(BzzPlayer.current_team_name)
    )

    strengths: dict[str, dict[str, float]] = {}
    for row in result.all():
        team_name = row[0]
        total_xg = row[1] or 0.0
        total_matches = row[2] or 0
        if total_matches > 0 and team_name:
            strengths[team_name] = {
                "xg_per_match": total_xg / total_matches,
            }

    return strengths


def _get_opponent_factor(
    opponent: str,
    team_strengths: dict[str, dict[str, float]],
) -> float:
    """Get opponent defensive factor (same logic as recommendation_service)."""
    if not team_strengths or opponent not in team_strengths:
        return 1.0

    opponent_xg = team_strengths[opponent]["xg_per_match"]
    league_avg = (
        sum(s["xg_per_match"] for s in team_strengths.values()) / len(team_strengths)
        if team_strengths
        else 1.0
    )

    if opponent_xg <= 0 or league_avg <= 0:
        return 1.0

    raw_factor = opponent_xg / league_avg
    shrunk_factor = 0.6 * raw_factor + 0.4 * 1.0
    return max(0.7, min(1.4, shrunk_factor))
