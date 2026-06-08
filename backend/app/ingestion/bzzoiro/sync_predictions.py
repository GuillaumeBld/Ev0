"""Sync bzz_predictions — ML match predictions from the Bzzoiro predictions endpoint."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.bzzoiro.client import BzzoiroClient
from app.models.bzzoiro import BzzEvent, BzzPrediction

logger = logging.getLogger(__name__)


async def sync_predictions(
    session: AsyncSession,
    client: BzzoiroClient,
    days_forward: int = 48,
) -> int:
    """Fetch all predictions from Bzzoiro and upsert into bzz_predictions.

    Only predictions for events within the next ``days_forward`` hours are
    upserted.  If no upcoming events exist in the DB yet (initial sync), the
    filter is skipped so that all predictions are persisted.

    Args:
        session: SQLAlchemy async session.
        client: Authenticated BzzoiroClient.
        days_forward: Number of hours ahead to consider for the event window.

    Returns:
        Number of predictions upserted.
    """
    now = datetime.now(UTC)
    cutoff = now + timedelta(hours=days_forward)

    event_result = await session.execute(
        select(BzzEvent.api_id).where(
            BzzEvent.event_date >= now,
            BzzEvent.event_date <= cutoff,
        )
    )
    upcoming_event_ids = {row[0] for row in event_result.fetchall()}

    rows = await client.get_all("/api/predictions/", params={"upcoming": "true"})
    count = 0
    for row in rows:
        event = row.get("event") or {}
        event_api_id = event.get("api_id") or event.get("id")
        if event_api_id is None:
            logger.warning("Prediction row missing event id — skipping: %r", row)
            continue

        if upcoming_event_ids and event_api_id not in upcoming_event_ids:
            continue

        created_at_raw = row.get("created_at")
        created_at_bzz = (
            datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
            if created_at_raw
            else None
        )

        values = {
            "event_api_id": event_api_id,
            "created_at_bzz": created_at_bzz,
            "prob_home_win": row.get("prob_home_win"),
            "prob_draw": row.get("prob_draw"),
            "prob_away_win": row.get("prob_away_win"),
            "predicted_result": row.get("predicted_result"),
            "expected_home_goals": row.get("expected_home_goals"),
            "expected_away_goals": row.get("expected_away_goals"),
            "prob_over_15": row.get("prob_over_15"),
            "prob_over_25": row.get("prob_over_25"),
            "prob_over_35": row.get("prob_over_35"),
            "prob_btts_yes": row.get("prob_btts_yes"),
            "confidence": row.get("confidence"),
            "model_version": row.get("model_version"),
            "most_likely_score": row.get("most_likely_score"),
            "favorite": row.get("favorite"),
            "favorite_prob": row.get("favorite_prob"),
            "favorite_recommend": row.get("favorite_recommend"),
            "over_15_recommend": row.get("over_15_recommend"),
            "over_25_recommend": row.get("over_25_recommend"),
            "over_35_recommend": row.get("over_35_recommend"),
            "btts_recommend": row.get("btts_recommend"),
            "winner_recommend": row.get("winner_recommend"),
        }
        stmt = pg_insert(BzzPrediction).values(**values).on_conflict_do_update(
            index_elements=["event_api_id"],
            set_={k: v for k, v in values.items() if k != "event_api_id"},
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d predictions", count)
    return count
