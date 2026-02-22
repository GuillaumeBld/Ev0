"""Health check endpoints."""

import os
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.fixtures import Fixture
from app.models.odds import OddsSnapshot
from app.models.players import PlayerStats
from app.models.recommendations import Recommendation

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "service": "ev0-api"}


@router.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)) -> dict:
    """Readiness check — verifies DB connectivity."""
    checks = {}

    # Check DB
    try:
        t0 = time.monotonic()
        await db.execute(text("SELECT 1"))
        db_ms = round((time.monotonic() - t0) * 1000)
        checks["db"] = {"status": "healthy", "latency_ms": db_ms}
    except Exception as exc:
        checks["db"] = {"status": "down", "error": str(exc)}

    overall = "ready" if all(c["status"] == "healthy" for c in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}


@router.get("/version")
async def version_check() -> dict:
    """Code version check."""
    return {
        "deploy_version": os.environ.get("DEPLOY_VERSION", "unknown"),
    }


class DataQualityItem(BaseModel):
    source: str
    last_sync: str | None
    record_count: int
    freshness: str  # fresh, stale, outdated
    issues: list[str]


def _freshness(last_sync: datetime | None) -> str:
    if last_sync is None:
        return "outdated"
    now = datetime.now(UTC)
    age = now - last_sync.replace(tzinfo=UTC) if last_sync.tzinfo is None else now - last_sync
    if age < timedelta(hours=24):
        return "fresh"
    elif age < timedelta(hours=72):
        return "stale"
    return "outdated"


@router.get("/data-quality", response_model=list[DataQualityItem])
async def data_quality(db: AsyncSession = Depends(get_db)):
    """Data quality metrics for all data sources."""
    items: list[DataQualityItem] = []

    # Fixtures
    result = await db.execute(select(func.count(Fixture.id), func.max(Fixture.updated_at)))
    count, last = result.one()
    fresh = _freshness(last)
    issues = [] if count and count > 0 else ["Aucun fixture en base"]
    items.append(
        DataQualityItem(
            source="Fixtures",
            last_sync=str(last) if last else None,
            record_count=count or 0,
            freshness=fresh,
            issues=issues,
        )
    )

    # Odds Snapshots
    result = await db.execute(
        select(func.count(OddsSnapshot.id), func.max(OddsSnapshot.snapshot_utc))
    )
    count, last = result.one()
    fresh = _freshness(last)
    issues = [] if count and count > 0 else ["Aucun snapshot de cotes"]
    items.append(
        DataQualityItem(
            source="Odds Snapshots",
            last_sync=str(last) if last else None,
            record_count=count or 0,
            freshness=fresh,
            issues=issues,
        )
    )

    # Player Stats
    result = await db.execute(select(func.count(PlayerStats.id), func.max(PlayerStats.as_of_utc)))
    count, last = result.one()
    fresh = _freshness(last)
    issues = [] if count and count > 0 else ["Aucune stat joueur"]
    items.append(
        DataQualityItem(
            source="Player Stats",
            last_sync=str(last) if last else None,
            record_count=count or 0,
            freshness=fresh,
            issues=issues,
        )
    )

    # Recommendations
    result = await db.execute(
        select(func.count(Recommendation.id), func.max(Recommendation.generated_utc))
    )
    count, last = result.one()
    fresh = _freshness(last)
    issues = [] if count and count > 0 else ["Aucune recommandation generee"]
    items.append(
        DataQualityItem(
            source="Recommendations",
            last_sync=str(last) if last else None,
            record_count=count or 0,
            freshness=fresh,
            issues=issues,
        )
    )

    return items
