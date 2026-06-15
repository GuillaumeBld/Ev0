"""Ev0 API - FastAPI application."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import (
    autopilot,
    backtest,
    bankroll,
    canonical_teams_api,
    config as config_api,
    fixtures,
    health,
    history,
    pen_takers,
    players,
    pricing,
    recommendations,
)
from app.api import (
    lineups as lineups_api,
)
from app.api import settings as settings_api
from app.api import wc2026 as wc2026_api
from app.api import wc2026_lineups as wc2026_lineups_api
from app.api import wc2026_matches as wc2026_matches_api
from app.api import wc2026_pricing as wc2026_pricing_api
from app.api import wc2026_stats as wc2026_stats_api
from app.cache import close_redis
from app.config import settings
from app.db import engine
from app.rate_limit import limiter


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    yield
    # Shutdown
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="Ev0 API",
    description="Prematch Value Engine - Player Props Pricing",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(health.router, tags=["health"])
app.include_router(canonical_teams_api.router, prefix="/api/v1", tags=["canonical-teams"])
app.include_router(players.router, prefix="/api/v1", tags=["players"])
app.include_router(pricing.router, prefix="/api/v1", tags=["pricing"])
app.include_router(recommendations.router, prefix="/api/v1", tags=["recommendations"])
app.include_router(fixtures.router, prefix="/api/v1", tags=["fixtures"])
app.include_router(history.router, prefix="/api/v1", tags=["history"])
app.include_router(backtest.router, prefix="/api/v1", tags=["backtest"])
app.include_router(bankroll.router, prefix="/api/v1", tags=["bankroll"])
app.include_router(settings_api.router, prefix="/api/v1", tags=["settings"])
app.include_router(autopilot.router, prefix="/api/v1", tags=["autopilot"])
app.include_router(lineups_api.router, prefix="/api/v1", tags=["lineups"])
app.include_router(pen_takers.router, prefix="/api/v1", tags=["pen-takers"])
app.include_router(wc2026_api.router, prefix="/api/v1", tags=["wc2026"])
app.include_router(wc2026_lineups_api.router, prefix="/api/v1", tags=["wc2026"])
app.include_router(wc2026_pricing_api.router, prefix="/api/v1", tags=["wc2026"])
app.include_router(wc2026_matches_api.router, prefix="/api/v1", tags=["wc2026"])
app.include_router(wc2026_stats_api.router, prefix="/api/v1", tags=["wc2026"])
app.include_router(config_api.router, prefix="/api", tags=["config"])
