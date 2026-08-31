"""Background worker for scheduled data ingestion.

Runs periodic jobs via APScheduler:
- Fixtures sync (daily at 06:00 UTC) — Bzzoiro as sole calendar source
- Player stats update (daily at 07:00 UTC)
- Match events sync (daily at 08:00 UTC)
- Odds snapshots (hourly for upcoming matches)
- Recommendation generation (every 2 hours)
"""

import asyncio
import html
import json
import logging
from datetime import UTC, date, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cache import close_redis
from app.config import settings
from app.db import DATABASE_URL, async_session, engine
from app.ingestion.auto_settle import settle_approved_recommendations
from app.ingestion.bzzoiro.aggregate import aggregate_all_leagues
from app.ingestion.bzzoiro.sync_loan_teams import sync_loan_teams
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.sync_events import sync_events
from app.ingestion.bzzoiro.sync_player_stats import sync_player_stats
from app.ingestion.bzzoiro.sync_players import sync_players
from app.ingestion.bzzoiro.sync_predictions import sync_predictions
from app.ingestion.bzzoiro.sync_fixtures_from_bzz import sync_fixtures_from_bzz
from app.ingestion.bzzoiro.sync_reference import sync_leagues, sync_teams
from app.ingestion.bzzoiro.sync_bzzoiro_odds import sync_bzzoiro_odds
from app.ingestion.bzzoiro.sync_bzzoiro_lineups import sync_bzzoiro_lineups
from app.ingestion.bzzoiro.sync_match_detail import sync_apres_match, sync_avant_match
from app.ingestion.bzzoiro.sync_incidents import sync_incidents
from app.ingestion.bzzoiro.sync_wc_squads import sync_wc_squads
from app.ingestion.bzzoiro.constants import INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST
from app.ingestion.storage import (
    store_match_events,
    store_recommendation,
)
from app.ingestion.transfermarkt.failure_surface import surface_failure
from app.ingestion.transfermarkt.schedule import should_run_today
from app.ingestion.transfermarkt.sync_squads import sync_squads
from app.models.canonical_teams import CanonicalTeam
from app.models.settings import UserSettings
from app.models.squad_sync import SquadSyncRun
from app.scripts.transfermarkt_career import TransfermarktClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Defaults (used when no user settings exist)
DEFAULT_LEAGUES = ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "champions_league", "world_cup_2026"]

_LEAGUE_ALIASES = {"ligue1": "ligue_1", "ligue-1": "ligue_1"}


def normalize_league_key(key: str) -> str:
    """Normalize a league key, resolving legacy aliases."""
    return _LEAGUE_ALIASES.get(key, key)




async def _load_user_settings() -> dict[str, str]:
    """Load all user settings from the database."""
    try:
        async with async_session() as session:
            result = await session.execute(select(UserSettings))
            rows = result.scalars().all()
            return {row.key: row.value for row in rows}
    except Exception as exc:
        logger.warning("Failed to load user settings, using defaults: %s", exc)
        return {}


def _get_leagues(user_settings: dict[str, str]) -> list[str]:
    """Get active leagues from user settings or defaults."""
    raw = user_settings.get("active_leagues", "")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list) and parsed:
                return [normalize_league_key(lg) for lg in parsed]
        except (json.JSONDecodeError, TypeError):
            # Comma-separated fallback
            leagues = [lg.strip() for lg in raw.split(",") if lg.strip()]
            if leagues:
                return [normalize_league_key(lg) for lg in leagues]
    return DEFAULT_LEAGUES


# ── Job 1: Fixtures Sync (Bzzoiro) ───────────────────────────────


async def job_sync_fixtures():
    """Create missing Fixtures and resolve placeholder team names from BzzEvents.

    Bzzoiro is the authoritative fixture calendar source. This job replaces
    the legacy The Odds API sync.
    """
    logger.info("=== Starting fixture sync (Bzzoiro) ===")
    try:
        async with async_session() as session:
            created, updated = await sync_fixtures_from_bzz(session)
            logger.info(
                "=== Fixture sync complete: %d created, %d updated ===",
                created, updated,
            )
    except Exception as exc:
        logger.error("job_sync_fixtures: %s", exc, exc_info=True)


# ── Job 2b: Match Events Sync ─────────────────────────────────────


