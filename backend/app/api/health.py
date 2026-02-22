"""Health check endpoints."""

import os
import time

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db

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
