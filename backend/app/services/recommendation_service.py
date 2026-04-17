"""Recommendation generation service.

Combines pricing engine, odds data, and strategy to generate
actionable betting recommendations.
"""

import logging
import math
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.odds import normalize_selection_name
from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat, BzzTeam
from app.pricing.assist import ASSIST_GOAL_RATE
from app.pricing.goalscorer import calculate_edge
from app.pricing.team_xg import PEN_CONVERSION, PENS_PER_MATCH
from app.services.market_xg import MarketXgService
from app.strategy.selector import RecommendationFilter, select_bets

logger = logging.getLogger(__name__)

CALIBRATION_SCALE = 0.84  # Empirically derived from settled data (Mar 2026): actual win rate 18.8% / model avg prob 22.3%

# Position-based xG/xA defaults for players with stats in DB but missing per-90 values
POSITION_DEFAULTS: dict[str, dict[str, float]] = {
    "FW": {"xg_per_90": 0.35, "xa_per_90": 0.15},
    "MF": {"xg_per_90": 0.10, "xa_per_90": 0.12},
    "DF": {"xg_per_90": 0.03, "xa_per_90": 0.03},
    "GK": {"xg_per_90": 0.00, "xa_per_90": 0.00},
}
DEFAULT_POSITION_FALLBACK = {"xg_per_90": 0.10, "xa_per_90": 0.08}  # Unknown position

# Bzzoiro single-char position → canonical FW/MF/DF/GK
_BZZ_POSITION_MAP: dict[str, str] = {"G": "GK", "D": "DF", "M": "MF", "F": "FW"}

# Fixture league slug → bzz_leagues.api_id
FIXTURE_LEAGUE_TO_BZZ_ID: dict[str, int] = {
    "ligue_1": 34,
    "premier_league": 17,
    "bundesliga": 35,
    "la_liga": 8,
    "serie_a": 23,
    "champions_league": 7,
}

CURRENT_SEASON = "2025-2026"


def _blend_rate(season_rate: float, form_value: float | None, avg_mins: float) -> float:
    """Blend season per-90 rate with last-5-match form (60% season, 40% form).

    form_value is cumulative over 5 matches (e.g. form_xg_5 or form_assists_5).
    Returns season_rate unchanged if form_value is None or avg_mins is 0.
    """
    if form_value is None or avg_mins <= 0:
        return season_rate
    form_rate = form_value / (5.0 * avg_mins / 90.0)
    return 0.60 * season_rate + 0.40 * form_rate


def _compute_team_denominators(
    player_stats: dict[str, dict],
    home_team: str,
    away_team: str,
    lambda_home: float,
    lambda_away: float,
) -> dict[str, dict[str, float]]:
    """Compute top-down share denominators for both teams in a fixture.

    For each team: denominator = max(sum of player weights in DB, λ_team).
    This ensures share_i = weight_i / denom ≤ 1 even when DB covers < 100% of squad.

    Returns: {team_name: {"goal_denom": float, "assist_denom": float}}
    """
    team_goal_weights: dict[str, list[float]] = {home_team: [], away_team: []}
    team_assist_weights: dict[str, list[float]] = {home_team: [], away_team: []}

    for stats in player_stats.values():
        team = stats.get("team")
        if team not in team_goal_weights:
            continue
        avg_mins = stats.get("avg_minutes_per_match") or stats.get("expected_minutes") or 75.0
        mins_ratio = (stats.get("expected_minutes") or 75.0) / 90.0

        blended_xg = _blend_rate(
            stats.get("xg_per_90") or 0.0,
            stats.get("form_xg_5"),
            avg_mins,
        )
        blended_xa = _blend_rate(
            stats.get("xa_per_90") or 0.0,
            stats.get("form_assists_5"),
            avg_mins,
        )
        team_goal_weights[team].append(blended_xg * mins_ratio)
        team_assist_weights[team].append(blended_xa * mins_ratio)

    def denom(weights: list[float], lambda_ref: float, rate: float = 1.0) -> float:
        return max(sum(weights), lambda_ref * rate)

    return {
        home_team: {
            "goal_denom":   denom(team_goal_weights[home_team],   lambda_home),
            "assist_denom": denom(team_assist_weights[home_team], lambda_home, ASSIST_GOAL_RATE),
        },
        away_team: {
            "goal_denom":   denom(team_goal_weights[away_team],   lambda_away),
            "assist_denom": denom(team_assist_weights[away_team], lambda_away, ASSIST_GOAL_RATE),
        },
    }