async def job_sync_match_events():
    """Sync match events (goals, assists) for finished fixtures via Bzzoiro incidents.

    Covers all Bzzoiro-sourced fixtures (external_id="bzz_*"), including WC2026.
    """
    logger.info("=== Starting match events sync (Bzzoiro incidents) ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping match events sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            processed = await sync_incidents(session, client, limit=100)
            logger.info("sync_incidents: %d fixtures processed", processed)
    except Exception as exc:
        logger.error("Error syncing match events: %s", exc, exc_info=True)
    logger.info("=== Match events sync complete ===")


# ── Job 4: Recommendation Generation ─────────────────────────────


async def job_generate_recommendations():
    """Generate betting recommendations for upcoming matches.

    Pipeline:
    1. Load upcoming fixtures (next 48h)
    2. For each fixture, load latest player stats + best odds
    3. Run recommendation engine
    4. Store results in DB
    """
    logger.info("=== Starting recommendation generation ===")

    if not settings.odds_api_key:
        logger.warning(
            "ODDS_API_KEY not configured — The Odds API snapshots are skipped. "
            "Recommendations will use direct/Unibet LVS odds from the DB."
        )

    try:
        from app.services.recommendation_service import get_recommendations_for_date
        from app.strategy.selector import RecommendationFilter

        user_settings = await _load_user_settings()

        # Build filter from user settings (fall back to defaults)
        filter_config = RecommendationFilter(
            min_edge=float(user_settings.get("min_edge", settings.min_edge_threshold)),
            min_confidence=float(user_settings.get("min_confidence", 0.50)),
            min_odds=float(user_settings.get("min_odds", 1.3)),
            max_odds=float(user_settings.get("max_odds", 15.0)),
            leagues=_get_leagues(user_settings),
        )
        logger.info(
            "Recommendation filter: min_edge=%.2f, min_conf=%.2f, odds=[%.1f-%.1f], leagues=%s",
            filter_config.min_edge,
            filter_config.min_confidence,
            filter_config.min_odds,
            filter_config.max_odds,
            filter_config.leagues,
        )

        # Generate for today
        now = datetime.now(UTC)

        recs, metadata = await async_session_scoped_call(
            get_recommendations_for_date, now, filter_config
        )

        logger.info(
            "Generated %d recommendations (fixtures=%s, players=%s, odds=%s)",
            len(recs),
            metadata.get("fixtures_count", 0),
            metadata.get("player_stats_count", 0),
            metadata.get("total_odds_entries", 0),
        )

        # Store recommendations in DB
        if recs:
            stored = 0
            skipped = 0
            async with async_session() as session:
                from types import SimpleNamespace

                from app.models.fixtures import Fixture
                from app.models.recommendations import Recommendation
                result = await session.execute(select(Fixture))
                fixtures_by_ext = {
                    f.external_id: SimpleNamespace(
                        id=f.id,
                        home_team=f.home_team,
                        away_team=f.away_team,
                        kickoff_utc=f.kickoff_utc,
                    )
                    for f in result.scalars().all()
                }

                # Load existing pending/approved recommendations to avoid duplicates
                existing_result = await session.execute(
                    select(
                        Recommendation.fixture_id,
                        Recommendation.player_name,
                        Recommendation.market_type,
                    ).where(Recommendation.status.in_(["pending", "approved"]))
                )
                existing_keys: set[tuple] = {
                    (row.fixture_id, row.player_name, row.market_type)
                    for row in existing_result
                }

                # Perplexity pre-match enrichment (once per fixture for VALUE recs)
                from app.ingestion.perplexity_enricher import enrich_fixture_context

                _value_by_fixture: dict[str, list[dict]] = {}
                for _vr in recs:
                    if _vr.get("classification") == "VALUE" and _vr.get("fixture_id"):
                        _value_by_fixture.setdefault(_vr["fixture_id"], []).append(_vr)

                _perp_ctx: dict[str, dict] = {}
                for _ext_id, _frecs in _value_by_fixture.items():
                    _fx = fixtures_by_ext.get(_ext_id)
                    if not _fx:
                        continue
                    _kickoff = _fx.kickoff_utc.strftime("%Y-%m-%d") if _fx.kickoff_utc else ""
                    _players = [
                        {
                            "name": _r["player_name"],
                            "team": _r.get("team", ""),
                            "market": _r.get("market_type", ""),
                        }
                        for _r in _frecs
                    ]
                    _perp_ctx[_ext_id] = await enrich_fixture_context(
                        home_team=_fx.home_team,
                        away_team=_fx.away_team,
                        kickoff_date=_kickoff,
                        players=_players,
                    )

                for _vr in recs:
                    if _vr.get("classification") != "VALUE":
                        continue
                    _pctx = _perp_ctx.get(_vr.get("fixture_id", ""), {}).get(
                        _vr.get("player_name", "")
                    )
                    if not _pctx:
                        continue
                    _cs = _pctx.get("context_score", 1.0)
                    _vr["confidence"] = round(min(1.0, _vr.get("confidence", 0.5) * _cs), 4)
                    if _cs <= 0.15:
                        _vr["classification"] = "NO_VALUE"
                    _vr.setdefault("explanation", {})["perplexity"] = {
                        "confirmed_starter": _pctx.get("confirmed_starter"),
                        "injury_risk": _pctx.get("injury_risk"),
                        "recent_form": _pctx.get("recent_form"),
                        "context_score": _cs,
                        "notes": _pctx.get("notes", ""),
                    }

                for rec in recs:
                    fixture_ext_id = rec.get("fixture_id", "")
                    fixture = fixtures_by_ext.get(fixture_ext_id)
                    fixture_id = fixture.id if fixture else 0
                    player_name = rec.get("player_name", "")
                    market_type = rec.get("market_type", "")

                    if (fixture_id, player_name, market_type) in existing_keys:
                        skipped += 1
                        continue

                    try:
                        await store_recommendation(
                            session,
                            fixture_id=fixture_id,
                            player_name=player_name,
                            market_type=market_type,
                            pricing_result={
                                "lambda_intensity": rec.get("lambda_intensity", 0),
                                "probability": rec.get("fair_probability", 0),
                                "fair_odds": rec.get("fair_odds", 0),
                                "explanation": rec.get("explanation", {}),
                            },
                            best_bookmaker=rec.get("best_bookmaker", ""),
                            best_odds=rec.get("market_odds", 0),
                            edge=rec.get("edge", 0),
                            xg_source=rec.get("xg_source"),
                            is_pen_taker=rec.get("is_pen_taker", False) or False,
                            confidence=rec.get("confidence"),
                            classification=rec.get("classification"),
                        )
                        existing_keys.add((fixture_id, player_name, market_type))
                        stored += 1
                        # Note : pas de notification ici. Les alertes VALUE sont
                        # émises par process_scraped_fixtures (canal unique) —
                        # ce job n'est de toute façon pas planifié.
                    except Exception as exc:
                        logger.warning("Failed to store recommendation: %s", exc)
                        await session.rollback()

            logger.info(
                "Stored %d/%d recommendations (%d duplicates skipped)",
                stored, len(recs), skipped,
            )

    except Exception as exc:
        logger.error("Error generating recommendations: %s", exc, exc_info=True)

    logger.info("=== Recommendation generation complete ===")


async def async_session_scoped_call(func, dt, filter_config):
    """Call get_recommendations_for_date with a fresh DB session."""
    async with async_session() as session:
        return await func(dt, session, filter_config)


# ── Job 5: Autopilot Run ──────────────────────────────────────────


async def job_autopilot_run():
    """Every 2h: run the RL agent on today's pending VALUE recommendations.

    Creates AutopilotDecision records in paper mode and auto-approves them.
    Only runs when autopilot_enabled=true in user settings.
    """
    logger.info("=== Starting autopilot run ===")

    try:
        user_settings = await _load_user_settings()
        if user_settings.get("autopilot_enabled") != "true":
            logger.info("Autopilot disabled — skipping run")
            return

        from app.autopilot.agent import ACTIONS
        from app.autopilot.features import extract_features_with_context
        from app.autopilot.trainer import _compute_stake, _load_agent, weights_exist

        if not weights_exist():
            logger.warning("Autopilot weights not found — skipping run (train first)")
            return

        agent = _load_agent()
        mode = user_settings.get("autopilot_mode", "paper")

        from app.models.autopilot import AutopilotDecision
        from app.models.fixtures import Fixture
        from app.models.player_odds_snapshot import PlayerOddsSnapshot as OddsSnapshot
        from app.models.recommendations import Recommendation

        async with async_session() as session:
            # Look at all pending VALUE recs whose fixture hasn't kicked off yet,
            # not just today's — ensures recs generated yesterday are still acted on.
            from app.models.fixtures import Fixture as _Fixture
            stmt = (
                select(Recommendation)
                .join(_Fixture, Recommendation.fixture_id == _Fixture.id)
                .where(
                    Recommendation.classification == "VALUE",
                    Recommendation.status == "pending",
                    _Fixture.kickoff_utc > datetime.now(UTC),
                )
                .order_by(Recommendation.edge.desc())
            )
            result = await session.execute(stmt)
            recs = list(result.scalars().all())

            if not recs:
                logger.info("Autopilot: no pending VALUE recommendations today")
                return

            # Load fixtures for names
            fixture_ids = list({r.fixture_id for r in recs})
            fx_result = await session.execute(
                select(Fixture).where(Fixture.id.in_(fixture_ids))
            )
            fixture_map = {fx.id: fx for fx in fx_result.scalars().all()}

            # Get current bankroll balance
            from app.models.bankroll import BankrollEntry
            bal_result = await session.execute(
                select(BankrollEntry).order_by(BankrollEntry.transacted_utc.desc()).limit(1)
            )
            latest_entry = bal_result.scalar_one_or_none()
            bankroll = latest_entry.balance_after if latest_entry else 1000.0

            from app.notifications import notify_autopilot_run

            # Load stats for scorecard
            from app.models.autopilot import AutopilotDecision as _AD
            stats_result = await session.execute(
                select(
                    _AD.result,
                    _AD.pnl,
                    _AD.stake,
                ).where(_AD.result.isnot(None))
            )
            _stats_rows = stats_result.all()
            _sc_settled = len(_stats_rows)
            _sc_won = sum(1 for r in _stats_rows if r.result == "won")
            _sc_total_pnl = sum(r.pnl or 0.0 for r in _stats_rows)
            _sc_staked = sum(r.stake or 0.0 for r in _stats_rows if r.result != "void")

            # Count fine-tune runs
            from app.models.autopilot import AutopilotDecision as _AD2
            ft_result = await session.execute(
                select(_AD2.id).where(_AD2.trained_on.is_(True)).limit(1)
            )
            # Approximate: count distinct fine-tune batches via settings
            _ft_setting_result = await session.execute(
                select(UserSettings).where(UserSettings.key == "autopilot_fine_tune_runs")
            )
            _ft_row = _ft_setting_result.scalar_one_or_none()
            _sc_ft_runs = int(_ft_row.value) if _ft_row else 0

            # Build all rec dicts up front for contextual features
            all_rec_dicts = []
            for rec in recs:
                all_rec_dicts.append({
                    "edge": rec.edge,
                    "confidence": rec.confidence,
                    "best_odds": rec.best_odds,
                    "fair_odds": rec.fair_odds,
                    "fair_probability": rec.fair_probability,
                    "lambda_intensity": rec.lambda_intensity,
                    "market_type": rec.market_type,
                    "explanation": rec.explanation or {},
                })

            decisions_made = 0
            bets_this_run: list[dict] = []
            for rec, rec_dict in zip(recs, all_rec_dicts):
                import json as _json

                # Query odds history for this player+market+fixture
                odds_stmt = (
                    select(OddsSnapshot.odds)
                    .where(
                        OddsSnapshot.fixture_id == rec.fixture_id,
                        OddsSnapshot.player_name == rec.player_name,
                        OddsSnapshot.market_type == rec.market_type,
                    )
                    .order_by(OddsSnapshot.scraped_at.asc())
                )
                odds_result = await session.execute(odds_stmt)
                odds_history = [row[0] for row in odds_result.all()]

                features = extract_features_with_context(rec_dict, all_rec_dicts, odds_history or None)
                # In paper mode, always explore so the agent accumulates real
                # outcomes and can fine-tune.  Live mode uses greedy inference.
                action_idx = agent.act(features, explore=(mode == "paper"))
                fraction = ACTIONS[action_idx]
                stake = _compute_stake(action_idx, rec_dict, bankroll)

                fx = fixture_map.get(rec.fixture_id)
                fixture_name = f"{fx.home_team} vs {fx.away_team}" if fx else str(rec.fixture_id)
                league = fx.league if fx else ""

                decision = AutopilotDecision(
                    recommendation_id=rec.id,
                    features_json=_json.dumps(features.tolist()),
                    action_idx=action_idx,
                    kelly_fraction=fraction,
                    stake=stake,
                    best_odds=rec.best_odds,
                    mode=mode,
                    player_name=rec.player_name,
                    fixture_name=fixture_name,
                    market_type=rec.market_type,
                    league=league,
                    created_utc=datetime.now(UTC),
                )
                session.add(decision)

                # Auto-approve recommendation if agent decided to bet
                if action_idx > 0 and stake > 0:
                    rec.status = "approved"
                    rec.decided_utc = datetime.now(UTC)
                    decisions_made += 1
                    bets_this_run.append({
                        "player_name": rec.player_name,
                        "fixture_name": fixture_name,
                        "market_type": rec.market_type,
                        "best_odds": rec.best_odds,
                        "edge": rec.edge,
                        "stake": stake,
                        "action_idx": action_idx,
                    })

            await session.commit()
            logger.info(
                "Autopilot run: evaluated %d recs, approved %d bets (mode=%s)",
                len(recs), decisions_made, mode,
            )

            # Un seul message pour tout le run (scorecard non répété)
            await notify_autopilot_run(
                bets=bets_this_run,
                mode=mode,
                settled=_sc_settled,
                won=_sc_won,
                total_pnl=_sc_total_pnl,
                staked_total=_sc_staked,
                fine_tune_runs=_sc_ft_runs,
            )

    except Exception as exc:
        logger.error("Error in autopilot run: %s", exc, exc_info=True)

    logger.info("=== Autopilot run complete ===")


# ── Job 6: Autopilot Settle ───────────────────────────────────────


async def job_autopilot_settle():
    """Daily at 09:00 UTC: settle paper trades from match events.

    Finds AutopilotDecisions where result is NULL and linked fixture is finished.
    Checks MatchEvent table for goals/assists. Updates result + pnl + reward.
    Triggers fine_tune_from_db() when >= 10 new decisions are settled.
    """
    logger.info("=== Starting autopilot settle ===")

    try:
        from app.autopilot.trainer import fine_tune_from_db
        from app.ingestion.auto_settle import _names_match
        from app.ingestion.bzzoiro.sync_incidents import SENTINEL_UNAVAILABLE_TYPE
        from app.models.autopilot import AutopilotDecision
        from app.models.fixtures import Fixture
        from app.models.match_events import MatchEvent
        from app.models.recommendations import Recommendation

        async with async_session() as session:
            # Find unsettled decisions linked to a recommendation
            stmt = (
                select(AutopilotDecision, Recommendation, Fixture)
                .join(
                    Recommendation,
                    AutopilotDecision.recommendation_id == Recommendation.id,
                    isouter=True,
                )
                .join(Fixture, Recommendation.fixture_id == Fixture.id, isouter=True)
                .where(
                    AutopilotDecision.result.is_(None),
                    AutopilotDecision.recommendation_id.isnot(None),
                    Fixture.status == "finished",
                )
            )
            result = await session.execute(stmt)
            rows = result.all()

            if not rows:
                logger.info("Autopilot settle: no unsettled decisions with finished fixtures")
                return

            logger.info("Autopilot settle: %d decisions to settle", len(rows))

            settled_count = 0
            batch_won = 0
            batch_lost = 0
            batch_pnl = 0.0
            # Par fixture : types d'events présents + nb d'events "but"
            # (cache — le backlog peut être gros)
            from sqlalchemy import func as sa_func
            _GOAL_TYPES = ("goal", "own_goal", "penalty_goal")
            _fixture_events_info: dict[int, dict] = {}
            for decision, rec, fixture in rows:
                if not rec or not fixture:
                    continue

                if fixture.id not in _fixture_events_info:
                    _ev_res = await session.execute(
                        select(MatchEvent.event_type, sa_func.count())
                        .where(MatchEvent.fixture_id == fixture.id)
                        .group_by(MatchEvent.event_type)
                    )
                    _counts = dict(_ev_res.all())
                    _fixture_events_info[fixture.id] = {
                        "types": set(_counts),
                        "goals": sum(_counts.get(t, 0) for t in _GOAL_TYPES),
                    }
                _ev_info = _fixture_events_info[fixture.id]

                # Couverture events COMPLÈTE exigée avant tout règlement :
                # régler sur des events partiels marque LOST des paris gagnants
                # (constaté le 09/07 : ~40% de couverture → 638 lost dont une
                # partie était en réalité gagnée).
                _complete = (
                    fixture.home_score is not None
                    and fixture.away_score is not None
                    and _ev_info["goals"] == fixture.home_score + fixture.away_score
                )
                if not _complete:
                    # Incidents définitivement indisponibles (404 Bzzoiro),
                    # jamais complétables → VOID. Sinon on attend le backfill.
                    if SENTINEL_UNAVAILABLE_TYPE in _ev_info["types"]:
                        decision.result = "void"
                        decision.pnl = 0.0
                        decision.reward = 0.0
                        decision.settled_at = datetime.now(UTC)
                        settled_count += 1
                    continue

                # Reward shaping for skip decisions (action_idx == 0)
                if decision.action_idx == 0:
                    # Map market_type → MatchEvent.event_type
                    _skip_market_to_event = {
                        "goalscorer": "goal",
                        "anytime_score": "goal",
                        "assist": "assist",
                        "anytime_assist": "assist",
                    }
                    _skip_event_type = _skip_market_to_event.get(
                        rec.market_type, rec.market_type
                    )

                    ev_skip_result = await session.execute(
                        select(MatchEvent).where(
                            MatchEvent.fixture_id == fixture.id,
                            MatchEvent.event_type == _skip_event_type,
                        )
                    )
                    would_have_won = any(
                        _names_match(ev.player_name, decision.player_name)
                        for ev in ev_skip_result.scalars().all()
                    )

                    if would_have_won:
                        # Skip was wrong — missed a winner
                        decision.result = "skip_wrong"
                        decision.pnl = 0.0
                        decision.reward = -0.01
                    else:
                        # Skip was right — avoided a loss
                        decision.result = "skip_correct"
                        decision.pnl = 0.0
                        decision.reward = 0.02

                    decision.settled_at = datetime.now(UTC)
                    settled_count += 1
                    continue

                # Map market_type → MatchEvent.event_type
                _market_to_event = {
                    "goalscorer": "goal",
                    "anytime_score": "goal",
                    "assist": "assist",
                    "anytime_assist": "assist",
                }
                _event_type = _market_to_event.get(rec.market_type, rec.market_type)

                # Check match events for outcome (même matching de noms que
                # le settlement principal : accents/tirets/2e prénom)
                ev_result = await session.execute(
                    select(MatchEvent).where(
                        MatchEvent.fixture_id == fixture.id,
                        MatchEvent.event_type == _event_type,
                    )
                )
                won = any(
                    _names_match(ev.player_name, rec.player_name)
                    for ev in ev_result.scalars().all()
                )

                result_str = "won" if won else "lost"
                stake = decision.stake or 0.0
                odds = decision.best_odds or rec.best_odds or 1.0
                pnl = round(stake * (odds - 1) if won else -stake, 2)

                # Approx bankroll at decision time for reward scaling
                reward = pnl / max(1000.0, 1.0)

                decision.result = result_str
                decision.pnl = pnl
                decision.reward = reward
                decision.settled_at = datetime.now(UTC)

                # Also update linked recommendation (always 10€ fixed stake for display)
                if rec.result is None:
                    rec.result = result_str
                    rec.pnl = round(10.0 * (decision.best_odds - 1) if won else -10.0, 2)
                    rec.settled_utc = datetime.now(UTC)

                settled_count += 1
                if result_str == "won":
                    batch_won += 1
                elif result_str == "lost":
                    batch_lost += 1
                batch_pnl += pnl

            await session.commit()
            logger.info("Autopilot settle: settled %d decisions", settled_count)

            if settled_count == 0:
                return

            # Load global stats for notifications
            from app.models.autopilot import AutopilotDecision as _AD
            from app.notifications import notify_autopilot_fine_tune, notify_autopilot_settle

            all_stats = await session.execute(
                select(_AD.result, _AD.pnl, _AD.stake).where(_AD.result.isnot(None))
            )
            _rows = all_stats.all()
            _total_settled = len(_rows)
            _total_won = sum(1 for r in _rows if r.result == "won")
            _total_pnl = sum(r.pnl or 0.0 for r in _rows)
            _total_staked = sum(r.stake or 0.0 for r in _rows if r.result != "void")

            _ft_setting = await session.execute(
                select(UserSettings).where(UserSettings.key == "autopilot_fine_tune_runs")
            )
            _ft_row = _ft_setting.scalar_one_or_none()
            _ft_runs = int(_ft_row.value) if _ft_row else 0

            await notify_autopilot_settle(
                batch_won=batch_won,
                batch_lost=batch_lost,
                batch_pnl=batch_pnl,
                total_settled=_total_settled,
                total_won=_total_won,
                total_pnl=_total_pnl,
                staked_total=_total_staked,
                fine_tune_runs=_ft_runs,
            )

            # Fine-tune if enough new data
            if settled_count >= 10:
                logger.info("Triggering fine_tune_from_db (settled=%d)", settled_count)
                ft_result = await fine_tune_from_db(session)
                logger.info("Fine-tune complete: %s", ft_result)

                # Increment fine-tune run counter
                _new_ft_runs = _ft_runs + 1
                if _ft_row:
                    _ft_row.value = str(_new_ft_runs)
                else:
                    session.add(UserSettings(key="autopilot_fine_tune_runs", value=str(_new_ft_runs)))
                await session.commit()

                await notify_autopilot_fine_tune(
                    decisions_used=ft_result["decisions_used"],
                    td_error_mean=ft_result["td_error_mean"],
                    fine_tune_runs=_new_ft_runs,
                    settled=_total_settled,
                    won=_total_won,
                    total_pnl=_total_pnl,
                    staked_total=_total_staked,
                )

    except Exception as exc:
        logger.error("Error in autopilot settle: %s", exc, exc_info=True)

    logger.info("=== Autopilot settle complete ===")


# ── Job: Expire Recommendations ───────────────────────────────────


async def job_expire_recommendations():
    """Every 5 min: expire pending recommendations whose fixture has kicked off."""
    from app.db import async_session
    from app.models.recommendations import Recommendation
    from app.models.fixtures import Fixture

    now = datetime.now(UTC)
    async with async_session() as session:
        result = await session.execute(
            select(Recommendation)
            .join(Fixture, Recommendation.fixture_id == Fixture.id)
            .where(
                Recommendation.status == "pending",
                Fixture.kickoff_utc <= now,
            )
        )
        recs = result.scalars().all()
        for rec in recs:
            rec.status = "expired"
        if recs:
            await session.commit()
            logger.info("Expired %d recommendations", len(recs))


async def job_refresh_recommendation_odds():
    """Every 30 min: update best_odds/edge on pending recs from fresh snapshots; expire EV-."""
    from app.db import async_session
    from app.models.fixtures import Fixture
    from app.models.player_odds_snapshot import PlayerOddsSnapshot
    from app.models.recommendations import Recommendation

    now = datetime.now(UTC)
    freshness_cutoff = now - timedelta(hours=2)

    async with async_session() as session:
        result = await session.execute(
            select(Recommendation)
            .join(Fixture, Recommendation.fixture_id == Fixture.id)
            .where(
                Recommendation.status == "pending",
                Fixture.kickoff_utc > now,
            )
        )
        recs = result.scalars().all()
        if not recs:
            return

        snap_result = await session.execute(
            select(
                PlayerOddsSnapshot.fixture_id,
                PlayerOddsSnapshot.player_name,
                PlayerOddsSnapshot.market_type,
                PlayerOddsSnapshot.bookmaker,
                PlayerOddsSnapshot.odds,
            ).where(PlayerOddsSnapshot.scraped_at >= freshness_cutoff)
        )
        odds_map: dict[tuple, tuple[float, str]] = {}
        for row in snap_result:
            key = (row.fixture_id, row.player_name, row.market_type)
            if key not in odds_map or row.odds > odds_map[key][0]:
                odds_map[key] = (row.odds, row.bookmaker)

        stale_cutoff = now - timedelta(hours=12)
        refreshed = expired_count = stale_expired = 0
        for rec in recs:
            key = (rec.fixture_id, rec.player_name, rec.market_type)
            if key not in odds_map:
                # No fresh snapshot — expire if match within 48h and odds older than 12h
                fixture_kickoff = getattr(rec, "_fixture_kickoff", None)
                last_snap_age = (now - rec.generated_utc).total_seconds() / 3600
                if last_snap_age > 12:
                    rec.status = "expired"
                    stale_expired += 1
                continue
            best_odds, best_bookmaker = odds_map[key]
            new_edge = best_odds * rec.fair_probability - 1
            rec.best_odds = best_odds
            rec.best_bookmaker = best_bookmaker
            rec.edge = new_edge
            if new_edge <= 0:
                rec.status = "expired"
                expired_count += 1
            refreshed += 1

        if refreshed or stale_expired:
            await session.commit()
            logger.info(
                "Refreshed odds on %d pending recs (%d expired EV-, %d expired stale)",
                refreshed, expired_count, stale_expired,
            )


async def job_auto_settle():
    """Every 3 hours: auto-settle approved recommendations via Understat."""
    logger.info("=== Starting auto-settle job ===")
    from app.models.fixtures import Fixture
    from app.alerts import send_alert

    try:
        async with async_session() as session:
            stats = await settle_approved_recommendations(session)

        settled = stats["settled"]
        logger.info("auto_settle: settled %d recommendations", settled)

        if settled > 0:
            await send_alert(
                f"✅ <b>[Ev0] Settlement automatique</b>\n\n"
                f"{settled} pari(s) réglé(s) :\n"
                f"• Gagnés : {stats['won']}\n"
                f"• Perdus : {stats['lost']}\n"
                f"• Voids : {stats['void']}",
                channel="autopilot",
            )

        # Alert if recs are stuck for fixtures finished >48h ago
        if stats["stuck_fixture_ids"]:
            now = datetime.now(UTC)
            async with async_session() as session:
                result = await session.execute(
                    select(Fixture).where(
                        Fixture.id.in_(stats["stuck_fixture_ids"]),
                        Fixture.kickoff_utc < now - timedelta(hours=48),
                    )
                )
                old_stuck = list(result.scalars().all())

            if old_stuck:
                names = "\n".join(
                    f"• {fx.home_team} vs {fx.away_team} ({fx.kickoff_utc.strftime('%d/%m')})"
                    for fx in old_stuck[:5]
                )
                await send_alert(
                    f"🚨 <b>[Ev0] Settlement bloqué</b>\n\n"
                    f"{len(old_stuck)} match(s) terminé(s) depuis >48h impossible(s) à settler :\n"
                    f"{names}\n\n"
                    f"Cause probable : PlayerMatchMinutes ou MatchEvents manquants.",
                    channel="incidents",
                )

    except Exception:
        logger.exception("auto_settle job failed")


# ── Job: Auto-Finish Fixtures ─────────────────────────────────────


async def job_auto_finish_fixtures():
    """Every 30 min: mark fixtures as finished if kickoff + 2h has passed.

    FotMob /api/leagues returns 404 so fixture statuses never update automatically.
    This time-based fallback ensures settlement can proceed for recent matches.
    Sends a Telegram alert listing which fixtures were auto-finished.
    """
    from app.models.fixtures import Fixture
    from app.alerts import send_alert

    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=2)

    async with async_session() as session:
        result = await session.execute(
            select(Fixture).where(
                Fixture.status == "scheduled",
                Fixture.kickoff_utc < cutoff,
            )
        )
        fixtures = list(result.scalars().all())

        if not fixtures:
            logger.debug("job_auto_finish_fixtures: no fixtures to auto-finish")
            return

        for fx in fixtures:
            fx.status = "finished"

        await session.commit()

        logger.info(
            "job_auto_finish_fixtures: auto-finished %d fixtures (kickoff + 2h passed)",
            len(fixtures),
        )

        names = "\n".join(
            f"• {fx.home_team} vs {fx.away_team} ({fx.kickoff_utc.strftime('%d/%m %H:%M')} UTC)"
            for fx in fixtures[:10]
        )
        await send_alert(
            f"⏱️ <b>[Ev0] Auto-finish fixtures</b>\n\n"
            f"{len(fixtures)} match(s) passés en <b>finished</b> (kickoff +2h dépassé) :\n"
            f"{names}"
            + (" ..." if len(fixtures) > 10 else ""),
            channel="autopilot",
        )


