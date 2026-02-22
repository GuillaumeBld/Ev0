"""Redis cache for Ev0.

Provides caching for external API responses (odds, player stats)
to avoid hammering rate-limited APIs on every request.
"""

import json
import logging
from typing import Any

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

# Singleton Redis client
_redis: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get or create the Redis client."""
    global _redis
    if _redis is None:
        _redis = redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
    return _redis


async def close_redis() -> None:
    """Close the Redis connection."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def cache_get(key: str) -> Any | None:
    """Get a JSON-serialized value from cache."""
    try:
        r = await get_redis()
        raw = await r.get(key)
        if raw is not None:
            return json.loads(raw)
    except Exception as exc:
        logger.warning("Redis GET failed for %s: %s", key, exc)
    return None


async def cache_set(key: str, value: Any, ttl_seconds: int = 300) -> None:
    """Set a JSON-serialized value in cache with TTL."""
    try:
        r = await get_redis()
        await r.set(key, json.dumps(value, default=str), ex=ttl_seconds)
    except Exception as exc:
        logger.warning("Redis SET failed for %s: %s", key, exc)


async def cache_delete(pattern: str) -> None:
    """Delete keys matching a pattern."""
    try:
        r = await get_redis()
        async for key in r.scan_iter(match=pattern):
            await r.delete(key)
    except Exception as exc:
        logger.warning("Redis DELETE failed for pattern %s: %s", pattern, exc)


# ── Domain-specific cache helpers ────────────────────────────────

# TTLs
ODDS_EVENTS_TTL = 300  # 5 min — list of upcoming events
ODDS_PLAYER_PROPS_TTL = 120  # 2 min — player props per event
PLAYER_STATS_TTL = 3600  # 1 hour — DB player stats


async def cache_odds_events(sport_key: str, events: list[dict]) -> None:
    """Cache Odds API events list."""
    await cache_set(f"odds:events:{sport_key}", events, ODDS_EVENTS_TTL)


async def get_cached_odds_events(sport_key: str) -> list[dict] | None:
    """Get cached Odds API events list."""
    return await cache_get(f"odds:events:{sport_key}")


async def cache_player_props(sport_key: str, event_id: str, market: str, props: list[dict]) -> None:
    """Cache Odds API player props."""
    await cache_set(f"odds:props:{sport_key}:{event_id}:{market}", props, ODDS_PLAYER_PROPS_TTL)


async def get_cached_player_props(sport_key: str, event_id: str, market: str) -> list[dict] | None:
    """Get cached Odds API player props."""
    return await cache_get(f"odds:props:{sport_key}:{event_id}:{market}")


async def cache_player_stats_map(stats: dict[str, dict]) -> None:
    """Cache the player stats lookup map."""
    await cache_set("player:stats_map", stats, PLAYER_STATS_TTL)


async def get_cached_player_stats_map() -> dict[str, dict] | None:
    """Get cached player stats lookup map."""
    return await cache_get("player:stats_map")
