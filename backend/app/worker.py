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
from app.ingestion.auto_settle import settle_approved_recommendations
from app.ingestion.bzzoiro.aggregate import aggregate_all_leagues
from app.ingestion.bzzoiro.client import BzzoiroClient
from app.ingestion.bzzoiro.sync_events import sync_events
from app.ingestion.bzzoiro.sync_player_stats import sync_player_stats
from app.ingestion.bzzoiro.sync_players import sync_players
from app.ingestion.bzzoiro.sync_predictions import sync_predictions
from app.ingestion.bzzoiro.sync_reference import sync_leagues, sync_teams
from app.ingestion.fixture_matcher import match_event_to_fixture_by_teams
from app.ingestion.match_odds import ingest_match_odds_for_league
from app.ingestion.odds import QuotaExhaustedError, fetch_events_for_league, ingest_odds_for_league, normalize_league_key
from app.ingestion.storage import (
    store_match_events,
    store_odds_snapshot,
    store_recommendation,
)
from app.models.match_odds import MatchOddsSnapshot
from app.models.settings import UserSettings
from app.services.market_scrape_scheduler import MarketScrapeScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Defaults (used when no user settings exist)
DEFAULT_LEAGUES = ["ligue_1", "premier_league", "bundesliga", "la_liga", "serie_a", "champions_league"]
CURRENT_SEASON = "2025-2026"

# Module-level scheduler instance for the OddsPortal market scrape tick
_market_scheduler = MarketScrapeScheduler()


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
    """Sync fixture kickoff_utc from The Odds API.

    Fetches upcoming events per league and updates kickoff_utc where it
    differs from the DB value. Matches by team names only (no date window)
    to handle placeholder kickoffs.
    """
    logger.info("=== Starting fixture sync ===")
    user_settings = await _load_user_settings()
    leagues = _get_leagues(user_settings)

    total_updated = 0

    for league in leagues:
        try:
            events = await fetch_events_for_league(league)
            if not events:
                logger.info("job_sync_fixtures: no events for %s", league)
                continue

            async with async_session() as session:
                from app.models.fixtures import Fixture
                result = await session.execute(
                    select(Fixture).where(
                        Fixture.league == league,
                        Fixture.status != "finished",
                    )
                )
                db_fixtures = list(result.scalars().all())

                updated = 0
                for event in events:
                    fixture = match_event_to_fixture_by_teams(event, db_fixtures)
                    if not fixture:
                        continue

                    api_kickoff_raw = event.get("commence_time", "")
                    if not api_kickoff_raw:
                        continue
                    try:
                        api_kickoff = datetime.fromisoformat(
                            api_kickoff_raw.replace("Z", "+00:00")
                        )
                    except (ValueError, TypeError):
                        continue

                    if fixture.kickoff_utc != api_kickoff:
                        fixture.kickoff_utc = api_kickoff
                        session.add(fixture)
                        updated += 1

                await session.commit()
                logger.info(
                    "job_sync_fixtures: %d kickoffs updated for %s",
                    updated, league,
                )
                total_updated += updated

        except Exception as exc:
            logger.error(
                "job_sync_fixtures: error on %s: %s", league, exc, exc_info=True
            )

    logger.info("=== Fixture sync complete: %d total kickoffs updated ===", total_updated)


# ── Job 2: Player Stats Sync ─────────────────────────────────────