def _normalize_position(raw_position: str | None) -> str | None:
    """Map various position formats to canonical FW/MF/DF/GK.

    Handles Bzzoiro single-char (G/D/M/F) and legacy multi-char formats.
    """
    if not raw_position:
        return None
    pos = raw_position.strip().upper()
    # Direct match for standard codes
    if pos in POSITION_DEFAULTS:
        return pos
    # Bzzoiro single-char (G/D/M/F)
    if pos in _BZZ_POSITION_MAP:
        return _BZZ_POSITION_MAP[pos]
    # Legacy multi-char fallbacks
    if "GK" in pos:
        return "GK"
    if pos.startswith("F") or "FW" in pos:
        return "FW"
    if pos.startswith("D") or "DF" in pos or "CB" in pos:
        return "DF"
    if pos.startswith("M") or "MF" in pos or "AM" in pos:
        return "MF"
    return None


async def generate_recommendations(
    fixtures: list[dict[str, Any]],
    player_stats: dict[str, dict[str, Any]],  # player_name -> stats
    odds_data: dict[str, list[dict[str, Any]]],  # fixture_id -> odds list
    filter_config: RecommendationFilter | None = None,
    team_strengths: dict[str, dict[str, float]] | None = None,  # team -> strengths (unused, kept for compat)
    team_ev0_stats: dict[str, Any] | None = None,  # Top-Down data (unused, kept for compat)
    fixture_xg: dict[str, tuple[float, float, str]] | None = None,  # ext_id -> (xg_home, xg_away, xg_source)
    pen_takers: dict[str, tuple[int | None, int | None]] | None = None,  # ext_id -> (home_id, away_id)
) -> list[dict[str, Any]]:
    """Generate betting recommendations for upcoming fixtures.

    fixture_xg maps external fixture id → (xg_home, xg_away, xg_source) from MarketXgService.
    Fixtures without an entry in fixture_xg are skipped (no market data).
    """
    all_recommendations = []
    _matched = 0
    _unmatched = 0
    _skipped_position = 0
    _unmatched_names: list[str] = []

    # Build normalized index for player matching
    normalized_index: dict[str, dict] = {}
    for name, s in player_stats.items():
        norm_key = normalize_selection_name(name)
        normalized_index[norm_key] = s

    for fixture in fixtures:
        fixture_id = str(fixture.get("fixture_id") or fixture.get("id") or "")
        home_team = str(fixture.get("home_team") or "")
        away_team = str(fixture.get("away_team") or "")
        kickoff = fixture.get("kickoff_utc")
        league = fixture.get("league")

        # Market-implied xG from MarketXgService (pre-computed per fixture)
        if fixture_xg is None or fixture_id not in fixture_xg:
            logger.warning(
                "rec_service: no market xG for fixture %s — skipping (no market data)",
                fixture_id,
            )
            continue
        home_match_xg, away_match_xg, xg_source = fixture_xg[fixture_id]

        fixture_odds = odds_data.get(fixture_id, [])

        for odds_entry in fixture_odds:
            player_name = odds_entry.get("player_name")
            market_type = odds_entry.get("market_type", "goalscorer")
            market_odds = odds_entry.get("odds", 0)
            bookmaker = odds_entry.get("bookmaker", "unknown")

            if not player_name or market_odds <= 1:
                continue

            stats = _find_player_stats(player_name, player_stats, normalized_index)
            if not stats:
                _unmatched += 1
                if len(_unmatched_names) < 20:
                    _unmatched_names.append(player_name)
                continue

            position = stats.get("position")
            if position == "GK":
                _skipped_position += 1
                continue

            _matched += 1
            team = stats.get("team") or _infer_team(player_name, home_team, away_team)

            # Skip players with no real scoring data to avoid false VALUE signals
            npxg_total = stats.get("npxg_total", 0.0) or 0.0
            xa_total = stats.get("xa_total", 0.0) or 0.0
            _xg_per_90 = stats.get("xg_per_90") or 0.0
            _xa_per_90 = stats.get("xa_per_90") or 0.0
            if market_type == "goalscorer" and npxg_total <= 0.01 and _xg_per_90 <= 0.005:
                _unmatched += 1
                continue
            if market_type == "assist" and xa_total <= 0.01 and _xa_per_90 <= 0.005:
                _unmatched += 1
                continue

            # Pen taker flag: check persisted override for this fixture
            player_api_id = stats.get("player_api_id")
            pen_home_id, pen_away_id = (pen_takers or {}).get(fixture_id, (None, None))
            is_pen_taker = player_api_id is not None and player_api_id in {pen_home_id, pen_away_id}

            # Lambda: player's historical xG rate scaled by market-implied team xG.
            # Market xG is already fixture-specific — no fixture_strength adjustment needed.
            team_match_xg = home_match_xg if team == home_team else away_match_xg
            expected_minutes = stats.get("expected_minutes", 75.0)
            mins_ratio = expected_minutes / 90.0

            if market_type == "goalscorer":
                lambda_base = max(0.001, _xg_per_90 * mins_ratio)
                lambda_penalty = PEN_CONVERSION * PENS_PER_MATCH * mins_ratio if is_pen_taker else 0.0
                lambda_val = lambda_base + lambda_penalty
            else:  # assist
                lambda_val = max(0.001, _xa_per_90 * mins_ratio)

            probability = 1 - math.exp(-lambda_val)
            probability = probability * CALIBRATION_SCALE
            fair_odds = 1 / probability if probability > 0 else 9999.0
            fair_odds = round(fair_odds, 2)

            # Calculate edge
            edge = calculate_edge(fair_odds, market_odds)

            # Confidence based on data quality
            matches = stats.get("matches_played", 0) or 0
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

            if edge >= 0.05:
                classification = "VALUE"
            elif edge >= 0.0:
                classification = "NO_VALUE"
            else:
                classification = "AVOID"

            recommendation = {
                "fixture_id": fixture_id,
                "fixture_name": f"{home_team} vs {away_team}",
                "kickoff_utc": kickoff,
                "league": league,
                "player_name": player_name,
                "team": team,
                "market_type": market_type,
                "fair_probability": round(probability, 4),
                "fair_odds": fair_odds,
                "lambda_intensity": round(lambda_val, 4),
                "market_odds": market_odds,
                "best_bookmaker": bookmaker,
                "edge": edge,
                "classification": classification,
                "confidence": confidence,
                "xg_source": xg_source,
                "is_pen_taker": is_pen_taker,
                "explanation": {
                    "model": "market_implied_xg",
                    "xg_source": xg_source,
                    "team_match_xg": round(team_match_xg, 3),
                    "expected_minutes": expected_minutes,
                    "lambda": round(lambda_val, 4),
                    "is_pen_taker": is_pen_taker,
                },
            }

            all_recommendations.append(recommendation)

    logger.info(
        "Player matching: %d matched, %d unmatched, %d skipped (GK)",
        _matched,
        _unmatched,
        _skipped_position,
    )
    if _unmatched_names:
        logger.debug("Unmatched players (sample): %s", _unmatched_names[:10])

    # Apply strategy selection
    selection = select_bets(all_recommendations, filter_config)

    return selection.selected


