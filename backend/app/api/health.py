"""Health check endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "service": "ev0-api"}


@router.get("/ready")
async def readiness_check() -> dict[str, str]:
    """Readiness check endpoint."""
    # TODO: Check DB and Redis connectivity
    return {"status": "ready"}


@router.get("/version")
async def version_check() -> dict[str, str]:
    """Code version check."""
    import os
    return {"deploy_version": os.environ.get("DEPLOY_VERSION", "unknown"), "code_version": "2026-02-22-debug-v2"}
