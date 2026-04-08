"""Adaptive token-bucket scheduler for market odds scraping.

Public functions used by worker.py:
    MarketScrapeScheduler.tick() — called every ~15s by APScheduler

Pure scheduling helpers (exported for testing):
    _compute_interval_minutes(t_minutes) -> int | None
    _compute_score(t_minutes, error_streak) -> float
    _compute_target_rpm(due_count, pressure_count, max_rpm_hard) -> float
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

import httpx
from playwright.async_api import async_playwright
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fixtures import Fixture
from app.models.poll_state import OddsPortalPollState
from app.models.team_xg import TeamXgEstimate

logger = logging.getLogger(__name__)

MAX_RPM_HARD: float = 5.0
JITTER_FACTOR: float = 0.15          # ± 15% on interval
T_MINUS_STOP_MINUTES: int = 5
PRESSURE_WINDOW_MINUTES: int = 120   # "hot" window for pressure count
PRESSURE_THRESHOLD: int = 10         # matches in hot window to trigger boost
BACKOFF_FREEZE_MINUTES: int = 20
BACKOFF_RECOVERY_RPM_STEP: float = 0.25
BACKOFF_RECOVERY_INTERVAL_MINUTES: int = 10


def _compute_interval_minutes(t_minutes: float) -> int | None:
    """Return polling interval in minutes for a match t_minutes before KO. None = stop."""
    if t_minutes <= T_MINUS_STOP_MINUTES:
        return None
    if t_minutes <= 30:
        return 3
    if t_minutes <= 120:
        return 7
    if t_minutes <= 360:
        return 20
    if t_minutes <= 1440:
        return 60
    return 120


def _compute_score(t_minutes: float, error_streak: int) -> float:
    """Priority score: higher = scrape sooner. urgency = 1/(t+15), penalty capped at 0.5."""
    urgency = 1.0 / (t_minutes + 15.0)
    penalty = min(0.5, 0.1 * error_streak)
    return urgency - penalty


def _compute_target_rpm(
    due_count: int,
    pressure_count: int,
    max_rpm_hard: float = MAX_RPM_HARD,
) -> float:
    """Dynamic target RPM based on queue depth and hot-window pressure."""
    if due_count == 0:
        rpm = 1.0
    elif due_count <= 3:
        rpm = 2.0
    else:
        rpm = 3.0

    if pressure_count > PRESSURE_THRESHOLD and due_count > 0:
        rpm = max_rpm_hard

    return min(rpm, max_rpm_hard)


def _apply_jitter(interval_minutes: int) -> timedelta:
    """Add ±15% jitter to interval."""
    factor = 1.0 + random.uniform(-JITTER_FACTOR, JITTER_FACTOR)
    return timedelta(minutes=interval_minutes * factor)


class MarketScrapeScheduler:
    """Token-bucket scheduler. Instantiated once in worker.py."""

    def __init__(self) -> None:
        self._tokens: float = 1.0
        self._target_rpm: float = 1.0
        self._frozen_until: datetime | None = None
        self._last_tick: datetime = datetime.now(timezone.utc)
        self._recovery_check: datetime = datetime.now(timezone.utc)

    def _refill_tokens(self, now: datetime) -> None:
        elapsed = (now - self._last_tick).total_seconds()
        self._tokens = min(
            self._tokens + self._target_rpm / 60.0 * elapsed,
            MAX_RPM_HARD,
        )
        self._last_tick = now

    def trigger_backoff(self) -> None:
        """Call on HTTP 429 / captcha / persistent errors."""
        self._target_rpm = max(0.5, self._target_rpm * 0.5)
        self._frozen_until = datetime.now(timezone.utc) + timedelta(minutes=BACKOFF_FREEZE_MINUTES)
        logger.warning("scheduler: backoff triggered, target_rpm=%.2f, frozen=%s", self._target_rpm, self._frozen_until)

    def _maybe_recover(self, now: datetime) -> None:
        if self._frozen_until and now > self._frozen_until:
            if now > self._recovery_check + timedelta(minutes=BACKOFF_RECOVERY_INTERVAL_MINUTES):
                self._target_rpm = min(MAX_RPM_HARD, self._target_rpm + BACKOFF_RECOVERY_RPM_STEP)
                self._recovery_check = now
                logger.info("scheduler: recovery step, target_rpm=%.2f", self._target_rpm)

    async def tick(self, session: AsyncSession) -> None:
        """
        Main tick — called every ~15s by APScheduler.
        Refills tokens, selects eligible fixtures, fires scrape chains.
        """
        now = datetime.now(timezone.utc)
        self._refill_tokens(now)
        self._maybe_recover(now)

        if self._frozen_until and now < self._frozen_until:
            logger.debug("scheduler: frozen until %s", self._frozen_until)
            return

        # Load eligible poll states
        eligible_q = (
            select(OddsPortalPollState, Fixture.kickoff_utc)
            .join(Fixture, OddsPortalPollState.fixture_id == Fixture.id)
            .where(
                OddsPortalPollState.stopped.is_(False),
                OddsPortalPollState.next_due_at_utc <= now,
            )
        )
        rows = (await session.execute(eligible_q)).all()

        # Filter: now < KO - 5min
        eligible = []
        for poll_state, kickoff_utc in rows:
            t_minutes = (kickoff_utc - now).total_seconds() / 60.0
            if t_minutes <= T_MINUS_STOP_MINUTES:
                # Stop this fixture
                await session.execute(
                    update(OddsPortalPollState)
                    .where(OddsPortalPollState.id == poll_state.id)
                    .values(stopped=True, stopped_reason="T_MINUS_5")
                )
                continue
            eligible.append((poll_state, kickoff_utc, t_minutes))

        # Compute dynamic target_rpm
        due_count = len(eligible)
        pressure_count = sum(1 for _, _, t in eligible if t <= PRESSURE_WINDOW_MINUTES)
        self._target_rpm = _compute_target_rpm(due_count, pressure_count)

        # Sort by priority score
        eligible.sort(key=lambda x: _compute_score(x[2], x[0].error_streak), reverse=True)

        logger.info(
            "scheduler: tick due=%d pressure=%d tokens=%.2f target_rpm=%.2f",
            due_count, pressure_count, self._tokens, self._target_rpm,
        )

        # Fire scrapes
        tasks = []
        for poll_state, kickoff_utc, t_minutes in eligible:
            if self._tokens < 1.0:
                break
            self._tokens -= 1.0
            tasks.append(self._run_scrape(poll_state, t_minutes, session))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await session.commit()

    async def _run_scrape(
        self,
        poll_state: OddsPortalPollState,
        t_minutes: float,
        session: AsyncSession,
    ) -> None:
        """Run the fallback chain for one fixture and update poll state."""
        from app.ingestion.market_scrape_chain import run_scrape_chain, store_scrape_result
        from app.services.market_xg import MarketXgService

        now = datetime.now(timezone.utc)
        logger.info(
            "scheduler: scrape fixture=%s t_to_ko=%.0f min",
            poll_state.fixture_id, t_minutes,
        )

        # Update last_scraped_at
        await session.execute(
            update(OddsPortalPollState)
            .where(OddsPortalPollState.id == poll_state.id)
            .values(last_scraped_at_utc=now)
        )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            async with httpx.AsyncClient() as http_client:
                result = await run_scrape_chain(poll_state, browser, http_client)
            await browser.close()

        if result is None:
            # All sources failed
            new_streak = poll_state.error_streak + 1
            await session.execute(
                update(OddsPortalPollState)
                .where(OddsPortalPollState.id == poll_state.id)
                .values(error_streak=new_streak)
            )
            if new_streak >= 3:
                self.trigger_backoff()
            logger.warning(
                "scrape_fail fixture=%s sources_tried=[op,bc,ub] error_streak=%d",
                poll_state.fixture_id, new_streak,
            )
        else:
            # Store snapshots
            await store_scrape_result(result, poll_state.fixture_id, session)

            # Compute and store team xG estimate
            xg_service = MarketXgService()
            xg = await xg_service.compute(poll_state.fixture_id, session)
            if xg is not None:
                session.add(TeamXgEstimate(
                    fixture_id=poll_state.fixture_id,
                    as_of_utc=result.ingested_at_utc,
                    lambda_home=xg.xg_home,
                    lambda_away=xg.xg_away,
                    fit_residual=xg.fit_residual,
                    flagged=xg.flagged,
                    data_source=xg.data_source,
                    fallback_used=xg.fallback_used,
                    input_snapshot_ids=xg.input_snapshot_ids,
                ))

            # Schedule next due
            interval = _compute_interval_minutes(t_minutes)
            if interval is None:
                next_due = None
                stopped = True
                stopped_reason = "T_MINUS_5"
            else:
                next_due = now + _apply_jitter(interval)
                stopped = False
                stopped_reason = None

            await session.execute(
                update(OddsPortalPollState)
                .where(OddsPortalPollState.id == poll_state.id)
                .values(
                    last_success_at_utc=now,
                    error_streak=0,
                    next_due_at_utc=next_due or poll_state.next_due_at_utc,
                    stopped=stopped,
                    stopped_reason=stopped_reason,
                )
            )
            logger.info(
                "scrape_success fixture=%s source=%s fallback=%s next_due=%s",
                poll_state.fixture_id, result.source, result.fallback_used, next_due,
            )