# ── Job: Settlement Pipeline (every 30 min) ──────────────────────


async def job_settle_pipeline():
    """Every 30 min: WC events refresh → Bzzoiro status sync → auto-finish → sync match events → settle → WC stats."""
    logger.info("=== Settlement pipeline: start ===")

    # Refresh WC2026 BzzEvents from Bzzoiro API so status reflects finished matches.
    # This runs every 30 min (not every 6h like the full event sync) so WC match
    # stats are available within 30 min of a match ending, not up to 7h later.
    if settings.bzzoiro_api_key:
        try:
            from app.ingestion.bzzoiro.sync_events import sync_events
            from app.ingestion.bzzoiro.constants import INTERNATIONAL_LEAGUE_INTERNAL_IDS
            async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
                wc_internal_id = INTERNATIONAL_LEAGUE_INTERNAL_IDS["world_cup_2026"]
                await sync_events(
                    session, client,
                    league_internal_ids=[wc_internal_id],
                    days_back=3, days_forward=14,
                )
        except Exception as exc:
            logger.error("Settlement pipeline: WC events refresh failed: %s", exc, exc_info=True)

    from app.ingestion.bzzoiro.sync_fixture_status import sync_fixture_status_from_bzz
    try:
        async with async_session() as session:
            await sync_fixture_status_from_bzz(session)
    except Exception as exc:
        logger.error("Settlement pipeline: bzz status sync failed: %s", exc, exc_info=True)

    # Resolve placeholder team names (W73, 2E, …) → real names using freshly-synced BzzEvents.
    # Must run after sync_events so R16+ bzz_events already have the correct team_api_ids.
    try:
        async with async_session() as session:
            created, updated = await sync_fixtures_from_bzz(session)
        if created or updated:
            logger.info("Settlement pipeline: fixtures resolved created=%d updated=%d", created, updated)
    except Exception as exc:
        logger.error("Settlement pipeline: fixture name resolution failed: %s", exc, exc_info=True)

    await job_auto_finish_fixtures()
    await job_sync_match_events()
    await job_auto_settle()

    # Recompute bracket advancement probabilities after any newly finished match
    await job_sync_wc_bracket()

    # Sync WC2026 player stats for any match that just became finished
    try:
        from app.ingestion.wc2026.sync_wc_match_stats import sync_wc_match_stats
        async with async_session() as session:
            result = await sync_wc_match_stats(session)
        if result.synced > 0:
            logger.info("Settlement pipeline: WC stats synced=%d", result.synced)
        if result.errors:
            logger.warning("Settlement pipeline: WC stats errors=%s", result.errors)
    except Exception as exc:
        logger.error("Settlement pipeline: WC stats sync failed: %s", exc, exc_info=True)

    logger.info("=== Settlement pipeline: done ===")