async def job_sync_player_stats():
    """Sync player stats from Understat + Sofascore (Model C).

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

        # Fallback: direct Understat + FotMob sync
        from app.ingestion.sync_all_players import sync_all

        await sync_all()
        logger.info("=== Player stats sync complete (direct) ===")

    except Exception as exc:
        logger.error("Error syncing player stats: %s", exc, exc_info=True)


# ── Job 2b: Sofascore Stats Sync ─────────────────────────────────


async def job_sync_sofascore_stats():
    """Daily at 07:15 UTC: sync player stats from Sofascore API.

    Fetches BCC, accurate crosses, through balls, SOT, TAP per player.
    Merges with existing Understat data (source=average) via normalized name.
    Updates player_stats rows with the new Sofascore fields.

    Note: Sofascore may return 403 from VPS (Cloudflare block). In that case
    this job completes with 0 updates — data must be imported manually.
    """
    logger.info("=== Starting Sofascore stats sync ===")

    try:
        from datetime import UTC, date

        from app.ingestion.sofascore_scraper import LEAGUES, fetch_league_players
        from app.ingestion.player_stats import normalize_player_name
        from app.models.players import Player, PlayerStats

        today = date.today()
        season = f"{today.year - 1}/{today.year}" if today.month < 7 else f"{today.year}/{today.year + 1}"
        as_of = datetime.now(UTC)

        user_settings = await _load_user_settings()
        active_leagues = _get_leagues(user_settings)

        total_updated = 0

        for league_key in active_leagues:
            cfg = LEAGUES.get(league_key)
            if not cfg:
                continue

            try:
                ss_players = await fetch_league_players(cfg["tournament_id"], cfg["season_id"])
                logger.info(
                    "Sofascore: fetched %d players for %s",
                    len(ss_players), league_key,
                )
            except Exception as exc:
                logger.warning("Sofascore fetch failed for %s (blocked?): %s", league_key, exc)
                continue

            # Build lookup by normalized name
            ss_lookup = {normalize_player_name(p.name): p for p in ss_players}

            async with async_session() as session:
                # Load all players in the DB for this league
                result = await session.execute(
                    select(Player).where(Player.league == league_key)
                )
                db_players = result.scalars().all()

                updated = 0
                for player in db_players:
                    norm = normalize_player_name(player.name)
                    ss = ss_lookup.get(norm)
                    if not ss:
                        continue

                    # Find latest average snapshot for this player
                    stats_result = await session.execute(
                        select(PlayerStats)
                        .where(
                            PlayerStats.player_id == player.id,
                            PlayerStats.source == "average",
                            PlayerStats.season == CURRENT_SEASON,
                        )
                        .order_by(PlayerStats.as_of_utc.desc())
                        .limit(1)
                    )
                    stat = stats_result.scalar_one_or_none()

                    if stat is None:
                        continue

                    # Patch Sofascore fields
                    stat.shots_on_target = ss.shots_on_target
                    stat.touches_attack_pen_area = ss.touches_attack_pen_area
                    stat.big_chances_created = ss.big_chances_created
                    stat.accurate_crosses = ss.accurate_crosses
                    stat.total_crosses = ss.total_crosses
                    stat.through_balls = ss.through_balls
                    stat.key_passes = ss.key_passes
                    stat.sofascore_rating = ss.rating or None
                    stat.sofascore_rating = ss.rating if ss.rating else None

                    # Recompute per-90s
                    stat.compute_per_90s()
                    stat.as_of_utc = as_of

                    updated += 1

                await session.commit()
                logger.info("Sofascore: updated %d players for %s", updated, league_key)
                total_updated += updated

        logger.info("=== Sofascore stats sync complete — %d players updated ===", total_updated)

    except Exception as exc:
        logger.error("Error in Sofascore stats sync: %s", exc, exc_info=True)


# ── Job 2c: FPL / Opta Stats Sync ────────────────────────────────


async def job_sync_fpl_stats():
    """Daily at 07:30 UTC: sync Opta/FPL player stats for all PL players.

    Uses the free FPL API (fantasy.premierleague.com) which is powered by
    Opta — the same data provider used by Premier League official stats.
    Upserts PlayerStats rows with source="fpl" for every matched PL player.
    """
    logger.info("=== Starting FPL stats sync ===")

    try:
        from datetime import date

        from app.ingestion.fpl_client import FPLClient, _normalize as _fpl_normalize
        from app.models.players import Player, PlayerStats

        fpl = FPLClient()
        fpl_players = await fpl.get_all_players()
        logger.info("FPL: fetched %d players", len(fpl_players))

        # Build lookup: normalized_name → FPL player dict
        fpl_lookup: dict[str, dict] = {p["normalized_name"]: p for p in fpl_players}

        today = date.today()
        season = f"{today.year - 1}/{today.year}" if today.month < 7 else f"{today.year}/{today.year + 1}"
        as_of = datetime.now(UTC)

        async with async_session() as session:
            # Load all players in our DB
            result = await session.execute(select(Player))
            players = result.scalars().all()

            matched = 0
            upserted = 0
            for player in players:
                norm = _fpl_normalize(player.name)
                fpl_p = fpl_lookup.get(norm)
                if not fpl_p:
                    continue
                matched += 1

                minutes = fpl_p["minutes"] or 0
                xg = fpl_p["xg"]
                xa = fpl_p["xa"]

                # Check for existing snapshot today
                existing = await session.execute(
                    select(PlayerStats).where(
                        PlayerStats.player_id == player.id,
                        PlayerStats.source == "fpl",
                        PlayerStats.season == season,
                    )
                )
                stat = existing.scalars().first()

                if stat is None:
                    stat = PlayerStats(
                        player_id=player.id,
                        as_of_utc=as_of,
                        league="premier_league",
                        season=season,
                        source="fpl",
                    )
                    session.add(stat)

                stat.as_of_utc = as_of
                stat.matches_played = max(1, round(minutes / 80)) if minutes > 0 else 0  # estimated from minutes (FPL has no appearances field)
                stat.minutes_played = minutes
                stat.goals = fpl_p["goals"]
                stat.assists = fpl_p["assists"]
                stat.xg = xg
                stat.xa = xa
                stat.xg_per_90 = fpl_p["xg_per_90"]
                stat.xa_per_90 = fpl_p["xa_per_90"]
                # Store FPL-specific fields: form in npxg, ict_index in npxg_per_90
                stat.npxg = fpl_p["form"]
                stat.npxg_per_90 = fpl_p["ict_index"]
                upserted += 1

            await session.commit()
            logger.info(
                "FPL sync complete: %d/%d players matched, %d stats upserted",
                matched, len(players), upserted,
            )

    except Exception as exc:
        logger.error("Error syncing FPL stats: %s", exc, exc_info=True)

    logger.info("=== FPL stats sync complete ===")


# ── Job 2b: Match Events Sync ─────────────────────────────────────


async def job_sync_match_events():
    """Sync match events (goals, assists) for finished fixtures via ESPN.

    ESPN covers all supported leagues: Ligue 1, PL, Bundesliga, La Liga, Serie A, CL.
    One HTTP call per fixture (scoreboard + summary).
    """
    logger.info("=== Starting match events sync ===")

    from datetime import timedelta as _td

    from app.ingestion.espn_client import ESPNClient, ESPN_LEAGUE_SLUGS
    from app.models.fixtures import Fixture
    from app.models.match_events import MatchEvent
    from app.notifications import send_telegram_alert

    import httpx as _httpx

    try:
        async with async_session() as session:
            fixtures_with_events = select(MatchEvent.fixture_id).distinct().subquery()
            result = await session.execute(
                select(Fixture)
                .where(Fixture.status == "finished")
                .where(Fixture.id.notin_(select(fixtures_with_events.c.fixture_id)))
                .where(Fixture.league.in_(ESPN_LEAGUE_SLUGS.keys()))
                .order_by(Fixture.kickoff_utc.desc())
                .limit(100)
            )
            fixtures = list(result.scalars().all())

            if not fixtures:
                logger.info("No finished fixtures missing match events")
                logger.info("=== Match events sync complete ===")
                return

            logger.info("Found %d finished fixtures without match events", len(fixtures))

            synced = 0
            async with _httpx.AsyncClient(follow_redirects=True, timeout=20.0) as http:
                espn_client = ESPNClient(http)

                for fixture in fixtures:
                    kickoff_date = fixture.kickoff_utc.strftime("%Y-%m-%d")
                    try:
                        events = await espn_client.get_match_events(
                            fixture.league,
                            fixture.home_team,
                            fixture.away_team,
                            kickoff_date,
                        )
                        if events is None:
                            # Match not found on ESPN
                            logger.debug(
                                "ESPN: match not found for %s vs %s on %s",
                                fixture.home_team, fixture.away_team, kickoff_date,
                            )
                        elif events:
                            stored = await store_match_events(session, fixture.id, events)
                            if stored > 0:
                                synced += 1
                                logger.info(
                                    "Stored %d events for %s vs %s (source=espn)",
                                    stored, fixture.home_team, fixture.away_team,
                                )
                        else:
                            # Match found, 0 scoring events (e.g. 0-0) — store sentinel
                            await store_match_events(session, fixture.id, [
                                {"player_name": "__processed__", "event_type": "match_processed", "minute": None}
                            ])
                            synced += 1
                            logger.info(
                                "ESPN: 0 goals for %s vs %s — sentinel stored",
                                fixture.home_team, fixture.away_team,
                            )
                    except Exception as exc:
                        logger.warning("ESPN failed for fixture %s: %s", fixture.id, exc)

                    await asyncio.sleep(1.0)

            logger.info("Synced match events for %d/%d fixtures (source=espn)", synced, len(fixtures))

            # ── Alert: fixtures still missing events >24h after finishing ─
            now = datetime.now(UTC)
            fixtures_with_events2 = select(MatchEvent.fixture_id).distinct().subquery()
            result2 = await session.execute(
                select(Fixture)
                .where(Fixture.status == "finished")
                .where(Fixture.id.notin_(select(fixtures_with_events2.c.fixture_id)))
                .where(Fixture.kickoff_utc < now - _td(hours=24))
            )
            still_missing = list(result2.scalars().all())

            if still_missing:
                names = ", ".join(
                    f"{fx.home_team} vs {fx.away_team} ({fx.kickoff_utc.strftime('%Y-%m-%d')})"
                    for fx in still_missing[:5]
                )
                await send_telegram_alert(
                    f"⚠️ <b>[Ev0] Match events manquants</b>\n\n"
                    f"{len(still_missing)} match(s) terminé(s) depuis >24h sans événements :\n"
                    f"{names}"
                    + (" ..." if len(still_missing) > 5 else "")
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

    # ── Match-level odds (h2h / totals / btts) ──────────────────────────────
    logger.info("--- Starting match-level odds ingestion ---")
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.fixtures import Fixture

    for league in leagues:
        try:
            async with async_session() as session:
                match_rows, match_errors = await ingest_match_odds_for_league(
                    league, session, api_key=settings.odds_api_key
                )

                if match_errors:
                    logger.warning(
                        "Match odds errors for %s: %d error(s)", league, len(match_errors)
                    )

                if not match_rows:
                    logger.info("No match odds rows for %s", league)
                    continue

                # Build event_id → fixture_id mapping via odds_api_event_id
                event_ids = list({r.event_id for r in match_rows})
                result = await session.execute(
                    select(Fixture).where(
                        Fixture.odds_api_event_id.in_(event_ids)
                    )
                )
                seen: dict[str, int] = {}
                for f in result.scalars().all():
                    if not f.odds_api_event_id:
                        continue
                    if f.odds_api_event_id in seen:
                        logger.warning(
                            "Duplicate odds_api_event_id %s (fixtures %d and %d) — skipping both",
                            f.odds_api_event_id, seen[f.odds_api_event_id], f.id,
                        )
                        seen[f.odds_api_event_id] = -1  # sentinel: skip
                    else:
                        seen[f.odds_api_event_id] = f.id
                fixtures_by_event_id = {k: v for k, v in seen.items() if v != -1}

                # Build insert values, skipping rows without a matched fixture
                insert_values = []
                skipped = 0
                for row in match_rows:
                    fixture_id = fixtures_by_event_id.get(row.event_id)
                    if fixture_id is None:
                        skipped += 1
                        continue
                    insert_values.append({
                        "fixture_id": fixture_id,
                        "bookmaker": row.bookmaker,
                        "market_type": row.market_type,
                        "outcome": row.outcome,
                        "odds": row.odds,
                        "snapshot_utc": row.snapshot_utc,
                    })

                if not insert_values:
                    logger.info(
                        "No match odds rows could be matched to fixtures for %s "
                        "(%d skipped, %d total)",
                        league, skipped, len(match_rows),
                    )
                    continue

                stmt = pg_insert(MatchOddsSnapshot).values(insert_values)
                stmt = stmt.on_conflict_do_nothing(constraint="uq_match_odds_snapshot")
                await session.execute(stmt)
                await session.commit()

                logger.info(
                    "Match odds for %s: inserted %d rows (%d skipped, %d errors)",
                    league, len(insert_values), skipped, len(match_errors),
                )

        except QuotaExhaustedError:
            logger.warning(
                "Odds API quota exhausted while ingesting match odds for %s — stopping league loop",
                league,
            )
            break
        except Exception as exc:
            logger.error(
                "Error ingesting match odds for %s: %s", league, exc, exc_info=True
            )

    logger.info("=== Odds snapshot complete ===")


# ── Job 3b: Direct Odds Snapshot (French bookmakers) ─────────────


async def job_snapshot_direct_odds():
    """Snapshot odds from Unibet LVS HTTP API + Playwright scrapers.

    Flow:
    1. Unibet LVS API (nouveau site post-fusion PSEL) — no browser needed
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

    # ── 1. Unibet LVS scraper (nouveau site post-fusion PSEL, pure HTTP) ──
    try:
        from app.ingestion.unibet_lvs_scraper import scrape_all_unibet

        unibet_results = await scrape_all_unibet(leagues)
        all_match_odds.extend(unibet_results)
        logger.info("Unibet LVS scraper: %d match-odds objects", len(unibet_results))
    except Exception as exc:
        logger.error("Unibet LVS scrape failed: %s", exc, exc_info=True)

    # ── 2. Betclic gRPC-web scraper (full player odds, no Playwright needed) ──
    try:
        from app.ingestion.betclic_grpc_scraper import scrape_betclic_leagues

        betclic_results = await scrape_betclic_leagues(leagues)
        all_match_odds.extend(betclic_results)
        logger.info("Betclic gRPC scraper: %d match-odds objects", len(betclic_results))
    except Exception as exc:
        logger.error("Betclic gRPC scrape failed: %s", exc, exc_info=True)

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
                    async with session.begin_nested():
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
        from app.autopilot.features import extract_features_with_context
        from app.autopilot.trainer import _compute_stake, _load_agent, weights_exist

        if not weights_exist():
            logger.warning("Autopilot weights not found — skipping run (train first)")
            return

        agent = _load_agent()
        mode = user_settings.get("autopilot_mode", "paper")

        from app.models.autopilot import AutopilotDecision
        from app.models.fixtures import Fixture
        from app.models.odds import OddsSnapshot
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

            from app.notifications import notify_autopilot_position

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
                    .order_by(OddsSnapshot.snapshot_utc.asc())
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

            # Send one Telegram notification per bet taken
            for bet in bets_this_run:
                await notify_autopilot_position(
                    **bet,
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
            for decision, rec, fixture in rows:
                if not rec or not fixture:
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

                    from sqlalchemy import func as sa_func
                    ev_skip_result = await session.execute(
                        select(MatchEvent).where(
                            MatchEvent.fixture_id == fixture.id,
                            sa_func.lower(MatchEvent.player_name)
                            == sa_func.lower(decision.player_name),
                            MatchEvent.event_type == _skip_event_type,
                        )
                    )
                    would_have_won = ev_skip_result.scalars().first() is not None

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

                # Check match events for outcome
                ev_result = await session.execute(
                    select(MatchEvent).where(
                        MatchEvent.fixture_id == fixture.id,
                        MatchEvent.player_name == rec.player_name,
                        MatchEvent.event_type == _event_type,
                    )
                )
                events = ev_result.scalars().all()
                won = len(events) > 0

                result_str = "won" if won else "lost"
                pnl = round(
                    stake * (decision.best_odds - 1) if won else -stake, 2
                )

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


async def job_auto_settle():
    """Every 3 hours: auto-settle approved recommendations via Understat."""
    logger.info("=== Starting auto-settle job ===")
    from datetime import timedelta

    from app.models.fixtures import Fixture
    from app.notifications import send_telegram_alert

    try:
        async with async_session() as session:
            stats = await settle_approved_recommendations(session)

        settled = stats["settled"]
        logger.info("auto_settle: settled %d recommendations", settled)

        if settled > 0:
            await send_telegram_alert(
                f"✅ <b>[Ev0] Settlement automatique</b>\n\n"
                f"{settled} pari(s) réglé(s) :\n"
                f"• Gagnés : {stats['won']}\n"
                f"• Perdus : {stats['lost']}\n"
                f"• Voids : {stats['void']}"
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
                await send_telegram_alert(
                    f"🚨 <b>[Ev0] Settlement bloqué</b>\n\n"
                    f"{len(old_stuck)} match(s) terminé(s) depuis >48h impossible(s) à settler :\n"
                    f"{names}\n\n"
                    f"Cause probable : PlayerMatchMinutes ou MatchEvents manquants."
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
    from datetime import timedelta

    from app.models.fixtures import Fixture
    from app.notifications import send_telegram_alert

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
        await send_telegram_alert(
            f"⏱️ <b>[Ev0] Auto-finish fixtures</b>\n\n"
            f"{len(fixtures)} match(s) passés en <b>finished</b> (kickoff +2h dépassé) :\n"
            f"{names}"
            + (" ..." if len(fixtures) > 10 else "")
        )


# ── Job: Settlement Pipeline (every 30 min) ──────────────────────


async def job_settle_pipeline():
    """Every 30 min: auto-finish → sync match events (ESPN) → settle.

    Chains the three settlement steps in sequence so a bet can be settled
    within ~30 min of kickoff + 2h instead of waiting for three independent
    job cycles (up to 4h30 previously).
    """
    logger.info("=== Settlement pipeline: start ===")
    await job_auto_finish_fixtures()
    await job_sync_match_events()
    await job_auto_settle()
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


# ── Job: OddsPortal Scheduler Tick ───────────────────────────────


async def job_oddsportal_scheduler_tick() -> None:
    """Token-bucket tick — fires scrape chains for due fixtures."""
    async with async_session() as session:
        try:
            await _market_scheduler.tick(session)
        except Exception as exc:
            logger.error("job_oddsportal_scheduler_tick error: %s", exc, exc_info=True)


# ── Job: OddsPortal URL Auto-Discovery ───────────────────────────


async def job_discover_oddsportal_urls() -> None:
    """Daily discovery — scrapes OddsPortal league listings and seeds oddsportal_poll_state."""
    from playwright.async_api import async_playwright

    from app.ingestion.oddsportal_fixture_matcher import match_items_to_fixtures
    from app.ingestion.oddsportal_league_discoverer import discover_all_leagues

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                items = await discover_all_leagues(browser)
            finally:
                await browser.close()
        async with async_session() as session:
            results = await match_items_to_fixtures(items, session)
        logger.info("job_discover_oddsportal_urls: seeded %d fixtures", len(results))
    except Exception as exc:
        logger.error("job_discover_oddsportal_urls error: %s", exc, exc_info=True)


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


async def job_sync_bzzoiro_events():
    """Every 6h: sync Bzzoiro match events — 3 days back, 14 days forward."""
    logger.info("=== Starting Bzzoiro events sync ===")
    if not settings.bzzoiro_api_key:
        logger.warning("BZZOIRO_API_KEY not configured, skipping events sync")
        return
    try:
        async with async_session() as session, BzzoiroClient(settings.bzzoiro_api_key) as client:
            result = await sync_events(session, client, days_back=3, days_forward=14)
            logger.info("Bzzoiro events synced: %s", result)
    except Exception as exc:
        logger.error("Error in Bzzoiro events sync: %s", exc, exc_info=True)
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


# ── Scheduler Setup ───────────────────────────────────────────────


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler."""
    scheduler = AsyncIOScheduler()

    # Fixtures: Daily at 06:00 UTC
    scheduler.add_job(
        job_sync_fixtures,
        CronTrigger(hour=6, minute=0),
        id="sync_fixtures",
        name="Sync fixture kickoffs via The Odds API",
        replace_existing=True,
    )

    # Player stats: Daily at 07:00 UTC
    scheduler.add_job(
        job_sync_player_stats,
        CronTrigger(hour=7, minute=0),
        id="sync_player_stats",
        name="Sync player stats from Understat + FotMob",
        replace_existing=True,
    )

    # Sofascore stats: Daily at 07:15 UTC (after Understat, before FPL)
    scheduler.add_job(
        job_sync_sofascore_stats,
        CronTrigger(hour=7, minute=15),
        id="sync_sofascore_stats",
        name="Sync Sofascore player stats (BCC, SOT, TAP, crosses, TB)",
        replace_existing=True,
    )

    # FPL/Opta stats: Daily at 07:30 UTC (after player stats sync)
    scheduler.add_job(
        job_sync_fpl_stats,
        CronTrigger(hour=7, minute=30),
        id="sync_fpl_stats",
        name="Sync FPL/Opta player stats for PL players",
        replace_existing=True,
    )

    # Match events: handled by settle_pipeline (every 30 min)

    # Odds: Every hour
    scheduler.add_job(
        job_snapshot_odds,
        IntervalTrigger(hours=1),
        id="snapshot_odds",
        name="Snapshot odds from bookmakers",
        replace_existing=True,
    )

    # Direct odds (Unibet LVS HTTP + Playwright scrapers): Every 3 hours
    scheduler.add_job(
        job_snapshot_direct_odds,
        IntervalTrigger(hours=3),
        id="snapshot_direct_odds",
        name="Snapshot direct odds (Unibet LVS, Betclic, ParionsSport)",
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

    # Expire recommendations: Every 5 minutes
    scheduler.add_job(
        job_expire_recommendations,
        IntervalTrigger(minutes=5),
        id="expire_recommendations",
        name="Expire pending recommendations past kickoff",
        replace_existing=True,
    )

    # Settlement pipeline: every 30 min (auto-finish → ESPN events → settle)
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

    # OddsPortal scrape scheduler tick: every 15 seconds
    scheduler.add_job(
        job_oddsportal_scheduler_tick,
        IntervalTrigger(seconds=15, jitter=2),
        id="job_oddsportal_scheduler_tick",
        name="OddsPortal token-bucket tick: fires scrape chains for due fixtures",
        replace_existing=True,
        max_instances=1,
    )

    # OddsPortal URL auto-discovery: daily at 08:00 UTC
    scheduler.add_job(
        job_discover_oddsportal_urls,
        CronTrigger(hour=8, minute=0),
        id="job_discover_oddsportal_urls",
        name="OddsPortal URL auto-discovery: seeds oddsportal_poll_state from league listings",
        replace_existing=True,
        max_instances=1,
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

    # Bzzoiro predictions: daily at 07:45 UTC (offset from job_sync_player_stats at 07:00)
    scheduler.add_job(
        job_sync_bzzoiro_predictions,
        CronTrigger(hour=7, minute=45),
        id="sync_bzzoiro_predictions",
        name="Sync Bzzoiro match predictions",
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
