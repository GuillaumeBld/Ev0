"""StatsHub public API client — no authentication required.

Endpoint used: GET /api/team/{team_id}/players/performance
               ?tournamentId={tournament_id}&seasonId={season_id}

Returns a dict keyed by player slug, each value containing:
  - id, internalId, slug, name, position
  - stats: dict[str, dict] keyed by eventId (SofaScore event ID as string)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

BASE_URL = "https://www.statshub.com"
DEFAULT_TIMEOUT = 30.0

logger = logging.getLogger(__name__)


class StatshubClient:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> StatshubClient:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.statshub.com/",
            },
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    async def get_team_player_performance(
        self,
        team_id: int,
        tournament_id: int,
        season_id: int,
    ) -> dict[str, Any]:
        """Fetch per-player, per-match stats for a team in a tournament/season.

        Returns dict keyed by player slug with internalId + per-event stats,
        or empty dict on 404 / repeated failure.
        """
        if not self._client:
            raise RuntimeError("Use StatshubClient as async context manager")

        path = f"/api/team/{team_id}/players/performance"
        params = {"tournamentId": tournament_id, "seasonId": season_id}

        for attempt in range(4):
            try:
                resp = await self._client.get(path, params=params)
                if resp.status_code == 404:
                    logger.debug(
                        "statshub: team %d not found in tournament=%d season=%d",
                        team_id, tournament_id, season_id,
                    )
                    return {}
                if resp.status_code in (429, 502, 503, 504):
                    wait = 2 ** attempt * 3
                    logger.warning(
                        "statshub HTTP %d (team=%d) — retry %d in %ds",
                        resp.status_code, team_id, attempt + 1, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, dict) else {}
            except httpx.TimeoutException:
                wait = 2 ** attempt * 3
                logger.warning(
                    "statshub timeout (team=%d) — retry %d in %ds",
                    team_id, attempt + 1, wait,
                )
                await asyncio.sleep(wait)

        logger.error(
            "statshub: all retries failed for team=%d tournament=%d season=%d",
            team_id, tournament_id, season_id,
        )
        return {}