# ── Job: Autopilot Re-optimize (Weekly) ──────────────────────────


async def job_autopilot_reoptimize():
    """Weekly: re-run Optuna optimization on current code with latest data.

    Updates agent weights with best hyperparams found.
    """
    from app.autopilot.autoresearch import run_optimization
    from app.backtest.simulator import simulate_historical

    try:
        async with async_session() as db:
            records = await simulate_historical(db)

        if len(records) < 300:
            logger.info("Not enough records for optimization (%d)", len(records))
            return

        result = run_optimization(records, n_trials=100)

        if "error" in result:
            logger.warning("Optimization failed: %s", result["error"])
            return

        logger.info(
            "Autopilot optimization: log_wealth=%.4f roi=%.2f%% dsr=%.3f (%d trials, %.1fs)",
            result.get("best_log_wealth", 0),
            result.get("best_roi", 0) * 100,
            result.get("dsr", 0),
            result.get("n_trials", 0),
            result.get("duration_s", 0),
        )

        # Retrain agent with best params and save
        best_params = result.get("best_params", {})
        if best_params:
            from app.autopilot.agent import LinearQAgent
            from app.autopilot.trainer import _save_agent, train_single_pass

            agent = LinearQAgent.from_params(best_params)
            value_records = sorted(
                [r for r in records if r.get("edge", 0) >= 0.05],
                key=lambda r: r["date"],
            )
            for _ in range(best_params.get("n_epochs", 3)):
                train_single_pass(
                    agent,
                    value_records,
                    l1_lambda=best_params.get("l1_lambda", 0),
                )
            _save_agent(agent)
            logger.info("Agent retrained and saved with optimized params")

        # Persist result to Redis for dashboard display
        from app.api.autopilot import _save_optimization_result
        _save_optimization_result(result)

    except Exception:
        logger.exception("job_autopilot_reoptimize failed")


# ── Odds Scheduler ────────────────────────────────────────────────────────────

_odds_scheduler = None  # initialized lazily


async def job_odds_scheduler_tick() -> None:
    """Adaptive odds scraper — fires Betclic + Unibet for fixtures due based on KO distance.

    After each scrape, immediately runs the recommendation pipeline on scraped fixtures:
    market xG recomputation, edge comparison, create/update/expire/resurrect recs.
    """
    global _odds_scheduler
    from app.ingestion.odds_scheduler import OddsScheduler
    if _odds_scheduler is None:
        _odds_scheduler = OddsScheduler()

    scraped_fixture_ids: list[int] = []
    async with async_session() as session:
        try:
            n, scraped_fixture_ids = await _odds_scheduler.tick(session)
            if n:
                logger.info("job_odds_scheduler_tick: scraped %d fixtures", n)
        except Exception as exc:
            logger.error("job_odds_scheduler_tick error: %s", exc, exc_info=True)
            return

    if not scraped_fixture_ids:
        return

    try:
        from app.services.recommendation_service import process_scraped_fixtures
        from app.strategy.selector import RecommendationFilter

        user_settings = await _load_user_settings()
        filter_config = RecommendationFilter(
            min_edge=float(user_settings.get("min_edge", settings.min_edge_threshold)),
            min_confidence=float(user_settings.get("min_confidence", 0.50)),
            min_odds=float(user_settings.get("min_odds", 1.3)),
            max_odds=float(user_settings.get("max_odds", 15.0)),
            leagues=_get_leagues(user_settings),
        )

        async with async_session() as session:
            rec_stats = await process_scraped_fixtures(
                scraped_fixture_ids, session, filter_config
            )

        if any(v > 0 for v in rec_stats.values()):
            logger.info(
                "Recommendations — created=%d resurrected=%d updated=%d expired=%d",
                rec_stats["created"],
                rec_stats["resurrected"],
                rec_stats["updated"],
                rec_stats["expired"],
            )
    except Exception as exc:
        logger.error(
            "job_odds_scheduler_tick: recommendation pipeline error: %s",
            exc,
            exc_info=True,
        )


# ── Bzzoiro Jobs ─────────────────────────────────────────────────


