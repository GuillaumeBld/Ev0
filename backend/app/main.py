"""Ev0 API - FastAPI application."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import backtest, bankroll, fixtures, health, history, players, pricing, recommendations
from app.cache import close_redis
from app.config import settings
from app.db import engine


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
app.include_router(players.router, prefix="/api/v1", tags=["players"])
app.include_router(pricing.router, prefix="/api/v1", tags=["pricing"])
app.include_router(recommendations.router, prefix="/api/v1", tags=["recommendations"])
app.include_router(fixtures.router, prefix="/api/v1", tags=["fixtures"])
app.include_router(history.router, prefix="/api/v1", tags=["history"])
app.include_router(backtest.router, prefix="/api/v1", tags=["backtest"])
app.include_router(bankroll.router, prefix="/api/v1", tags=["bankroll"])
