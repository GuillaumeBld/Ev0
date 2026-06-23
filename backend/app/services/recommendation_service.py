"""Recommendation generation service.

Combines pricing engine, odds data, and strategy to generate
actionable betting recommendations.
"""

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.odds import normalize_selection_name
from app.models.match_odds import MatchOddsSnapshot
from app.models.recommendations import MarketType
from app.pricing.goalscorer import (
    calculate_edge,
    ev_anytime,
    ev_anytime_p00_guarantee,
    devig_implied_prob,
    GOALSCORER_MARKET_MARGIN,
)
from app.pricing.team_xg import load_match_pricing
from app.services.market_xg import p_poisson_away_win, p_poisson_draw, p_poisson_home_win
from app.services.recommendations_service import build_recommendations_for_player
from app.strategy.selector import RecommendationFilter, select_bets

logger = logging.getLogger(__name__)

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


def _normalize_position(raw_position: str | None) -> str | None:
    """Map various position formats to canonical FW/MF/DF/GK.

    Handles Bzzoiro single-char (G/D/M/F) and legacy multi-char formats."""
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
        match_p00 = pricing.p00

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
            ev = round(ev_anytime(probability, market_odds), 4)
            ev_p00 = round(ev_anytime_p00_guarantee(probability, market_odds, match_p00), 4)
            market_devigged_prob = round(devig_implied_prob(market_odds, GOALSCORER_MARKET_MARGIN), 4)

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
                "ev_anytime": ev,
                "ev_p00_guarantee": ev_p00,
                "market_devigged_prob": market_devigged_prob,
                "p00": match_p00,
                "classification": classification,
                "confidence": confidence,
                "xg_source": xg_source,
                "is_pen_taker": alloc.is_pen_taker,
                "explanation": {
                    "model": "top_down_v2",
                    "xg_source": xg_source,
                    "team": alloc.team,
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


FR_H2H_BOOKMAKERS = ["betclic", "unibet", "pmu"]


async def _generate_h2h_recs(
    fixture_id: int,
    lh: float,
    la: float,
    session: AsyncSession,
    home_team: str = "home",
    away_team: str = "away",
) -> list[dict]:
    """Compute h2h recommendations for a single fixture.

    Returns a list of dicts (one per outcome with edge >= 0), each containing:
      outcome, display_name, fair_prob, fair_odds, lambda_intensity,
      best_bookmaker, best_odds, edge, classification
    """
    p_home = p_poisson_home_win(lh, la)
    p_draw = p_poisson_draw(lh, la)
    p_away = p_poisson_away_win(lh, la)

    outcomes = {
        "home": (p_home, lh, home_team),
        "draw": (p_draw, (lh + la) / 2, "Nul"),
        "away": (p_away, la, away_team),
    }

    snap_result = await session.execute(
        select(MatchOddsSnapshot).where(
            MatchOddsSnapshot.fixture_id == fixture_id,
            MatchOddsSnapshot.market_type == "h2h",
            MatchOddsSnapshot.bookmaker.in_(FR_H2H_BOOKMAKERS),
        )
    )
    snapshots = list(snap_result.scalars().all())

    best_by_outcome: dict[str, dict] = {}
    for snap in snapshots:
        existing = best_by_outcome.get(snap.outcome)
        if existing is None or snap.odds > existing["odds"]:
            best_by_outcome[snap.outcome] = {
                "bookmaker": snap.bookmaker,
                "odds": snap.odds,
            }

    recs: list[dict] = []
    for outcome, (fair_prob, lambda_intensity, display_name) in outcomes.items():
        if fair_prob <= 0:
            continue
        if outcome not in best_by_outcome:
            continue

        fair_odds = 1.0 / fair_prob
        market_entry = best_by_outcome[outcome]
        market_odds = market_entry["odds"]
        edge = calculate_edge(fair_odds, market_odds)

        classification = "VALUE" if edge >= 0.05 else "NO_VALUE"

        recs.append({
            "outcome": outcome,
            "display_name": display_name,
            "fair_prob": round(fair_prob, 4),
            "fair_odds": round(fair_odds, 4),
            "lambda_intensity": round(lambda_intensity, 4),
            "best_bookmaker": market_entry["bookmaker"],
            "best_odds": market_odds,
            "edge": round(edge, 4),
            "classification": classification,
        })

    return recs


async def process_scraped_fixtures(
    fixture_ids: list[int],
    session: AsyncSession,
    filter_config: RecommendationFilter | None = None,
) -> dict[str, int]:
    """Run the full recommendation pipeline for fixtures that were just scraped.

    Called after each scrape tick. For each fixture:
    - Computes fresh market xG via load_match_pricing
    - Compares fresh player_odds_snapshots vs fair odds
    - Creates new recommendations for newly-VALUE players
    - Resurrects expired recommendations when odds recover to EV+
    - Updates best_odds/edge on existing pending/approved recs
    - Expires pending/approved recs for players that are no longer EV+
    """
    from app.models.app_config import AppConfig
    from app.models.fixtures import Fixture
    from app.models.player_odds_snapshot import PlayerOddsSnapshot
    from app.models.recommendations import Recommendation

    if not fixture_ids:
        return {"created": 0, "updated": 0, "expired": 0, "resurrected": 0}

    now = datetime.now(UTC)
    stats: dict[str, int] = {"created": 0, "updated": 0, "expired": 0, "resurrected": 0}
    new_value_alerts: list[dict] = []

    # Load fixture ORM objects
    fx_result = await session.execute(
        select(Fixture).where(Fixture.id.in_(fixture_ids))
    )
    fixtures = list(fx_result.scalars().all())
    if not fixtures:
        return stats

    # Load all existing recommendations for these fixtures (all statuses)
    rec_result = await session.execute(
        select(Recommendation).where(Recommendation.fixture_id.in_(fixture_ids))
    )
    all_recs = list(rec_result.scalars().all())
    rec_by_key: dict[tuple, Recommendation] = {
        (r.fixture_id, r.player_name, r.market_type, getattr(r, "bet_type", "goal")): r
        for r in all_recs
    }

    # Load pen taker overrides
    config_keys = [f"pen_taker::{f.id}" for f in fixtures]
    cfg_result = await session.execute(
        select(AppConfig).where(AppConfig.key.in_(config_keys))
    )
    pen_takers_by_fixture: dict[int, tuple[int | None, int | None]] = {}
    for cfg in cfg_result.scalars().all():
        fid_str = cfg.key.split("::")[-1]
        try:
            data = json.loads(cfg.value)
            pen_takers_by_fixture[int(fid_str)] = (data.get("home"), data.get("away"))
        except Exception:
            pass

    min_edge = filter_config.min_edge if filter_config else 0.05
    min_odds = filter_config.min_odds if filter_config else 1.3
    max_odds = filter_config.max_odds if filter_config else 15.0

    for fixture_orm in fixtures:
        pen_home, pen_away = pen_takers_by_fixture.get(fixture_orm.id, (None, None))

        pricing = await load_match_pricing(
            session, fixture_orm,
            home_pen_taker_override=pen_home,
            away_pen_taker_override=pen_away,
        )
        if pricing is None:
            logger.debug("process_scraped: no pricing for fixture %d — skipping", fixture_orm.id)
            continue

        match_p00 = pricing.p00
        alloc_by_norm: dict[str, Any] = {}
        for alloc in pricing.home_players + pricing.away_players:
            alloc_by_norm[normalize_selection_name(alloc.player_name)] = alloc

        # Best odds per (player_name, market_type) across all bookmakers
        odds_result = await session.execute(
            select(PlayerOddsSnapshot).where(
                PlayerOddsSnapshot.fixture_id == fixture_orm.id
            )
        )
        best_odds_map: dict[tuple[str, str], dict] = {}
        for o in odds_result.scalars().all():
            key = (o.player_name, o.market_type)
            existing = best_odds_map.get(key)
            if existing is None or o.odds > existing["odds"]:
                best_odds_map[key] = {
                    "player_name": o.player_name,
                    "market_type": o.market_type,
                    "odds": o.odds,
                    "bookmaker": o.bookmaker,
                }

        seen_value_keys: set[tuple] = set()

        for (player_name, bet_type), odds_entry in best_odds_map.items():
            market_odds = odds_entry["odds"]
            bookmaker = odds_entry["bookmaker"]

            if market_odds <= 1 or market_odds < min_odds or market_odds > max_odds:
                continue

            norm_key = normalize_selection_name(player_name)
            alloc = alloc_by_norm.get(norm_key)
            if not alloc or alloc.position == "GK":
                continue

            if bet_type == "goalscorer":
                fair_odds_std = alloc.fair_odds_goal
                probability_std = alloc.prob_goal
                lambda_val = alloc.lambda_total
                has_form = alloc.has_form_goal
                fair_odds_ss = alloc.fair_odds_goal_supersub
                probability_ss = alloc.p_goal_supersub
            elif bet_type == "assist":
                fair_odds_std = alloc.fair_odds_assist
                probability_std = alloc.prob_assist
                lambda_val = alloc.lambda_assist
                has_form = alloc.has_form_assist
                fair_odds_ss = alloc.fair_odds_assist_supersub
                probability_ss = alloc.p_assist_supersub
            else:
                continue

            if not fair_odds_std or fair_odds_std <= 1:
                continue

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

            # --- Generate STANDARD and SUPERSUB recommendations ---
            for mkt_type, probability, fair_odds in (
                (MarketType.STANDARD, probability_std, fair_odds_std),
                (MarketType.SUPERSUB, probability_ss, fair_odds_ss),
            ):
                if not fair_odds or fair_odds <= 1 or probability <= 0:
                    continue

                edge = calculate_edge(fair_odds, market_odds)
                ev = round(ev_anytime(probability, market_odds), 4)
                ev_p00 = round(ev_anytime_p00_guarantee(probability, market_odds, match_p00), 4)
                is_value = edge >= min_edge

                rec_key = (fixture_orm.id, player_name, mkt_type, bet_type)
                existing_rec = rec_by_key.get(rec_key)

                if is_value:
                    seen_value_keys.add(rec_key)

                    if existing_rec is None:
                        new_rec = Recommendation(
                            fixture_id=fixture_orm.id,
                            player_name=player_name,
                            market_type=mkt_type,
                            bet_type=bet_type,
                            lambda_intensity=round(lambda_val, 4),
                            fair_probability=round(probability, 4),
                            fair_odds=fair_odds,
                            best_bookmaker=bookmaker,
                            best_odds=market_odds,
                            edge=edge,
                            classification="VALUE",
                            confidence=confidence,
                            xg_source=pricing.xg_source,
                            is_pen_taker=alloc.is_pen_taker,
                            explanation={
                                "model": "top_down_v2",
                                "xg_source": pricing.xg_source,
                                "lambda": round(lambda_val, 4),
                                "is_pen_taker": alloc.is_pen_taker,
                                "bet_type": bet_type,
                                "market_type": mkt_type.value,
                                "ev_anytime": ev,
                                "ev_p00_guarantee": ev_p00,
                                "p00": match_p00,
                            },
                            generated_utc=now,
                            status="pending",
                        )
                        session.add(new_rec)
                        rec_by_key[rec_key] = new_rec
                        stats["created"] += 1
                        new_value_alerts.append({
                            "player": player_name,
                            "fixture": f"{fixture_orm.home_team} vs {fixture_orm.away_team}",
                            "market": f"{bet_type}/{mkt_type.value}",
                            "odds": market_odds,
                            "bookmaker": bookmaker,
                            "edge": edge,
                            "resurrected": False,
                        })

                    elif existing_rec.status == "expired":
                        existing_rec.status = "pending"
                        existing_rec.best_odds = market_odds
                        existing_rec.best_bookmaker = bookmaker
                        existing_rec.edge = edge
                        existing_rec.confidence = confidence
                        existing_rec.generated_utc = now
                        stats["resurrected"] += 1
                        new_value_alerts.append({
                            "player": player_name,
                            "fixture": f"{fixture_orm.home_team} vs {fixture_orm.away_team}",
                            "market": f"{bet_type}/{mkt_type.value}",
                            "odds": market_odds,
                            "bookmaker": bookmaker,
                            "edge": edge,
                            "resurrected": True,
                        })

                    else:
                        # pending or approved — update if odds/edge changed
                        if (
                            abs(market_odds - existing_rec.best_odds) > 0.001
                            or abs(edge - existing_rec.edge) > 0.001
                        ):
                            existing_rec.best_odds = market_odds
                            existing_rec.best_bookmaker = bookmaker
                            existing_rec.edge = edge
                            existing_rec.confidence = confidence
                            stats["updated"] += 1

        # Expire pending/approved recs for players that appeared in fresh odds but are no longer EV+
        # Build set of (player_name, bet_type) pairs present in fresh odds
        active_player_bets: set[str] = {pname for (pname, _) in best_odds_map}
        for rec in all_recs:
            if rec.fixture_id != fixture_orm.id:
                continue
            if rec.status not in ("pending", "approved"):
                continue
            rec_key = (rec.fixture_id, rec.player_name, rec.market_type)
            if rec_key in seen_value_keys:
                continue
            # Expire if player was seen in fresh odds (not value) or still present
            if rec.player_name in active_player_bets:
                rec.status = "expired"
                stats["expired"] += 1

        # --- H2H recommendations (Poisson / team xG) ---
        h2h_recs = await _generate_h2h_recs(
            fixture_id=fixture_orm.id,
            lh=pricing.home_match_xg,
            la=pricing.away_match_xg,
            session=session,
            home_team=fixture_orm.home_team,
            away_team=fixture_orm.away_team,
        )
        for h2h in h2h_recs:
            outcome = h2h["outcome"]
            rec_key = (fixture_orm.id, h2h["display_name"], "h2h", "goal")
            existing_rec = rec_by_key.get(rec_key)

            if h2h["edge"] < 0:
                # Market no longer offers value — expire existing active rec
                if existing_rec and existing_rec.status in ("pending", "approved"):
                    existing_rec.status = "expired"
                    stats["expired"] += 1
                continue

            seen_value_keys.add(rec_key)

            if existing_rec is None:
                new_rec = Recommendation(
                    fixture_id=fixture_orm.id,
                    player_name=h2h["display_name"],
                    market_type="h2h",
                    lambda_intensity=h2h["lambda_intensity"],
                    fair_probability=h2h["fair_prob"],
                    fair_odds=h2h["fair_odds"],
                    best_bookmaker=h2h["best_bookmaker"],
                    best_odds=h2h["best_odds"],
                    edge=h2h["edge"],
                    classification=h2h["classification"],
                    confidence=0.0,
                    xg_source=pricing.xg_source,
                    is_pen_taker=False,
                    explanation={
                        "model": "poisson_h2h",
                        "xg_source": pricing.xg_source,
                        "lambda_home": round(pricing.home_match_xg, 4),
                        "lambda_away": round(pricing.away_match_xg, 4),
                        "outcome": outcome,
                    },
                    generated_utc=now,
                    status="pending",
                )
                session.add(new_rec)
                rec_by_key[rec_key] = new_rec
                stats["created"] += 1

            elif existing_rec.status == "expired":
                existing_rec.status = "pending"
                existing_rec.player_name = h2h["display_name"]
                existing_rec.best_odds = h2h["best_odds"]
                existing_rec.best_bookmaker = h2h["best_bookmaker"]
                existing_rec.edge = h2h["edge"]
                existing_rec.generated_utc = now
                stats["resurrected"] += 1

            else:
                existing_rec.player_name = h2h["display_name"]
                if (
                    abs(h2h["best_odds"] - existing_rec.best_odds) > 0.001
                    or abs(h2h["edge"] - existing_rec.edge) > 0.001
                ):
                    existing_rec.best_odds = h2h["best_odds"]
                    existing_rec.best_bookmaker = h2h["best_bookmaker"]
                    existing_rec.edge = h2h["edge"]
                    stats["updated"] += 1

    await session.commit()

    for alert in new_value_alerts:
        try:
            from app.notifications import send_telegram_alert
            prefix = "♻️ <b>VALUE BET RESSUSCITÉ</b>" if alert["resurrected"] else "🎯 <b>VALUE BET</b>"
            msg = (
                f"{prefix}\n"
                f"{alert['player']} — {alert['market']}\n"
                f"📋 {alert['fixture']}\n"
                f"📈 Cote: {alert['odds']} ({alert['bookmaker']}) | Edge: +{alert['edge']:.1%}\n"
                f"🤖 Ev0 Autopilot"
            )
            await send_telegram_alert(msg)
        except Exception as exc:
            logger.warning("Failed to send Telegram alert: %s", exc)

    return stats