async def job_sync_bzzoiro_reference():
    """Daily at 02:00 UTC: sync Bzzoiro leagues and teams reference data."""
    logger.info("=== Starting Bzzoiro reference sync ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping reference sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            result_leagues = await sync_leagues(session, client)
            logger.info("Bzzoiro leagues synced: %s", result_leagues)
            result_teams = await sync_teams(session, client)
            logger.info("Bzzoiro teams synced: %s", result_teams)
    except Exception as exc:
        logger.error("Error in Bzzoiro reference sync: %s", exc, exc_info=True)
    logger.info("=== Bzzoiro reference sync complete ===")


async def job_sync_bzzoiro_players():
    """Daily at 03:00 UTC: sync Bzzoiro player roster data."""
    logger.info("=== Starting Bzzoiro players sync ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping players sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            result = await sync_players(session, client)
            logger.info("Bzzoiro players synced: %s", result)
    except Exception as exc:
        logger.error("Error in Bzzoiro players sync: %s", exc, exc_info=True)
    logger.info("=== Bzzoiro players sync complete ===")


async def job_aggregate_season_stats():
    """Daily at 04:00 UTC: aggregate per-match stats into season totals."""
    logger.info("=== Starting Bzzoiro season stats aggregation ===")
    try:
        async with async_session() as session:
            result = await aggregate_all_leagues(session)
            logger.info("Bzzoiro season aggregation: %s", result)
    except Exception as exc:
        logger.error("Error in Bzzoiro season stats aggregation: %s", exc, exc_info=True)
    logger.info("=== Bzzoiro season stats aggregation complete ===")


async def job_sync_loan_teams():
    """Daily at 04:30 UTC: detect on-loan players from match stats and update loan_team_*."""
    logger.info("=== Starting loan team sync ===")
    try:
        async with async_session() as session:
            changed = await sync_loan_teams(session)
            logger.info("Loan team sync: %d rows changed", changed)
    except Exception as exc:
        logger.error("Error in loan team sync: %s", exc, exc_info=True)
    logger.info("=== Loan team sync complete ===")


async def job_sync_bzzoiro_events():
    """Every 6h: sync Bzzoiro match events — 3 days back, 30 days forward."""
    logger.info("=== Starting Bzzoiro events sync ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping events sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            result = await sync_events(session, client, days_back=3, days_forward=30)
            logger.info("Bzzoiro events synced: %s", result)
            intl_count = await sync_events(
                session,
                client,
                days_forward=30,
                league_internal_ids=INTERNATIONAL_LEAGUE_INTERNAL_ID_LIST,
            )
            logger.info("job_sync_events: %d international events synced", intl_count)
    except Exception as exc:
        logger.error("Error in Bzzoiro events sync: %s", exc, exc_info=True)

    # Sync fixture dates/status from BzzEvent
    try:
        from app.ingestion.bzzoiro.sync_fixture_status import sync_fixture_status_from_bzz
        async with async_session() as session:
            n = await sync_fixture_status_from_bzz(session)
            logger.info("Fixture status sync from BzzEvent: %d updated", n)
    except Exception as exc:
        logger.error("Error in fixture status sync from BzzEvent: %s", exc, exc_info=True)

    logger.info("=== Bzzoiro events sync complete ===")


async def job_sync_bzzoiro_events_full_season():
    """Weekly: full-season event refresh to catch any gaps."""
    logger.info("=== Starting Bzzoiro full-season events sync ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping full-season events sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            result = await sync_events(session, client, full_season=True)
            logger.info("Bzzoiro full-season events synced: %s", result)
    except Exception as exc:
        logger.error("Error in Bzzoiro full-season events sync: %s", exc, exc_info=True)
    logger.info("=== Bzzoiro full-season events sync complete ===")


async def job_sync_bzzoiro_player_stats():
    """Every 6h (offset 1h from events): sync per-match player stats — 14 days back."""
    logger.info("=== Starting Bzzoiro player stats sync ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping player stats sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            result = await sync_player_stats(session, client, days_back=14)
            logger.info("Bzzoiro player stats synced: %s", result)
    except Exception as exc:
        logger.error("Error in Bzzoiro player stats sync: %s", exc, exc_info=True)
    logger.info("=== Bzzoiro player stats sync complete ===")


async def job_sync_bzzoiro_player_stats_full_season():
    """Weekly: full-season player stats refresh to catch any gaps."""
    logger.info("=== Starting Bzzoiro full-season player stats sync ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping full-season player stats sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            result = await sync_player_stats(session, client, full_season=True)
            logger.info("Bzzoiro full-season player stats synced: %s", result)
    except Exception as exc:
        logger.error("Error in Bzzoiro full-season player stats sync: %s", exc, exc_info=True)
    logger.info("=== Bzzoiro full-season player stats sync complete ===")



async def job_sync_bzzoiro_predictions():
    """Daily at 07:00 UTC: sync Bzzoiro match predictions."""
    logger.info("=== Starting Bzzoiro predictions sync ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping predictions sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            result = await sync_predictions(session, client)
            logger.info("Bzzoiro predictions synced: %s", result)
    except Exception as exc:
        logger.error("Error in Bzzoiro predictions sync: %s", exc, exc_info=True)
    logger.info("=== Bzzoiro predictions sync complete ===")


async def job_sync_bzzoiro_odds() -> None:
    """Sync Pinnacle odds from Bzzoiro into match_odds_snapshots."""
    logger.info("job_sync_bzzoiro_odds: start")
    try:
        async with async_session() as session:
            async with BzzoiroClient(settings.bzzoiro_api_key) as client:
                count = await sync_bzzoiro_odds(session, client, bookmakers=["pinnacle"])
        logger.info("job_sync_bzzoiro_odds: %d rows upserted", count)
    except Exception as exc:
        logger.exception("job_sync_bzzoiro_odds failed: %s", exc)


async def has_imminent_kickoff(session, heures: int = 3) -> bool:
    """Vrai si au moins un match commence dans les prochaines heures."""
    from app.models.fixtures import Fixture

    maintenant = datetime.now(UTC)
    result = await session.execute(
        select(func.count())
        .select_from(Fixture)
        .where(
            Fixture.kickoff_utc > maintenant,
            Fixture.kickoff_utc <= maintenant + timedelta(hours=heures),
        )
    )
    return (result.scalar() or 0) > 0


async def job_sync_bzzoiro_events_approche() -> None:
    """Toutes les 10 min : rafraichit les evenements quand un coup d'envoi approche.

    Les compos vivent dans bzz_events.lineups, alimente par le sync des
    evenements. Bzzoiro les publie environ 1 h avant le coup d'envoi : a la
    cadence normale de 6 h, elles arrivent souvent apres le match.

    Le job ne fait rien tant qu'aucun match n'est proche, ce qui laisse la
    cadence normale seule active la plupart du temps.
    """
    if not settings.bzzoiro_api_key:
        return
    try:
        async with async_session() as session:
            proche = await has_imminent_kickoff(session)
        if not proche:
            return
        logger.info("Coup d'envoi imminent — rafraichissement des evenements")
        await job_sync_bzzoiro_events()
    except Exception as exc:
        logger.error("Echec du rafraichissement d'approche : %s", exc, exc_info=True)


async def job_sync_compos_avant_match() -> None:
    """Toutes les 5 min : suit les compos des matchs a venir.

    Bzzoiro publie une compo probable un a deux jours avant, puis l'officielle
    peu avant le coup d'envoi. La regle d'interrogation limite la veille a une
    vingtaine de requetes par match : voir doit_interroger.
    """
    if not settings.bzzoiro_api_key:
        return
    try:
        async with async_session() as session, BzzoiroClient(
            settings.bzzoiro_api_key
        ) as client:
            await sync_avant_match(session, client)
    except Exception as exc:
        logger.error("Echec du suivi des compos : %s", exc, exc_info=True)


async def job_sync_donnees_apres_match() -> None:
    """Toutes les heures : tirs, statistiques et compos des matchs termines.

    Ces donnees sont definitives : un match traite ne l'est qu'une fois. Les
    tirs et la compo forment deux files independantes — un match dont les tirs
    sont pris peut rester en attente de sa compo.

    Le rattrapage des compos porte sur 9 012 matchs et avance par lots de 200
    a l'heure, soit environ deux jours. Aucun script separe : la file se vide
    d'elle-meme, et un match sans compo chez Bzzoiro en sort definitivement.
    """
    if not settings.bzzoiro_api_key:
        return
    try:
        async with async_session() as session, BzzoiroClient(
            settings.bzzoiro_api_key
        ) as client:
            await sync_apres_match(session, client)
    except Exception as exc:
        logger.error("Echec des donnees d'apres match : %s", exc, exc_info=True)


async def job_sync_bzzoiro_lineups() -> None:
    """Sync lineups from bzz_events JSONB into TeamLineup."""
    logger.info("job_sync_bzzoiro_lineups: start")
    try:
        async with async_session() as session:
            count = await sync_bzzoiro_lineups(session)
        logger.info("job_sync_bzzoiro_lineups: %d lineups written", count)
    except Exception as exc:
        logger.exception("job_sync_bzzoiro_lineups failed: %s", exc)


# ── WC2026 Match Stats Job ───────────────────────────────────────


async def job_sync_fixture_status() -> None:
    """Every hour: sync fixture status and scores from BzzEvent."""
    try:
        from app.ingestion.bzzoiro.sync_fixture_status import sync_fixture_status_from_bzz
        async with async_session() as session:
            updated = await sync_fixture_status_from_bzz(session)
        if updated:
            logger.info("job_sync_fixture_status: %d fixtures updated", updated)
    except Exception as exc:
        logger.exception("job_sync_fixture_status failed: %s", exc)


async def job_sync_wc_match_stats() -> None:
    """Every hour: fetch player stats for finished WC2026 matches that have none yet."""
    logger.info("job_sync_wc_match_stats: start")
    try:
        from app.ingestion.wc2026.sync_wc_match_stats import sync_wc_match_stats
        async with async_session() as session:
            result = await sync_wc_match_stats(session)
        if result.synced or result.errors:
            logger.info(
                "job_sync_wc_match_stats: synced=%d errors=%d",
                result.synced, len(result.errors),
            )
            for err in result.errors:
                logger.warning("job_sync_wc_match_stats error: %s", err)
    except Exception as exc:
        logger.exception("job_sync_wc_match_stats failed: %s", exc)


async def job_sync_wc_bracket() -> None:
    """Every hour: recompute WC2026 team advancement probabilities via ELO + Monte Carlo, then reprice players."""
    logger.info("job_sync_wc_bracket: start")
    try:
        from app.pricing.wc2026_bracket import compute_wc_advancement
        from app.models.wc2026_advancement import WC2026TeamAdvancement
        async with async_session() as session:
            rows = await compute_wc_advancement(session)
            if not rows:
                logger.warning("job_sync_wc_bracket: no rows computed — skipping truncate")
                return
            await session.execute(text("TRUNCATE TABLE wc2026_team_advancement RESTART IDENTITY"))
            for row in rows:
                session.add(WC2026TeamAdvancement(**row))
            await session.commit()
        logger.info("job_sync_wc_bracket: %d nations computed", len(rows))
    except Exception as exc:
        logger.exception("job_sync_wc_bracket failed: %s", exc)
        return

    try:
        from app.pricing.wc2026_tournament import compute_tournament_pricing
        from app.models.wc2026_pricing import WC2026PlayerPricing
        async with async_session() as session:
            pricing_rows = await compute_tournament_pricing(session)
            await session.execute(text("TRUNCATE TABLE wc2026_player_pricing RESTART IDENTITY"))
            for row in pricing_rows:
                session.add(WC2026PlayerPricing(**row))
            await session.commit()
        logger.info("job_sync_wc_bracket: %d players repriced", len(pricing_rows))
    except Exception as exc:
        logger.exception("job_sync_wc_bracket: player repricing failed: %s", exc)

    # Backtest: nettoyer les prédictions placeholders, puis snapshot + résultats
    try:
        from app.api.wc2026_stats import (
            _cleanup_placeholder_predictions,
            _snapshot_ko_predictions,
            _update_ko_results,
        )
        async with async_session() as session:
            deleted = await _cleanup_placeholder_predictions(session)
            created = await _snapshot_ko_predictions(session)
            updated = await _update_ko_results(session)
        if deleted or created or updated:
            logger.info(
                "ko_predictions: %d placeholders supprimés, %d snapshots, %d résultats",
                deleted, created, updated,
            )
    except Exception as exc:
        logger.exception("ko_predictions failed: %s", exc)


# ── WC2026 Outrights Job ──────────────────────────────────────────


async def job_sync_wc_squads() -> None:
    """Sync WC2026 official squad call-ups from Bzzoiro."""
    logger.info("job_sync_wc_squads: start")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping WC squads sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            total = await sync_wc_squads(session, client)
        logger.info("job_sync_wc_squads: %d rows upserted", total)
    except Exception as exc:
        logger.exception("job_sync_wc_squads failed: %s", exc)


# ── Transfermarkt Squad Sync Job ─────────────────────────────────


def _run_sync_squads_blocking(
    clubs: list[CanonicalTeam], mode: str
) -> tuple[SquadSyncRun, dict[int, str]]:
    """Runs `sync_squads` to completion on its own event loop, inside the
    worker thread this function is dispatched to via `asyncio.to_thread`.

    `TransfermarktClient` is a SYNCHRONOUS client (`httpx.Client`, and its
    rate-limiting/retry backoff uses `time.sleep`, not `asyncio.sleep`) —
    `fetch_club_squad` is declared `async def` but never actually yields
    control while it waits on the network/rate-limit, so calling
    `sync_squads` directly on the worker's shared event loop would stall
    EVERY other scheduled job (odds ticks every 60s, the 30-min settlement
    pipeline, ...) for the full duration of the scrape — dozens of clubs at
    >= 2s/request plus retries, easily several minutes.

    `asyncio.to_thread` moves that blocking work to a separate OS thread:
    CPython releases the GIL during `time.sleep` and socket I/O, so the
    worker's main event loop keeps servicing every other job normally while
    this thread runs.

    We deliberately do NOT reuse the caller's `AsyncSession` (or its event
    loop) for the actual scrape+write: asyncpg connections are bound to the
    event loop that created them, so handing one across threads/loops would
    corrupt it. Crucially, this also rules out the module-level `async_session`
    / `engine` from `app.db` — even though we open a *fresh* session from it,
    that sessionmaker is bound to the ONE shared `engine`/connection pool used
    by the worker's main event loop for every other job (odds, settlement,
    ...). `asyncio.run(_run())` below spins up a brand-new event loop inside
    this thread; if `_run()` checked out a connection from the shared pool,
    that connection would be a `Future` created on the main loop being awaited
    from this thread's loop -> `RuntimeError: Future attached to a different
    loop`, AND it would corrupt the shared pool out from under the other jobs
    still running on the main loop.

    Instead this function creates its OWN dedicated `AsyncEngine` (own
    connection(s), own sessionmaker), using `NullPool` so no connection is
    ever pooled/reused across `asyncio.run()` calls, and disposes it before
    returning. `clubs` is only ever read here (`.id` / `.transfermarkt_club_id`
    / `.bzz_team_id`, already loaded in memory by the caller) and never
    re-attached to a session — the same read-only contract `sync_squads`
    already relies on in `test_sync_squads.py` — so passing already-loaded
    ORM instances across the thread boundary is safe.
    """

    async def _run() -> tuple[SquadSyncRun, dict[int, str]]:
        client = TransfermarktClient()
        eng = create_async_engine(DATABASE_URL, poolclass=NullPool)
        sm = async_sessionmaker(eng, expire_on_commit=False)
        try:
            async with sm() as session:
                return await sync_squads(session, client, clubs, mode=mode)
        finally:
            client.close()
            await eng.dispose()

    return asyncio.run(_run())


async def job_sync_squads() -> None:
    """Daily at 04:30 UTC: reconcile Transfermarkt squads into `bzz_players`.

    Cadence decided by `should_run_today` (Tache 7): daily during the two
    mercato windows (summer/winter), weekly otherwise (see
    `app.ingestion.transfermarkt.schedule`). `None` means "nothing to do
    today" (already ran this week, off mercato) — a no-op, not an error.
    """
    logger.info("job_sync_squads: start")
    job_started_at = datetime.now(UTC)
    mode: str | None = None
    try:
        async with async_session() as session:
            last_weekly_result = await session.execute(
                select(func.max(SquadSyncRun.started_at)).where(
                    SquadSyncRun.mode == "weekly",
                    SquadSyncRun.status.in_(["ok", "partial"]),
                )
            )
            last_weekly_started_at = last_weekly_result.scalar_one_or_none()
            last_weekly = last_weekly_started_at.date() if last_weekly_started_at else None

            mode = should_run_today(date.today(), last_weekly)
            if mode is None:
                logger.info("job_sync_squads: no-op (hors fenêtre / hebdo déjà fait)")
                return

            clubs_result = await session.execute(
                select(CanonicalTeam).where(
                    CanonicalTeam.transfermarkt_club_id.isnot(None),
                    CanonicalTeam.bzz_team_id.isnot(None),
                )
            )
            clubs = list(clubs_result.scalars().all())

        if not clubs:
            logger.warning(
                "job_sync_squads: aucun canonical_team avec transfermarkt_club_id + "
                "bzz_team_id renseignes, no-op (mode=%s)",
                mode,
            )
            return

        run, samples = await asyncio.to_thread(_run_sync_squads_blocking, clubs, mode)

        logger.info(
            "job_sync_squads: mode=%s status=%s clubs_ok=%s/%s players_updated=%s "
            "players_detached=%s",
            mode, run.status, run.clubs_ok, run.clubs_total,
            run.players_updated, run.players_detached,
        )

        if run.status in ("failed", "partial"):
            await surface_failure(run, samples, settings=settings)

    except Exception as exc:
        logger.error("job_sync_squads failed: %s", exc, exc_info=True)
        # `sync_squads` may never have run (or never reached the point where it
        # persists its own `SquadSyncRun` row) — without this, an exception here
        # would leave NEITHER a run row NOR the failure garde-fou triggered:
        # a silent death. Persist a minimal "failed" run on the MAIN loop's
        # shared `async_session` (safe here — unlike `_run_sync_squads_blocking`,
        # this coroutine runs on the worker's main event loop, the one that
        # owns `app.db.engine`) and surface it exactly like a normal failed run.
        # This block must never itself crash the scheduler: it is wrapped in
        # its own try/except, falling back to `logger.critical` as last resort.
        try:
            failed_run = SquadSyncRun(
                started_at=job_started_at,
                finished_at=datetime.now(UTC),
                mode=mode,
                status="failed",
                detail={"exception": repr(exc)},
            )
            async with async_session() as session:
                session.add(failed_run)
                await session.commit()
                await session.refresh(failed_run)
            await surface_failure(failed_run, {}, settings=settings)
            logger.critical(
                "job_sync_squads: run en exception, trace en run failed (id=%s) + garde-fou "
                "declenche.",
                failed_run.id,
            )
        except Exception:
            logger.critical(
                "job_sync_squads: echec du tracage du run failed / garde-fou suite a "
                "l'exception initiale (%s) — AUCUNE trace persistee pour cet echec.",
                exc,
                exc_info=True,
            )


async def job_deactivate_stale_wc_odds() -> None:
    """Hourly: deactivate WC outright odds not re-seen recently (dead markets)."""
    try:
        from app.ingestion.wc2026.sync_wc_outrights import deactivate_stale_outrights
        async with async_session() as session:
            count = await deactivate_stale_outrights(session)
        if count:
            logger.info("job_deactivate_stale_wc_odds: %d cotes désactivées", count)
    except Exception as exc:
        logger.exception("job_deactivate_stale_wc_odds failed: %s", exc)


async def job_capture_xg_opening() -> None:
    """Archive la premiere estimation xG publiee pour chaque match."""
    try:
        from app.services.xg_library import capture_opening

        async with async_session() as session:
            await capture_opening(session)
    except Exception as exc:
        logger.exception("job_capture_xg_opening failed: %s", exc)


async def job_capture_xg_closing() -> None:
    """Archive la derniere estimation xG avant le coup d'envoi."""
    try:
        from app.services.xg_library import capture_closing

        async with async_session() as session:
            await capture_closing(session)
    except Exception as exc:
        logger.exception("job_capture_xg_closing failed: %s", exc)


# ── PS3838 Anchor Resolution (Task 7) ────────────────────────────

# PS3838 ouvre ses lignes ~10 jours a l'avance : un match non ancre a moins de
# 7 jours est un bug, pas un cas limite. Un match non ancre est un match sans
# aucune recommandation (load_match_pricing renvoie None faute de xG).
_ANCHOR_ALERT_HORIZON = timedelta(days=7)


def _unanchored_alert_lines(rows, now: datetime) -> list[str]:
    """Libelles des matchs non ancres a moins de 7 jours. Vide = rien a signaler.

    Le libelle vient de noms d'equipe en base et part dans un message Telegram
    en HTML : un & (ou < / >) non echappe casse le parsing du message entier
    et l'alerte est perdue en silence (send_alert avale l'echec). On echappe
    donc la partie variable uniquement, jamais les balises <b> construites
    par l'appelant.
    """
    out = []
    for label, kickoff in rows:
        if kickoff is None:
            continue
        ko = kickoff if kickoff.tzinfo else kickoff.replace(tzinfo=UTC)
        delta = ko - now
        if timedelta(0) < delta < _ANCHOR_ALERT_HORIZON:
            safe_label = html.escape(label, quote=False)
            out.append(f"• {safe_label} ({ko:%d/%m %H:%M} UTC) — non ancré")
    return out


def _stale_odds_alert_lines(rows, now: datetime) -> list[str]:
    """Libelles des matchs ANCRES a moins de 7 jours mais sans cotes PS3838
    exploitables recentes (evenement disparu du flux, marche disparu, ou
    simplement jamais scrape). Vide = rien a signaler.

    La spec exige une alerte pour un match sans identifiant resolu OU sans
    cotes exploitables : cette fonction couvre la seconde cause, avec un
    libelle distinct de _unanchored_alert_lines pour que l'alerte Telegram
    ne melange pas les deux diagnostics. Meme echappement HTML, meme motif :
    voir la docstring de _unanchored_alert_lines.
    """
    out = []
    for label, kickoff in rows:
        if kickoff is None:
            continue
        ko = kickoff if kickoff.tzinfo else kickoff.replace(tzinfo=UTC)
        delta = ko - now
        if timedelta(0) < delta < _ANCHOR_ALERT_HORIZON:
            safe_label = html.escape(label, quote=False)
            out.append(f"• {safe_label} ({ko:%d/%m %H:%M} UTC) — ancré mais sans cotes")
    return out


async def job_resolve_ps3838_anchors() -> None:
    """Resout les ancrages PS3838 et signale les matchs proches restes orphelins."""
    try:
        from app.alerts import send_alert
        from app.ingestion.ps3838.anchor import resolve_anchors
        from app.ingestion.ps3838.client import fetch_events
        from app.models.fixtures import Fixture
        from app.models.match_odds import MatchOddsSnapshot
        from app.services.market_xg import MAX_SNAPSHOT_AGE, XG_BOOKMAKER

        events = await fetch_events()
        if not events:
            await send_alert(
                "🚨 <b>[Ev0] PS3838 injoignable</b>\n\n"
                "Aucun événement récupéré — le xG d'équipe ne sera plus calculé.",
                channel="incidents",
            )
            return

        async with async_session() as session:
            resolved, _ = await resolve_anchors(session, events)

            now = datetime.now(UTC)
            unanchored_rows = (await session.execute(
                select(Fixture.home_team, Fixture.away_team, Fixture.kickoff_utc).where(
                    Fixture.ps3838_event_id.is_(None),
                    Fixture.kickoff_utc > now,
                    Fixture.kickoff_utc < now + _ANCHOR_ALERT_HORIZON,
                    Fixture.status.notin_(["finished", "cancelled", "postponed"]),
                ).order_by(Fixture.kickoff_utc)
            )).all()

            # Ancrees mais sans cotes PS3838 exploitables recemment : meme
            # horizon de proximite que ci-dessus, meme fenetre de fraicheur
            # que MarketXgService (MAX_SNAPSHOT_AGE) pour rester coherent
            # avec ce qui alimente reellement le pricing. Pas de colonne
            # dediee : on interroge simplement les snapshots existants.
            anchored_near = (await session.execute(
                select(
                    Fixture.id, Fixture.home_team, Fixture.away_team, Fixture.kickoff_utc
                ).where(
                    Fixture.ps3838_event_id.isnot(None),
                    Fixture.kickoff_utc > now,
                    Fixture.kickoff_utc < now + _ANCHOR_ALERT_HORIZON,
                    Fixture.status.notin_(["finished", "cancelled", "postponed"]),
                ).order_by(Fixture.kickoff_utc)
            )).all()

            stale_rows: list[tuple[str, datetime]] = []
            if anchored_near:
                fresh_ids = set((await session.execute(
                    select(MatchOddsSnapshot.fixture_id).where(
                        MatchOddsSnapshot.fixture_id.in_([fid for fid, _, _, _ in anchored_near]),
                        MatchOddsSnapshot.bookmaker == XG_BOOKMAKER,
                        MatchOddsSnapshot.snapshot_utc >= now - MAX_SNAPSHOT_AGE,
                    ).distinct()
                )).scalars().all())
                stale_rows = [
                    (f"{h} - {a}", ko)
                    for fid, h, a, ko in anchored_near
                    if fid not in fresh_ids
                ]

        lines = _unanchored_alert_lines(
            [(f"{h} - {a}", ko) for h, a, ko in unanchored_rows], now
        ) + _stale_odds_alert_lines(stale_rows, now)
        logger.info(
            "PS3838 anchors: %d resolus, %d non ancres proches, %d ancres sans cotes proches",
            resolved, len(unanchored_rows), len(stale_rows),
        )

        if lines:
            await send_alert(
                f"🚨 <b>[Ev0] {len(lines)} match(s) sans ancrage ou sans cotes PS3838 à moins de 7j</b>\n\n"
                + "\n".join(lines[:10])
                + ("\n…" if len(lines) > 10 else "")
                + "\n\nCes matchs n'auront aucune recommandation.",
                channel="incidents",
            )
    except Exception as exc:
        logger.exception("job_resolve_ps3838_anchors failed: %s", exc)


SNAPSHOT_RETENTION_DAYS = 45
_PURGE_BATCH = 50_000


async def job_purge_old_snapshots() -> None:
    """Daily 04:30 UTC: purge les snapshots de cotes plus vieux que 45 jours.

    Sans risque : les recs réglées portent leurs cotes en dur, le pricing et
    market_xg ne lisent que le dernier snapshot, le moteur de recos a une
    fenêtre de 24h. Suppression par lots pour éviter les locks longs.
    """
    try:
        totals = {}
        for table, ts_col in (
            ("match_odds_snapshots", "created_at"),
            ("player_odds_snapshots", "scraped_at"),
        ):
            deleted = 0
            while True:
                async with async_session() as session:
                    res = await session.execute(
                        text(
                            f"DELETE FROM {table} WHERE id IN ("
                            f" SELECT id FROM {table}"
                            f" WHERE {ts_col} < now() - make_interval(days => :d)"
                            f" LIMIT :batch)"
                        ),
                        {"d": SNAPSHOT_RETENTION_DAYS, "batch": _PURGE_BATCH},
                    )
                    await session.commit()
                n = res.rowcount or 0
                deleted += n
                if n < _PURGE_BATCH:
                    break
            totals[table] = deleted
        if any(totals.values()):
            logger.info(
                "job_purge_old_snapshots: %s supprimés (> %d jours)",
                totals, SNAPSHOT_RETENTION_DAYS,
            )
    except Exception as exc:
        logger.exception("job_purge_old_snapshots failed: %s", exc)


_HEALTH_STALE_H = 24
_HEALTH_BACKLOG_MAX = 20

# Traefik renouvelle a 30 jours de l'expiration. On alerte a 21 : si le
# renouvellement automatique echoue, il reste trois semaines pour reagir avant
# que le site ne devienne inaccessible (certificat invalide = telephones bloques).
_CERT_WARN_DAYS = 21


def _cert_expiry(host: str, timeout: float = 10.0) -> datetime | None:
    """Date d'expiration du certificat TLS servi par `host`. None si illisible."""
    import socket
    import ssl

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
        return datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except Exception as exc:
        logger.warning("Certificat de %s illisible: %s", host, exc)
        return None


def _cert_status(
    domains: list[str],
    now: datetime,
    fetch=_cert_expiry,
) -> list[tuple[str, int | None]]:
    """(hote, jours restants) pour chaque domaine. `None` = lecture impossible."""
    out: list[tuple[str, int | None]] = []
    for host in domains:
        expiry = fetch(host)
        out.append((host, None if expiry is None else (expiry - now).days))
    return out


def _cert_red_flags(statuses: list[tuple[str, int | None]]) -> list[str]:
    """Certificats trop proches de l'expiration.

    Un hote injoignable ne declenche rien : on verifie tous les jours et Traefik
    renouvelle bien avant le seuil, quelques echecs de lecture sont sans gravite.
    """
    return [
        f"certificat {host} expire dans {days}j"
        for host, days in statuses
        if days is not None and days < _CERT_WARN_DAYS
    ]


def _health_red_flags(row: dict, now: datetime) -> list[str]:
    """Indicateurs de santé au rouge. Liste vide = rien à signaler."""
    stale = timedelta(hours=_HEALTH_STALE_H)
    flags: list[str] = []

    for key, label in (
        ("last_player_odds", "cotes joueurs"),
        ("last_match_odds", "cotes matchs"),
    ):
        ts = row[key]
        if ts is None or (now - ts) > stale:
            flags.append(label)

    if row["backlog_settle"] > _HEALTH_BACKLOG_MAX:
        flags.append("backlog settlement")
    if row["recs_24h"] == 0:
        flags.append("aucune reco en 24h")

    return flags


async def job_daily_health_report() -> None:
    """Daily 08:00 UTC : détecte les morts silencieuses qui ne lèvent aucune
    exception (scraper arrêté, données figées).

    Ne sonne (`incidents`) que si un indicateur est au rouge ; sinon le rapport
    tombe sur `autopilot`, consultable. Un rapport quotidien identique qu'il
    aille bien ou mal finit par ne plus être lu — donc à ne plus alerter."""
    try:
        from app.alerts import send_alert

        async with async_session() as session:
            row = (await session.execute(text("""
                SELECT
                  (SELECT max(scraped_at) FROM player_odds_snapshots)          AS last_player_odds,
                  (SELECT max(created_at) FROM match_odds_snapshots)           AS last_match_odds,
                  (SELECT count(*) FROM wc2026_outright_odds WHERE is_active)  AS wc_odds_actives,
                  (SELECT max(computed_at) FROM wc2026_player_pricing)         AS wc_pricing_at,
                  (SELECT count(*) FROM recommendations
                    WHERE created_at > now() - interval '24 hours')            AS recs_24h,
                  (SELECT count(*) FROM recommendations
                    WHERE status = 'pending')                                  AS recs_pending,
                  (SELECT count(*) FROM autopilot_decisions d
                    JOIN recommendations r ON r.id = d.recommendation_id
                    JOIN fixtures f ON f.id = r.fixture_id
                    WHERE d.result IS NULL AND f.status = 'finished')          AS backlog_settle
            """))).mappings().one()

        now = datetime.now(UTC)

        def age(ts) -> str:
            if ts is None:
                return "jamais"
            mins = int((now - ts).total_seconds() // 60)
            if mins < 60:
                return f"il y a {mins} min"
            if mins < 48 * 60:
                return f"il y a {mins // 60}h{mins % 60:02d}"
            return f"il y a {mins // 1440}j ⚠️"

        # Lecture TLS bloquante -> thread, pour ne pas figer la boucle du scheduler
        domains = [d.strip() for d in settings.cert_check_domains.split(",") if d.strip()]
        certs = await asyncio.to_thread(_cert_status, domains, now) if domains else []

        red = _health_red_flags(row, now) + _cert_red_flags(certs)
        title = (
            f"🚨 <b>[Ev0] Santé — {', '.join(red)}</b>"
            if red
            else "🩺 <b>[Ev0] Santé quotidienne</b>"
        )
        cert_lines = "".join(
            f"\nCertificat {host} : " + ("illisible ⚠️" if days is None else f"{days} j")
            for host, days in certs
        )
        msg = (
            f"{title}\n\n"
            f"Cotes joueurs : {age(row['last_player_odds'])}\n"
            f"Cotes matchs : {age(row['last_match_odds'])}\n"
            f"Outrights WC actifs : {row['wc_odds_actives']}\n"
            f"Pricing WC : {age(row['wc_pricing_at'])}\n"
            f"Recos 24h : {row['recs_24h']} (pending: {row['recs_pending']})\n"
            f"Backlog settle : {row['backlog_settle']} décision(s)"
            f"{cert_lines}"
        )
        await send_alert(msg, channel="incidents" if red else "autopilot")
    except Exception as exc:
        logger.exception("job_daily_health_report failed: %s", exc)


# ── Scheduler Setup ───────────────────────────────────────────────


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler."""
    scheduler = AsyncIOScheduler()

    # Fixtures: Every 30 min (Bzzoiro — create missing + fix placeholders).
    # Frequent interval needed during knockout stages so team names are resolved
    # shortly after each match result is known in Bzzoiro.
    scheduler.add_job(
        job_sync_fixtures,
        IntervalTrigger(minutes=30),
        id="sync_fixtures",
        name="Sync fixtures from Bzzoiro (create + placeholder resolution)",
        replace_existing=True,
    )

    # Match events: handled by settle_pipeline (every 30 min)

    # Autopilot run: Every 2 hours (after recommendations)
    scheduler.add_job(
        job_autopilot_run,
        IntervalTrigger(hours=2),
        id="autopilot_run",
        name="Autopilot: evaluate today's VALUE recs",
        replace_existing=True,
    )

    # Autopilot settle: Daily at 09:00 UTC
    scheduler.add_job(
        job_autopilot_settle,
        CronTrigger(hour=9, minute=0),
        id="autopilot_settle",
        name="Autopilot: settle paper trades from match events",
        replace_existing=True,
    )

    # Expire recommendations: Every 5 minutes
    scheduler.add_job(
        job_expire_recommendations,
        IntervalTrigger(minutes=5),
        id="expire_recommendations",
        name="Expire pending recommendations past kickoff",
        replace_existing=True,
    )

    # Settlement pipeline: every 30 min (auto-finish → Bzzoiro incidents → settle)
    scheduler.add_job(
        job_settle_pipeline,
        IntervalTrigger(minutes=30),
        id="settle_pipeline",
        name="Settlement pipeline: auto-finish + match events + settle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Autopilot re-optimize: Weekly Sunday at 03:00 UTC
    scheduler.add_job(
        job_autopilot_reoptimize,
        CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="autopilot_reoptimize",
        name="Autopilot: Optuna hyperparameter re-optimization",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Odds scheduler tick: every 60 seconds
    scheduler.add_job(
        job_odds_scheduler_tick,
        IntervalTrigger(seconds=60),
        id="job_odds_scheduler_tick",
        name="Odds scheduler: adaptive Betclic+Unibet scraping based on KO distance",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── Bzzoiro jobs ───────────────────────────────────────────────

    # Bzzoiro reference (leagues + teams): daily at 02:00 UTC
    scheduler.add_job(
        job_sync_bzzoiro_reference,
        CronTrigger(hour=2, minute=0),
        id="sync_bzzoiro_reference",
        name="Sync Bzzoiro leagues and teams reference data",
        replace_existing=True,
    )

    # Bzzoiro players: daily at 03:00 UTC
    scheduler.add_job(
        job_sync_bzzoiro_players,
        CronTrigger(hour=3, minute=0),
        id="sync_bzzoiro_players",
        name="Sync Bzzoiro player roster data",
        replace_existing=True,
    )

    # Bzzoiro season stats aggregation: daily at 04:00 UTC
    scheduler.add_job(
        job_aggregate_season_stats,
        CronTrigger(hour=4, minute=0),
        id="aggregate_season_stats",
        name="Aggregate Bzzoiro per-match stats into season totals",
        replace_existing=True,
    )

    # Loan team detection: daily at 04:30 UTC (after aggregation)
    scheduler.add_job(
        job_sync_loan_teams,
        CronTrigger(hour=4, minute=30),
        id="sync_loan_teams",
        name="Detect on-loan players from match stats and update loan_team_*",
        replace_existing=True,
    )

    # Transfermarkt squad sync: daily at 04:30 UTC (mercato: daily, season: weekly —
    # cadence itself decided at runtime by should_run_today, see job docstring)
    scheduler.add_job(
        job_sync_squads,
        CronTrigger(hour=4, minute=30),
        id="sync_squads",
        name="Sync effectifs Transfermarkt (mercato: quotidien, saison: hebdo)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Bzzoiro events: every 6 hours
    scheduler.add_job(
        job_sync_bzzoiro_events,
        IntervalTrigger(hours=6),
        id="sync_bzzoiro_events",
        name="Sync Bzzoiro match events (goals, assists)",
        replace_existing=True,
    )

    # Bzzoiro player stats: every 6 hours, offset 1h from events
    scheduler.add_job(
        job_sync_bzzoiro_player_stats,
        IntervalTrigger(hours=6, start_date="2000-01-01T01:00:00"),
        id="sync_bzzoiro_player_stats",
        name="Sync Bzzoiro per-match player stats (14 days back)",
        replace_existing=True,
    )

    # Régime d'approche : toutes les 10 min, mais ne déclenche un sync que si
    # un coup d'envoi est dans les 3 heures. C'est ce qui fait arriver les
    # compos avant le match — le job à 6 h les captait souvent après.
    scheduler.add_job(
        job_sync_bzzoiro_events_approche,
        IntervalTrigger(minutes=10),
        id="sync_bzzoiro_events_approche",
        name="Sync Bzzoiro events — régime d'approche",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Bzzoiro full-season events refresh: weekly Monday 01:00 UTC
    scheduler.add_job(
        job_sync_bzzoiro_events_full_season,
        CronTrigger(day_of_week="mon", hour=1, minute=0),
        id="sync_bzzoiro_events_full_season",
        name="Sync Bzzoiro events — full season refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Bzzoiro full-season player stats refresh: weekly Monday 02:30 UTC (after full events)
    scheduler.add_job(
        job_sync_bzzoiro_player_stats_full_season,
        CronTrigger(day_of_week="mon", hour=2, minute=30),
        id="sync_bzzoiro_player_stats_full_season",
        name="Sync Bzzoiro player stats — full season refresh",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Bzzoiro predictions: daily at 07:45 UTC
    scheduler.add_job(
        job_sync_bzzoiro_predictions,
        CronTrigger(hour=7, minute=45),
        id="sync_bzzoiro_predictions",
        name="Sync Bzzoiro match predictions",
        replace_existing=True,
    )

    # Bzzoiro odds sync — toutes les heures
    scheduler.add_job(
        job_sync_bzzoiro_odds,
        trigger=IntervalTrigger(hours=1),
        id="sync_bzzoiro_odds",
        replace_existing=True,
        max_instances=1,
    )

    # Compos des matchs a venir. 5 minutes : c'est le delai maximal entre la
    # publication de la compo officielle par Bzzoiro et sa disponibilite chez
    # nous. La regle d'interrogation evite la veille inutile entre-temps.
    scheduler.add_job(
        job_sync_compos_avant_match,
        IntervalTrigger(minutes=5),
        id="sync_compos_avant_match",
        name="Compos Bzzoiro — matchs a venir",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Donnees d'apres match — definitives, une passe horaire suffit.
    scheduler.add_job(
        job_sync_donnees_apres_match,
        IntervalTrigger(hours=1),
        id="sync_donnees_apres_match",
        name="Tirs et stats Bzzoiro — matchs termines",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Bzzoiro lineups sync — toutes les 30 min
    scheduler.add_job(
        job_sync_bzzoiro_lineups,
        trigger=IntervalTrigger(minutes=30),
        id="sync_bzzoiro_lineups",
        replace_existing=True,
        max_instances=1,
    )

    # Fixture status + scores: every hour — marks finished + populates home/away scores
    scheduler.add_job(
        job_sync_fixture_status,
        IntervalTrigger(hours=1),
        id="sync_fixture_status",
        name="Sync fixture status and scores from BzzEvent",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # WC2026 squads: daily at 05:00 UTC (squads rarely change; covers late call-ups)
    scheduler.add_job(
        job_sync_wc_squads,
        CronTrigger(hour=5, minute=0),
        id="sync_wc_squads",
        name="Sync WC2026 official squads from Bzzoiro",
        replace_existing=True,
    )

    # Rétention snapshots de cotes (04:30 UTC)
    scheduler.add_job(
        job_purge_old_snapshots,
        CronTrigger(hour=4, minute=30),
        id="purge_old_snapshots",
        name="Purge des snapshots de cotes > 45 jours",
        replace_existing=True,
    )

    # Santé quotidienne (08:00 UTC) — canal selon l'état des indicateurs
    scheduler.add_job(
        job_daily_health_report,
        CronTrigger(hour=8, minute=0),
        id="daily_health_report",
        name="Résumé santé quotidien (fraîcheur données, backlog) → alerte ops",
        replace_existing=True,
    )

    # WC2026 stale outright odds: hourly safety net — a dead odds line shown
    # as active fabricates false edges on the pricing page.
    scheduler.add_job(
        job_deactivate_stale_wc_odds,
        IntervalTrigger(hours=1),
        id="deactivate_stale_wc_odds",
        name="Deactivate WC2026 outright odds not seen for 3+ days",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # WC2026 match stats: every 5 min — picks up newly finished matches
    # automatically. Idle runs cost one DB query (early return when no new
    # finished match lacks stats); when a match finishes, stats sync then
    # advancement + player pricing recompute (classement + cotes à jour).
    scheduler.add_job(
        job_sync_wc_match_stats,
        IntervalTrigger(minutes=5),
        id="sync_wc_match_stats",
        name="Sync WC2026 player stats for finished matches",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # WC2026 bracket simulation: every hour — recomputes ELO + advancement probs
    scheduler.add_job(
        job_sync_wc_bracket,
        IntervalTrigger(hours=1),
        id="sync_wc_bracket",
        name="Recompute WC2026 bracket advancement probabilities (ELO + Monte Carlo)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Bibliotheque xG (Task 6) : archivage definitif ouverture + closing
    scheduler.add_job(
        job_capture_xg_opening,
        IntervalTrigger(hours=1),
        id="capture_xg_opening",
        name="Archive l'ouverture xG des matchs nouvellement cotes",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        job_capture_xg_closing,
        IntervalTrigger(minutes=30),
        id="capture_xg_closing",
        name="Archive le closing xG des matchs commences",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        job_resolve_ps3838_anchors,
        IntervalTrigger(hours=1),
        id="resolve_ps3838_anchors",
        name="Résout les ancrages PS3838 et signale les matchs orphelins",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    return scheduler


# ── Main Entry Point ──────────────────────────────────────────────


async def main():
    """Main worker entry point."""
    logger.info("Starting Ev0 worker...")

    # Alerte ops sur toute erreur loggée (les jobs attrapent leurs exceptions,
    # donc un listener APScheduler seul ne suffit pas). Dédup 15 min intégrée.
    from apscheduler.events import EVENT_JOB_ERROR

    from app.alerts import ErrorAlertHandler, alert_bg

    _error_handler = ErrorAlertHandler(level=logging.ERROR)
    logging.getLogger("app").addHandler(_error_handler)
    logging.getLogger("__main__").addHandler(_error_handler)

    scheduler = create_scheduler()

    def _on_job_error(event) -> None:
        alert_bg(
            f"🔴 [Ev0 worker] Job '{event.job_id}' a levé une exception:\n"
            f"{event.exception}",
            channel="incidents",
        )

    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
    scheduler.start()

    logger.info("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        logger.info("  - %s: %s", job.name, job.trigger)

    # Run initial sync on startup — events FIRST so fixture sync finds fresh BzzEvent rows
    logger.info("Running initial sync...")
    if settings.bzzoiro_api_key:
        await job_sync_bzzoiro_events()
    await job_sync_fixtures()
    from app.ingestion.bzzoiro.sync_fixture_status import sync_fixture_status_from_bzz
    async with async_session() as session:
        await sync_fixture_status_from_bzz(session)
    await job_sync_match_events()
    await job_sync_wc_bracket()
    await job_sync_wc_match_stats()
    # Ancrage PS3838 juste apres la sync des fixtures : sans ca, un demarrage
    # laisse ps3838_event_id a NULL partout jusqu'au prochain passage du job
    # planifie, donc aucune recommandation sur tout le site pendant des heures.
    await job_resolve_ps3838_anchors()

    # Keep running
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down worker...")
        scheduler.shutdown()
        await close_redis()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