def _find_player_stats(
    player_name: str,
    stats_dict: dict[str, dict],
    normalized_index: dict[str, dict] | None = None,
) -> dict[str, Any] | None:
    """Find player stats by name with normalized matching."""
    # Direct match
    if player_name in stats_dict:
        return stats_dict[player_name]

    # Normalized match using normalize_selection_name (handles accents, punctuation, etc.)
    if normalized_index:
        norm_key = normalize_selection_name(player_name)
        if norm_key in normalized_index:
            return normalized_index[norm_key]

    # Fallback: basic normalized match
    normalized = player_name.lower().replace(" ", "-")
    for key, stats in stats_dict.items():
        if key.lower().replace(" ", "-") == normalized:
            return stats

    # Partial match (last name)
    parts = player_name.split()
    if parts:
        last_name = parts[-1].lower()
        for key, stats in stats_dict.items():
            if last_name in key.lower():
                return stats

    return None


def _infer_team(
    player_name: str,
    home_team: str,
    away_team: str,
    player_stats: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Try to infer player's team.

    Checks the player_stats dict first (which has team from DB).
    Falls back to home_team as a last resort.
    """
    if player_stats:
        stats = _find_player_stats(player_name, player_stats)
        if stats and stats.get("team"):
            return stats["team"]
    return home_team


def _get_opponent_factor(
    opponent: str,
    market_type: str,
    team_strengths: dict[str, dict[str, float]] | None = None,
) -> float:
    """
    Get opponent defensive factor.

    > 1.0 = weak defense (good for attacker)
    < 1.0 = strong defense (bad for attacker)
    """
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


async def get_recommendations_for_date(
    target_date: datetime,
    db: AsyncSession,
    filter_config: RecommendationFilter | None = None,
) -> tuple[list[dict[str, Any]], dict]:
    """
    Get recommendations for a specific date.

    Reads fixtures and odds from the DB (populated by the worker).

    Returns (recommendations, metadata) tuple.
    """
    from datetime import timedelta

    from app.models.fixtures import Fixture
    from app.models.player_odds_snapshot import PlayerOddsSnapshot as OddsSnapshotModel

    # 1. Load upcoming fixtures from DB (next 7 days from target_date)
    window_start = target_date
    window_end = target_date + timedelta(days=7)

    fixture_result = await db.execute(
        select(Fixture)
        .where(Fixture.status == "scheduled")
        .where(Fixture.kickoff_utc >= window_start)
        .where(Fixture.kickoff_utc <= window_end)
        .order_by(Fixture.kickoff_utc)
    )
    db_fixtures = list(fixture_result.scalars().all())

    fixtures: list[dict[str, Any]] = []
    odds_data: dict[str, list[dict[str, Any]]] = {}

    for f in db_fixtures:
        fixture_id = f.external_id
        fixtures.append(
            {
                "fixture_id": fixture_id,
                "home_team": f.home_team,
                "away_team": f.away_team,
                "kickoff_utc": str(f.kickoff_utc),
                "league": f.league,
            }
        )

        # Load odds from DB for this fixture
        odds_result = await db.execute(
            select(OddsSnapshotModel)
            .where(OddsSnapshotModel.fixture_id == f.id)
            .order_by(OddsSnapshotModel.scraped_at.desc())
        )
        # Keep best odds per (player, market) — avoids duplicates from multiple bookmakers/snapshots
        best_odds: dict[tuple[str, str], dict[str, Any]] = {}
        for o in odds_result.scalars().all():
            key = (o.player_name, o.market_type)
            existing = best_odds.get(key)
            if existing is None or o.odds > existing["odds"]:
                best_odds[key] = {
                    "player_name": o.player_name,
                    "market_type": o.market_type,
                    "odds": o.odds,
                    "bookmaker": o.bookmaker,
                }
        odds_data[fixture_id] = list(best_odds.values())

    if not fixtures:
        return [], {
            "fixtures_count": 0,
            "odds_data_keys": list(odds_data.keys()),
            "stage": "no_fixtures_for_date",
        }

    # 2. Load player stats from Bzzoiro (current season, filtered to fixture leagues)
    fixture_leagues = list({f.league for f in db_fixtures if f.league})
    bzz_league_ids = [
        FIXTURE_LEAGUE_TO_BZZ_ID[lg]
        for lg in fixture_leagues
        if lg in FIXTURE_LEAGUE_TO_BZZ_ID
    ]

    player_stats: dict[str, dict[str, Any]] = {}

    if bzz_league_ids:
        result = await db.execute(
            select(
                BzzPlayerSeasonStat,
                BzzPlayer.name,
                BzzPlayer.position,
                BzzTeam.name.label("team_name"),
            )
            .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
            .join(BzzTeam, BzzTeam.api_id == BzzPlayer.current_team_api_id, isouter=True)
            .where(BzzPlayerSeasonStat.league_api_id.in_(bzz_league_ids))
            .where(BzzPlayerSeasonStat.season == CURRENT_SEASON)
        )

        for row in result.all():
            stats: BzzPlayerSeasonStat = row[0]
            player_name: str = row[1]
            raw_position: str | None = row[2]
            team_name: str | None = row[3]
            position = _normalize_position(raw_position)

            # Skip GKs at data loading stage
            if position == "GK":
                continue

            matches = stats.matches_played or 1
            minutes = stats.minutes_played or 0
            # Use pre-computed avg_minutes_per_match if available, else derive
            expected_minutes = stats.avg_minutes_per_match or (minutes / matches if matches > 0 else 75.0)

            # Conversion rate: goals / xG, clamped [0.5, 2.0], require >= 3 matches
            xg_total = stats.expected_goals or 0.0
            goals_total = stats.goals or 0
            raw_cr = (goals_total / xg_total) if xg_total > 0 else 1.0
            conversion_rate = (
                max(0.5, min(2.0, raw_cr)) if (stats.matches_played or 0) >= 3 else 1.0
            )

            # Per-90 values — fall back to position defaults if missing
            pos_defaults = (
                POSITION_DEFAULTS.get(position, DEFAULT_POSITION_FALLBACK)
                if position
                else DEFAULT_POSITION_FALLBACK
            )
            xg_per_90 = (
                stats.xg_per_90 if stats.xg_per_90 is not None else pos_defaults["xg_per_90"]
            )
            xa_per_90 = (
                stats.xa_per_90 if stats.xa_per_90 is not None else pos_defaults["xa_per_90"]
            )

            entry = {
                "player_api_id":          stats.player_api_id,
                "xg_per_90":              xg_per_90,
                "xa_per_90":              xa_per_90,
                "npxg_total":             xg_total,
                "xa_total":               stats.expected_assists or 0.0,
                "expected_minutes":       expected_minutes,
                "avg_minutes_per_match":  stats.avg_minutes_per_match or expected_minutes,
                "conversion_rate":        conversion_rate,
                "team":                   team_name,
                "position":               position,
                "goals":                  goals_total,
                "assists":                stats.goal_assist or 0,
                "matches_played":         stats.matches_played or 0,
                # Bzzoiro enriched fields (top-down model)
                "form_xg_5":              stats.form_xg_5,
                "form_assists_5":         stats.form_assists_5,
                "shot_accuracy":          stats.shot_accuracy,
                "xg_per_shot":            stats.xg_per_shot,
                "avg_rating":             stats.avg_rating,
                "key_pass_per_90":        stats.key_pass_per_90,
                "accurate_cross_per_90":  stats.accurate_cross_per_90,
                "cross_accuracy":         stats.cross_accuracy,
            }

            # If same player appears for multiple fixture leagues, keep the richer entry
            existing = player_stats.get(player_name)
            if existing is None or (matches > (existing.get("matches_played") or 0)):
                player_stats[player_name] = entry

    # 3. Load pen taker overrides from app_config
    from app.models.app_config import AppConfig
    pen_takers_by_fixture: dict[str, tuple[int | None, int | None]] = {}
    if db_fixtures:
        config_keys = [f"pen_taker::{f.id}" for f in db_fixtures]
        cfg_result = await db.execute(
            select(AppConfig).where(AppConfig.key.in_(config_keys))
        )
        for cfg in cfg_result.scalars().all():
            fid_str = cfg.key.split("::")[-1]
            try:
                import json as _json
                data = _json.loads(cfg.value)
                # Map fixture.id → external_id for lookup in generate_recommendations
                for f in db_fixtures:
                    if str(f.id) == fid_str:
                        pen_takers_by_fixture[f.external_id] = (data.get("home"), data.get("away"))
                        break
            except Exception:
                pass

    # 4. Compute market-implied xG per fixture via MarketXgService
    _market_xg_svc = MarketXgService()
    fixture_xg: dict[str, tuple[float, float, str]] = {}
    for f in db_fixtures:
        market_xg = await _market_xg_svc.compute(f.id, db)
        if market_xg is None:
            logger.warning(
                "rec_service: no market xG for fixture %s (%s vs %s) — will skip",
                f.id,
                f.home_team,
                f.away_team,
            )
            continue
        fixture_xg[f.external_id] = (market_xg.xg_home, market_xg.xg_away, market_xg.data_source)

    # 5. Generate recommendations
    recs = await generate_recommendations(
        fixtures,
        player_stats,
        odds_data,
        filter_config,
        fixture_xg=fixture_xg,
        pen_takers=pen_takers_by_fixture,
    )

    # 6. Add unique IDs
    for rec in recs:
        rec["id"] = str(uuid.uuid4())

    metadata = {
        "fixtures_count": len(fixtures),
        "player_stats_count": len(player_stats),
        "total_odds_entries": sum(len(v) for v in odds_data.values()),
        "recommendations_count": len(recs),
    }
    return recs, metadata
