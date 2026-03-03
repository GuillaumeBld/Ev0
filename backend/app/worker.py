"""Background worker for scheduled data ingestion.

Runs periodic jobs via APScheduler:
- Fixtures sync (daily at 06:00 UTC)
- Player stats update (daily at 07:00 UTC)
- Match events sync (daily at 08:00 UTC)
- Odds snapshots (hourly for upcoming matches)
- Recommendation generation (every 2 hours)
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.cache import close_redis
from app.config import settings
from app.db import async_session, engine
from app.ingestion.fotmob_scraper import fetch_fotmob_fixtures
from app.ingestion.odds import ingest_odds_for_league, normalize_league_key
from app.ingestion.storage import (
    store_match_events,
    store_odds_snapshot,
    store_recommendation,
    upsert_fixture,
)
from app.models.settings import UserSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Defaults (used when no user settings exist)
DEFAULT_LEAGUES = ["ligue_1", "premier_league", "champions_league"]
CURRENT_SEASON = "2025-2026"


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


# ── Job 1: Fixtures Sync ─────────────────────────────────────────


async def job_sync_fixtures():
    """Sync fixtures from FotMob for all leagues and store in DB."""
    logger.info("=== Starting fixtures sync ===")

    user_settings = await _load_user_settings()
    leagues = _get_leagues(user_settings)
    logger.info("Active leagues: %s", leagues)

    for league in leagues:
        try:
            fixtures = await fetch_fotmob_fixtures(league, CURRENT_SEASON)
            logger.info("Fetched %d fixtures for %s", len(fixtures), league)

            stored = 0
            async with async_session() as session:
                for f in fixtures:
                    try:
                        await upsert_fixture(session, f)
                        stored += 1
                    except Exception as exc:
                        logger.warning("Failed to upsert fixture %s: %s", f.get("fixture_id"), exc)
                        await session.rollback()

            logger.info("Stored %d/%d fixtures for %s", stored, len(fixtures), league)

        except Exception as exc:
            logger.error("Error syncing %s fixtures: %s", league, exc, exc_info=True)

    logger.info("=== Fixtures sync complete ===")


# ── Job 2: Player Stats Sync ─────────────────────────────────────


async def job_sync_player_stats():
    """Sync player stats from Understat + FBref.

    Uses sync_all_players which fetches from both sources,
    stores per-source stats, and computes averages.
    """
    logger.info("=== Starting player stats sync ===")

    try:
        # Try the smart_sync first (Firecrawl+LLM+API-Football)
        # Falls back gracefully if API keys missing
        try:
            from app.ingestion.smart_sync import smart_sync_all

            results = await smart_sync_all()
            for r in results:
                status = "OK" if r.get("success") else "FAIL"
                logger.info(
                    "Smart sync %s: %s (strategy=%s)",
                    r.get("league"),
                    status,
                    r.get("strategy"),
                )
            logger.info("=== Player stats sync complete (smart) ===")
            return
        except Exception as exc:
            logger.warning("Smart sync failed, falling back to direct sync: %s", exc)

        # Fallback: direct FBref + Understat sync
        from app.ingestion.sync_all_players import sync_all

        await sync_all()
        logger.info("=== Player stats sync complete (direct) ===")

    except Exception as exc:
        logger.error("Error syncing player stats: %s", exc, exc_info=True)


# ── Job 2b: Match Events Sync ─────────────────────────────────────


async def job_sync_match_events():
    """Sync match events (goals, assists) for finished fixtures.

    Primary source: FotMob /api/matchDetails.
    Fallback source: ESPN public API (site.api.espn.com) — used when FotMob
    returns 403 or any other error.

    Fetches individual goal/assist events for finished fixtures that
    don't yet have match events stored. This provides ground truth
    data for backtesting.
    """
    logger.info("=== Starting match events sync ===")

    from app.ingestion.espn_client import ESPNClient
    from app.ingestion.fotmob_scraper import fetch_match_events
    from app.models.fixtures import Fixture
    from app.models.match_events import MatchEvent

    try:
        async with async_session() as session:
            # Find finished fixtures that have no match events yet
            fixtures_with_events = (
                select(MatchEvent.fixture_id).distinct().subquery()
            )
            result = await session.execute(
                select(Fixture)
                .where(Fixture.status == "finished")
                .where(Fixture.external_id.like("fotmob_%"))
                .where(Fixture.id.notin_(select(fixtures_with_events.c.fixture_id)))
                .order_by(Fixture.kickoff_utc.desc())
                .limit(50)  # Process at most 50 per run to avoid rate limits
            )
            fixtures = list(result.scalars().all())

            if not fixtures:
                logger.info("No finished fixtures missing match events")
                logger.info("=== Match events sync complete ===")
                return

            logger.info("Found %d finished fixtures without match events", len(fixtures))

            import httpx

            synced = 0
            fotmob_ok = 0
            espn_ok = 0
            espn_client = None  # lazily initialised below

            async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as http:
                espn_client = ESPNClient(http)

                for fixture in fixtures:
                    # Extract FotMob match ID from external_id
                    match_id_str = fixture.external_id.removeprefix("fotmob_")
                    try:
                        match_id = int(match_id_str)
                    except (ValueError, TypeError):
                        logger.debug("Skipping non-numeric FotMob ID: %s", fixture.external_id)
                        continue

                    events: list[dict] = []
                    source = "none"

                    # ── Primary: FotMob ──
                    try:
                        events = await fetch_match_events(match_id)
                        if events:
                            source = "fotmob"
                            fotmob_ok += 1
                    except Exception as exc:
                        logger.debug(
                            "FotMob failed for fixture %s: %s — trying ESPN fallback",
                            fixture.external_id, exc,
                        )

                    # ── Fallback: ESPN ──
                    if not events:
                        try:
                            kickoff_date = fixture.kickoff_utc.strftime("%Y-%m-%d")
                            events = await espn_client.get_match_events(
                                fixture.league,
                                fixture.home_team,
                                fixture.away_team,
                                kickoff_date,
                            )
                            if events:
                                source = "espn"
                                espn_ok += 1
                            else:
                                logger.debug(
                                    "ESPN returned 0 events for %s vs %s on %s",
                                    fixture.home_team, fixture.away_team, kickoff_date,
                                )
                        except Exception as exc:
                            logger.warning(
                                "ESPN fallback failed for fixture %s: %s",
                                fixture.external_id, exc,
                            )

                    if events:
                        try:
                            stored = await store_match_events(session, fixture.id, events)
                            if stored > 0:
                                synced += 1
                                logger.info(
                                    "Stored %d events for %s vs %s (source=%s)",
                                    stored, fixture.home_team, fixture.away_team, source,
                                )
                        except Exception as exc:
                            logger.warning(
                                "Failed to store events for fixture %s: %s",
                                fixture.external_id, exc,
                            )
                    else:
                        logger.debug(
                            "No events from any source for %s vs %s (%s)",
                            fixture.home_team, fixture.away_team, fixture.external_id,
                        )

                    # Rate-limit: 1 req/sec
                    await asyncio.sleep(1.0)

            logger.info(
                "Synced match events for %d/%d fixtures (fotmob=%d, espn=%d)",
                synced, len(fixtures), fotmob_ok, espn_ok,
            )

    except Exception as exc:
        logger.error("Error syncing match events: %s", exc, exc_info=True)

    logger.info("=== Match events sync complete ===")


# ── Job 3: Odds Snapshot ─────────────────────────────────────────


async def job_snapshot_odds():
    """Snapshot odds from The Odds API for upcoming matches and store in DB.

    Flow:
    1. Fetch odds + raw events from The Odds API
    2. Load upcoming DB fixtures
    3. Match events → fixtures by team names (fixture_matcher)
    4. Persist odds_api_event_id on matched fixtures (cached for next run)
    5. Store odds snapshots against matched DB fixture.id
    """
    logger.info("=== Starting odds snapshot ===")

    if not settings.odds_api_key:
        logger.warning("ODDS_API_KEY not configured, skipping odds snapshot")
        return

    user_settings = await _load_user_settings()
    leagues = _get_leagues(user_settings)
    logger.info("Active leagues for odds: %s", leagues)

    from app.ingestion.fixture_matcher import match_odds_event_to_fixture
    from app.models.fixtures import Fixture

    for league in leagues:
        for market in ["goalscorer", "assist"]:
            try:
                snapshots, events = await ingest_odds_for_league(league, market)
                logger.info(
                    "Got %d odds from %d events for %s %s",
                    len(snapshots), len(events), league, market,
                )

                if not events:
                    continue

                stored = 0
                matched_events = 0

                async with async_session() as session:
                    # Load upcoming fixtures from DB
                    result = await session.execute(
                        select(Fixture).where(Fixture.league == league)
                    )
                    db_fixtures = list(result.scalars().all())

                    # Build event_id → snapshots index
                    snaps_by_event: dict[str, list] = {}
                    for snap in snapshots:
                        snaps_by_event.setdefault(snap.fixture_id, []).append(snap)

                    for event in events:
                        event_id = event.get("id", "")
                        if not event_id:
                            continue

                        fixture = match_odds_event_to_fixture(event, db_fixtures)
                        if not fixture:
                            continue

                        matched_events += 1

                        # Cache the Odds API event ID on the fixture
                        if not fixture.odds_api_event_id:
                            fixture.odds_api_event_id = event_id
                            session.add(fixture)
                            await session.flush()

                        # Store all snapshots for this event
                        event_snaps = snaps_by_event.get(event_id, [])
                        for snap in event_snaps:
                            try:
                                await store_odds_snapshot(
                                    session,
                                    fixture_id=fixture.id,
                                    player_name=snap.player_name,
                                    market_type=snap.market_type,
                                    bookmaker=snap.bookmaker,
                                    odds=snap.odds,
                                    raw_data=snap.raw_data,
                                )
                                stored += 1
                            except Exception as exc:
                                logger.debug("Odds upsert skip (likely duplicate): %s", exc)
                                await session.rollback()

                    await session.commit()

                logger.info(
                    "Matched %d/%d events, stored %d/%d odds for %s %s",
                    matched_events, len(events), stored, len(snapshots), league, market,
                )

            except Exception as exc:
                logger.error(
                    "Error snapshotting %s %s odds: %s", league, market, exc, exc_info=True
                )

    logger.info("=== Odds snapshot complete ===")


# ── Job 3b: Direct Odds Snapshot (French bookmakers) ─────────────


async def job_snapshot_direct_odds():
    """Snapshot odds from Kambi (Unibet) HTTP API + Playwright scrapers.

    Flow:
    1. Kambi HTTP API (Unibet) — no browser needed, always runs first
    2. Playwright scrapers (Betclic, Unibet page, ParionsSport) — best-effort
    3. Match each MatchOdds → DB fixture by team name + date window
    4. Persist selections via store_odds_snapshot()
    """
    logger.info("=== Starting direct odds snapshot ===")

    from app.ingestion.fixture_matcher import match_odds_event_to_fixture
    from app.models.fixtures import Fixture

    user_settings = await _load_user_settings()
    leagues = _get_leagues(user_settings)
    logger.info("Direct scrapers: leagues = %s", leagues)

    all_match_odds = []

    # ── 1. Kambi HTTP scraper (Unibet — pure HTTP, no Playwright needed) ──
    try:
        from app.ingestion.kambi_scraper import scrape_all_kambi

        kambi_results = await scrape_all_kambi(leagues)
        all_match_odds.extend(kambi_results)
        logger.info("Kambi scraper: %d match-odds objects", len(kambi_results))
    except Exception as exc:
        logger.error("Kambi scrape failed: %s", exc, exc_info=True)

    # ── 2. Betclic HTTP scraper (SSR HTML — no Playwright needed) ──
    try:
        from app.ingestion.betclic_scraper import scrape_all_betclic

        betclic_results = await scrape_all_betclic(leagues)
        all_match_odds.extend(betclic_results)
        logger.info("Betclic scraper: %d match-odds objects", len(betclic_results))
    except Exception as exc:
        logger.error("Betclic scrape failed: %s", exc, exc_info=True)

    # ── 3. Playwright scrapers (Unibet page, ParionsSport) ──
    try:
        from playwright.async_api import async_playwright

        from app.ingestion.direct_scrapers import scrape_all_direct

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                pw_results = await scrape_all_direct(leagues, browser)
                all_match_odds.extend(pw_results)
                logger.info("Playwright scrapers: %d match-odds objects", len(pw_results))
            finally:
                await browser.close()
    except ImportError:
        logger.warning("Playwright not installed — skipping Playwright scrapers")
    except Exception as exc:
        logger.error("Playwright scrape failed: %s", exc, exc_info=True)

    if not all_match_odds:
        logger.warning("All scrapers returned 0 match-odds objects")
        logger.info("=== Direct odds snapshot complete (nothing stored) ===")
        return

    logger.info("Direct scrapers total: %d match-odds objects", len(all_match_odds))

    # ── Match → fixtures + store ──
    stored = 0
    matched = 0

    async with async_session() as session:
        result = await session.execute(select(Fixture).where(Fixture.league.in_(leagues)))
        db_fixtures = list(result.scalars().all())

        for mo in all_match_odds:
            # Adapt MatchOdds to the dict shape that fixture_matcher expects
            event_dict = {
                "id": "",
                "home_team": mo.home_team,
                "away_team": mo.away_team,
                "commence_time": mo.kickoff_utc.isoformat() if mo.kickoff_utc else "",
            }
            league_fixtures = [f for f in db_fixtures if f.league == mo.league]
            fixture = match_odds_event_to_fixture(event_dict, league_fixtures)

            if not fixture:
                logger.debug(
                    "No fixture match for %s vs %s (%s)", mo.home_team, mo.away_team, mo.league
                )
                continue

            matched += 1

            for sel in mo.selections:
                try:
                    await store_odds_snapshot(
                        session,
                        fixture_id=fixture.id,
                        player_name=sel.player_name,
                        market_type=sel.market_type,
                        bookmaker=sel.bookmaker,
                        odds=sel.odds,
                        raw_data=sel.raw_data,
                    )
                    stored += 1
                except Exception as exc:
                    logger.debug("Direct odds upsert skip (likely duplicate): %s", exc)
                    await session.rollback()

        await session.commit()

    logger.info(
        "Direct odds: matched %d/%d fixtures, stored %d selections",
        matched,
        len(all_match_odds),
        stored,
    )
    logger.info("=== Direct odds snapshot complete ===")


# ── Job 3b: Weekly Player Stats Refresh ──────────────────────────


async def job_refresh_player_stats():
    """Weekly: refresh Understat player stats for the current season.

    Keeps xG/xA rates current as the season progresses.  Upserts all
    player stats so stale priors don't pollute recommendations.
    """
    logger.info("=== Starting weekly player stats refresh ===")
    try:
        from app.scripts.backfill import backfill_stats

        current_season = "2025-2026"
        n = await backfill_stats(leagues=["ligue_1", "premier_league", "champions_league"], season=current_season)
        logger.info("Stats refreshed: %d player records updated (%s)", n, current_season)
    except Exception as exc:
        logger.error("Error refreshing player stats: %s", exc, exc_info=True)
    logger.info("=== Player stats refresh complete ===")


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
            "Recommendations will use direct/Kambi odds from the DB."
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
                from app.models.fixtures import Fixture
                from app.models.recommendations import Recommendation

                result = await session.execute(select(Fixture))
                fixtures_by_ext = {f.external_id: f for f in result.scalars().all()}

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
                        )
                        existing_keys.add((fixture_id, player_name, market_type))
                        stored += 1

                        # Notify on new VALUE bets
                        if rec.get("classification") == "VALUE":
                            from app.notifications import send_telegram_alert
                            player = rec.get("player_name", "?")
                            fixture_name = rec.get("fixture_name", fixture_ext_id)
                            odds = rec.get("market_odds", "?")
                            edge = rec.get("edge", 0)
                            book = rec.get("best_bookmaker", "?")
                            msg = (
                                f"🎯 <b>VALUE BET</b>\n"
                                f"{player} — {rec.get('market_type', 'goalscorer')}\n"
                                f"📋 {fixture_name}\n"
                                f"📈 Odds: {odds} ({book}) | Edge: +{edge:.1%}\n"
                                f"🤖 Ev0 Autopilot"
                            )
                            await send_telegram_alert(msg)
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
        from app.autopilot.features import extract_features
        from app.autopilot.trainer import _compute_stake, _load_agent, weights_exist

        if not weights_exist():
            logger.warning("Autopilot weights not found — skipping run (train first)")
            return

        agent = _load_agent()
        mode = user_settings.get("autopilot_mode", "paper")

        from app.models.autopilot import AutopilotDecision
        from app.models.fixtures import Fixture
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

            decisions_made = 0
            for rec in recs:
                rec_dict = {
                    "edge": rec.edge,
                    "confidence": rec.confidence,
                    "best_odds": rec.best_odds,
                    "fair_odds": rec.fair_odds,
                    "fair_probability": rec.fair_probability,
                    "lambda_intensity": rec.lambda_intensity,
                    "market_type": rec.market_type,
                    "explanation": rec.explanation or {},
                }

                import json as _json
                features = extract_features(rec_dict)
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

            await session.commit()
            logger.info(
                "Autopilot run: evaluated %d recs, approved %d bets (mode=%s)",
                len(recs), decisions_made, mode,
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
            for decision, rec, fixture in rows:
                if not rec or not fixture:
                    continue

                # Check match events for outcome
                ev_result = await session.execute(
                    select(MatchEvent).where(
                        MatchEvent.fixture_id == fixture.id,
                        MatchEvent.player_name == rec.player_name,
                        MatchEvent.event_type == rec.market_type,
                    )
                )
                events = ev_result.scalars().all()
                won = len(events) > 0

                result_str = "won" if won else "lost"
                stake = decision.stake or 10.0
                pnl = round(
                    stake * (decision.best_odds - 1) if won else -stake, 2
                )

                # Approx bankroll at decision time for reward scaling
                reward = pnl / max(1000.0, 1.0)

                decision.result = result_str
                decision.pnl = pnl
                decision.reward = reward
                decision.settled_at = datetime.now(UTC)

                # Also update linked recommendation
                if rec.result is None:
                    rec.result = result_str
                    rec.pnl = pnl
                    rec.settled_utc = datetime.now(UTC)

                settled_count += 1

            await session.commit()
            logger.info("Autopilot settle: settled %d decisions", settled_count)

            # Fine-tune if enough new data
            if settled_count >= 10:
                logger.info("Triggering fine_tune_from_db (settled=%d)", settled_count)
                ft_result = await fine_tune_from_db(session)
                logger.info("Fine-tune complete: %s", ft_result)

    except Exception as exc:
        logger.error("Error in autopilot settle: %s", exc, exc_info=True)

    logger.info("=== Autopilot settle complete ===")


# ── Scheduler Setup ───────────────────────────────────────────────


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler."""
    scheduler = AsyncIOScheduler()

    # Fixtures: Daily at 06:00 UTC
    scheduler.add_job(
        job_sync_fixtures,
        CronTrigger(hour=6, minute=0),
        id="sync_fixtures",
        name="Sync fixtures from FotMob",
        replace_existing=True,
    )

    # Player stats: Daily at 07:00 UTC
    scheduler.add_job(
        job_sync_player_stats,
        CronTrigger(hour=7, minute=0),
        id="sync_player_stats",
        name="Sync player stats from FBref + Understat",
        replace_existing=True,
    )

    # Match events: Daily at 08:00 UTC (after fixtures sync)
    scheduler.add_job(
        job_sync_match_events,
        CronTrigger(hour=8, minute=0),
        id="sync_match_events",
        name="Sync match events from FotMob",
        replace_existing=True,
    )

    # Odds: Every hour
    scheduler.add_job(
        job_snapshot_odds,
        IntervalTrigger(hours=1),
        id="snapshot_odds",
        name="Snapshot odds from bookmakers",
        replace_existing=True,
    )

    # Direct odds (Kambi/Unibet HTTP + Playwright scrapers): Every 3 hours
    scheduler.add_job(
        job_snapshot_direct_odds,
        IntervalTrigger(hours=3),
        id="snapshot_direct_odds",
        name="Snapshot direct odds (Kambi, Betclic, ParionsSport)",
        replace_existing=True,
    )

    # Weekly player stats refresh: every Monday at 06:00 UTC
    scheduler.add_job(
        job_refresh_player_stats,
        CronTrigger(day_of_week="mon", hour=6, minute=0),
        id="refresh_player_stats",
        name="Refresh Understat player stats (current season)",
        replace_existing=True,
    )

    # Recommendations: Every 2 hours
    scheduler.add_job(
        job_generate_recommendations,
        IntervalTrigger(hours=2),
        id="generate_recommendations",
        name="Generate betting recommendations",
        replace_existing=True,
    )

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

    return scheduler


# ── Main Entry Point ──────────────────────────────────────────────


async def main():
    """Main worker entry point."""
    logger.info("Starting Ev0 worker...")

    scheduler = create_scheduler()
    scheduler.start()

    logger.info("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        logger.info("  - %s: %s", job.name, job.trigger)

    # Run initial sync on startup
    logger.info("Running initial sync...")
    await job_sync_fixtures()
    await job_sync_match_events()
    await job_snapshot_odds()
    await job_snapshot_direct_odds()

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
