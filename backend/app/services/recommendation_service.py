"""Recommendation generation service.

Combines pricing engine, odds data, and strategy to generate
actionable betting recommendations.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.odds import normalize_selection_name
from app.pricing.goalscorer import calculate_edge
from app.pricing.team_xg import load_match_pricing
from app.strategy.selector import RecommendationFilter, select_bets

logger = logging.getLogger(__name__)

# Position-based xG/xA defaults for players with stats in DB but missing per-90 values
# Kept for backward compat: imported by simulator.py and generate_synthetic_odds.py
POSITION_DEFAULTS: dict[str, dict[str, float]] = {
    "FW": {"xg_per_90": 0.35, "xa_per_90": 0.15},
    "MF": {"xg_per_90": 0.10, "xa_per_90": 0.12},
    "DF": {"xg_per_90": 0.03, "xa_per_90": 0.03},
    "GK": {"xg_per_90": 0.00, "xa_per_90": 0.00},
}
DEFAULT_POSITION_FALLBACK = {"xg_per_90": 0.10, "xa_per_90": 0.08}  # Unknown position

# Bzzoiro single-char position → canonical FW/MF/DF/GK
_BZZ_POSITION_MAP: dict[str, str] = {"G": "GK", "D": "DF", "M": "MF", "F": "FW"}


def _normalize_position(raw_position: str | None) -> str | None:
    """Map various position formats to canonical FW/MF/DF/GK.

    Handles Bzzoiro single-char (G/D/M/F) and legacy multi-char formats.
    Kept for backward compat: imported by simulator.py and generate_synthetic_odds.py.
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
    odds_data: dict[str, list[dict[str, Any]]],
    db: AsyncSession,
    db_fixtures: list,           # objets Fixture ORM
    filter_config: RecommendationFilter | None = None,
    pen_takers: dict[str, tuple[int | None, int | None]] | None = None,
) -> list[dict[str, Any]]:
    """Generate betting recommendations for upcoming fixtures.

    Delegates all pricing to load_match_pricing (top-down engine).
    Fixtures without market xG data are skipped.
    """
    all_recommendations = []
    _matched = 0
    _unmatched = 0
    _skipped_position = 0

    # Index ORM objects par external_id
    fixture_orm_map = {str(f.external_id): f for f in db_fixtures}

    for fixture in fixtures:
        fixture_id = str(fixture.get("fixture_id") or fixture.get("id") or "")
        home_team = str(fixture.get("home_team") or "")
        away_team = str(fixture.get("away_team") or "")
        kickoff = fixture.get("kickoff_utc")
        league = fixture.get("league")

        fixture_orm = fixture_orm_map.get(fixture_id)
        if not fixture_orm:
            continue

        # Pen taker overrides
        pen_home_id, pen_away_id = (pen_takers or {}).get(fixture_id, (None, None))

        # Get pricing from the single top-down engine
        pricing = await load_match_pricing(
            db, fixture_orm,
            home_pen_taker_override=pen_home_id,
            away_pen_taker_override=pen_away_id,
        )
        if pricing is None:
            logger.warning("rec_service: no market xG for fixture %s — skipping", fixture_id)
            continue

        xg_source = pricing.xg_source
        home_match_xg = pricing.home_match_xg
        away_match_xg = pricing.away_match_xg

        # Build player allocation lookup (normalized name → allocation)
        alloc_by_norm: dict[str, Any] = {}
        for alloc in pricing.home_players + pricing.away_players:
            norm_key = normalize_selection_name(alloc.player_name)
            alloc_by_norm[norm_key] = alloc

        fixture_odds = odds_data.get(fixture_id, [])
        for odds_entry in fixture_odds:
            player_name = odds_entry.get("player_name")
            market_type = odds_entry.get("market_type", "goalscorer")
            market_odds = odds_entry.get("odds", 0)
            bookmaker = odds_entry.get("bookmaker", "unknown")

            if not player_name or market_odds <= 1:
                continue

            norm_key = normalize_selection_name(player_name)
            alloc = alloc_by_norm.get(norm_key)
            if not alloc:
                _unmatched += 1
                continue

            if alloc.position == "GK":
                _skipped_position += 1
                continue

            _matched += 1

            # Get fair odds from allocation
            if market_type == "goalscorer":
                fair_odds = alloc.fair_odds_goal
                probability = alloc.prob_goal
                lambda_val = alloc.lambda_total
                has_form = alloc.has_form_goal
            else:
                fair_odds = alloc.fair_odds_assist
                probability = alloc.prob_assist
                lambda_val = alloc.lambda_assist
                has_form = alloc.has_form_assist

            edge = calculate_edge(fair_odds, market_odds)

            # Confidence
            matches = alloc.matches_played
            if matches >= 10 and has_form:
                confidence = 0.85
            elif matches >= 5:
                confidence = 0.70
            elif matches >= 3:
                confidence = 0.55
            elif matches >= 1:
                confidence = 0.40
            else:
                confidence = 0.25

            if edge >= 0.05:
                classification = "VALUE"
            elif edge >= 0.0:
                classification = "NO_VALUE"
            else:
                classification = "AVOID"

            team_lambda = home_match_xg if alloc.team == home_team else away_match_xg

            recommendation = {
                "fixture_id": fixture_id,
                "fixture_name": f"{home_team} vs {away_team}",
                "kickoff_utc": kickoff,
                "league": league,
                "player_name": player_name,
                "team": alloc.team,
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
                "is_pen_taker": alloc.is_pen_taker,
                "explanation": {
                    "model": "top_down_v2",
                    "xg_source": xg_source,
                    "team_lambda": round(team_lambda, 3),
                    "expected_minutes": alloc.expected_minutes,
                    "lambda": round(lambda_val, 4),
                    "is_pen_taker": alloc.is_pen_taker,
                    "market_type": market_type,
                },
            }
            all_recommendations.append(recommendation)

    logger.info(
        "Player matching: %d matched, %d unmatched, %d skipped (GK)",
        _matched, _unmatched, _skipped_position,
    )

    selection = select_bets(all_recommendations, filter_config)
    return selection.selected


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

    # 2. Load pen taker overrides from app_config
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

    # 3. Generate recommendations (pricing delegated to load_match_pricing per fixture)
    recs = await generate_recommendations(
        fixtures,
        odds_data,
        db=db,
        db_fixtures=db_fixtures,
        filter_config=filter_config,
        pen_takers=pen_takers_by_fixture,
    )

    # 4. Add unique IDs
    for rec in recs:
        rec["id"] = str(uuid.uuid4())

    metadata = {
        "fixtures_count": len(fixtures),
        "player_stats_count": 0,  # now computed inside load_match_pricing per fixture
        "total_odds_entries": sum(len(v) for v in odds_data.values()),
        "recommendations_count": len(recs),
    }
    return recs, metadata
