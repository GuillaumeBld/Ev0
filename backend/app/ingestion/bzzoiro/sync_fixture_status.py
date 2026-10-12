"""Sync Fixture.kickoff_utc / status / scores from BzzEvent.

BzzEvent is the authoritative source for match dates (Bzzoiro API).
The Fixture table may have stale kickoff_utc values from OddsAPI or manual entry.

Matching strategy:
1. A Fixture carrying external_id "bzz_<api_id>" is matched to THAT BzzEvent,
   whatever the date gap — a match rescheduled by weeks stays the same match.
2. Otherwise, fallback on normalized team names (fixture_matcher.normalize_team_name),
   restricted to BzzEvents within ±5 days of the fixture's own kickoff_utc.
3. Update kickoff_utc (if differs >30 min), status, and scores.

Sans la borne de l'etape 2, un rapprochement par noms pouvait retenir la
rencontre de la saison passee (meme affiche) : le 11/10/2026, Koln–Gladbach et
Brest–Angers avaient recu la date, le statut et le score de leur match de
mars/mai 2026.

Status mapping (BzzEvent → Fixture):
  "finished"   → "finished"
  "inprogress" → "live"
  "live"       → "live"
  anything else → leave unchanged (never downgrade a fixture)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import re

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.fixture_matcher import normalize_team_name
from app.models.bzzoiro import BzzEvent, BzzTeam
from app.models.fixtures import Fixture

logger = logging.getLogger(__name__)

_BZZ_TO_FIXTURE_STATUS = {
    "finished": "finished",
    "inprogress": "live",
    "live": "live",
}
_DATE_WINDOW = timedelta(days=5)
_KICKOFF_THRESHOLD_SECONDS = 1800  # 30 minutes
_BZZ_EXTERNAL_ID_RE = re.compile(r"^bzz_(\d+)$")


def _bzz_api_id(fixture: Fixture) -> int | None:
    """api_id of the BzzEvent a fixture was created from, if any."""
    ext = fixture.external_id
    if not isinstance(ext, str):
        return None
    m = _BZZ_EXTERNAL_ID_RE.match(ext)
    return int(m.group(1)) if m else None


async def sync_fixture_status_from_bzz(session: AsyncSession) -> int:
    """Update Fixture rows from matching BzzEvent rows. Returns count updated."""

    # 1. Load non-finished fixtures + finished fixtures that still have NULL scores
    fixture_result = await session.execute(
        select(Fixture).where(
            or_(
                Fixture.status != "finished",
                Fixture.home_score.is_(None),
            )
        )
    )
    fixtures = list(fixture_result.scalars().all())

    if not fixtures:
        logger.info("No fixtures need status/score sync")
        return 0

    # 2. Determine date range from their kickoff_utc (±5 days)
    kickoffs = [f.kickoff_utc for f in fixtures if f.kickoff_utc is not None]
    if not kickoffs:
        logger.info("No fixtures with kickoff_utc set")
        return 0

    earliest = min(kickoffs) - _DATE_WINDOW
    latest = max(kickoffs) + _DATE_WINDOW

    # 3. Load BzzEvents in that date range, plus the fixtures' own events
    own_ids = [i for i in (_bzz_api_id(f) for f in fixtures) if i is not None]
    event_result = await session.execute(
        select(BzzEvent).where(
            or_(
                (BzzEvent.event_date >= earliest) & (BzzEvent.event_date <= latest),
                BzzEvent.api_id.in_(own_ids),
            )
        )
    )
    bzz_events = list(event_result.scalars().all())

    if not bzz_events:
        logger.info("No BzzEvents found in date range [%s, %s]", earliest, latest)
        return 0

    # 4. Load BzzTeam names for all team api_ids referenced in those events
    team_api_ids: set[int] = set()
    for ev in bzz_events:
        if ev.home_team_api_id is not None:
            team_api_ids.add(ev.home_team_api_id)
        if ev.away_team_api_id is not None:
            team_api_ids.add(ev.away_team_api_id)

    team_map: dict[int, str] = {}
    if team_api_ids:
        team_result = await session.execute(
            select(BzzTeam).where(BzzTeam.api_id.in_(team_api_ids))
        )
        for team in team_result.scalars().all():
            team_map[team.api_id] = team.name

    # 5. Build index: (norm_home, norm_away) → list[BzzEvent]
    bzz_index: dict[tuple[str, str], list[BzzEvent]] = {}
    by_api_id: dict[int, BzzEvent] = {}
    for ev in bzz_events:
        if isinstance(ev.api_id, int):
            by_api_id[ev.api_id] = ev
        home_name = team_map.get(ev.home_team_api_id, "") if ev.home_team_api_id is not None else ""
        away_name = team_map.get(ev.away_team_api_id, "") if ev.away_team_api_id is not None else ""
        if not home_name or not away_name:
            continue
        key = (normalize_team_name(home_name), normalize_team_name(away_name))
        bzz_index.setdefault(key, []).append(ev)

    # 6. For each fixture, find best matching BzzEvent and update if needed
    updated = 0
    for fixture in fixtures:
        if fixture.kickoff_utc is None:
            continue

        norm_home = normalize_team_name(fixture.home_team or "")
        norm_away = normalize_team_name(fixture.away_team or "")

        own_id = _bzz_api_id(fixture)
        own = by_api_id.get(own_id) if own_id is not None else None
        if own is not None:
            # Lien direct : c'est ce match-la, quel que soit l'ecart de date.
            # Seule l'orientation reste a verifier (fixtures WC semees a part).
            own_home = team_map.get(own.home_team_api_id, "")
            own_away = team_map.get(own.away_team_api_id, "")
            reversed_match = (
                normalize_team_name(own_home) == norm_away
                and normalize_team_name(own_away) == norm_home
                and norm_home != norm_away
            )
            candidates = [own]
        else:
            def _in_window(ev: BzzEvent) -> bool:
                return (
                    ev.event_date is not None
                    and abs(ev.event_date - fixture.kickoff_utc) <= _DATE_WINDOW
                )

            candidates = [
                ev for ev in bzz_index.get((norm_home, norm_away), []) if _in_window(ev)
            ]

            # Neutral-venue WC games: BzzEvent may have home/away reversed vs our fixture.
            reversed_match = False
            if not candidates:
                candidates = [
                    ev for ev in bzz_index.get((norm_away, norm_home), []) if _in_window(ev)
                ]
                if candidates:
                    reversed_match = True

        if not candidates:
            continue

        # Pick the BzzEvent closest in date to the fixture's current kickoff_utc.
        # When two events are equidistant, prefer the more advanced status
        # (finished > inprogress/live > anything else) so a stale "notstarted"
        # duplicate doesn't shadow the real finished result.
        _STATUS_PRIORITY = {"finished": 0, "inprogress": 1, "live": 1}

        def _sort_key(ev: BzzEvent):
            date_diff = (
                abs((ev.event_date - fixture.kickoff_utc).total_seconds())
                if ev.event_date is not None
                else float("inf")
            )
            status_rank = _STATUS_PRIORITY.get(ev.status or "", 2)
            return (date_diff, status_rank)

        best = min(candidates, key=_sort_key)

        if best.event_date is None:
            continue

        changed = False

        # Update kickoff_utc if difference > 30 minutes
        diff_seconds = abs((best.event_date - fixture.kickoff_utc).total_seconds())
        if diff_seconds > _KICKOFF_THRESHOLD_SECONDS:
            logger.info(
                "Updating kickoff for %s vs %s: %s → %s (diff=%.0fs)",
                fixture.home_team,
                fixture.away_team,
                fixture.kickoff_utc,
                best.event_date,
                diff_seconds,
            )
            fixture.kickoff_utc = best.event_date
            changed = True

        # Update status (only forward-mapped statuses)
        mapped_status = _BZZ_TO_FIXTURE_STATUS.get(best.status or "")
        if mapped_status is not None and fixture.status != mapped_status:
            logger.info(
                "Updating status for %s vs %s: %s → %s",
                fixture.home_team,
                fixture.away_team,
                fixture.status,
                mapped_status,
            )
            fixture.status = mapped_status
            changed = True

        # Update scores only when BzzEvent is finished and scores are available.
        # If home/away are reversed in the BzzEvent, swap scores to match fixture orientation.
        if best.status == "finished":
            fix_hs = best.away_score if reversed_match else best.home_score
            fix_as = best.home_score if reversed_match else best.away_score
            if fix_hs is not None and fixture.home_score != fix_hs:
                fixture.home_score = fix_hs
                changed = True
            if fix_as is not None and fixture.away_score != fix_as:
                fixture.away_score = fix_as
                changed = True

        if changed:
            session.add(fixture)
            updated += 1

    if updated:
        await session.commit()

    logger.info("sync_fixture_status_from_bzz: %d fixtures updated", updated)
    return updated
