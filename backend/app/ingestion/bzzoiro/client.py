"""Bzzoiro Sports Data API — authenticated async HTTP client with pagination."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

BASE_URL = "https://sports.bzzoiro.com"
DEFAULT_TIMEOUT = 30.0

logger = logging.getLogger(__name__)


class BzzoiroClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BzzoiroClient:
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Token {self._api_key}"},
            timeout=DEFAULT_TIMEOUT,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client:
            await self._client.aclose()

    async def get_page(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._client:
            raise RuntimeError("Use BzzoiroClient as async context manager")
        for attempt in range(5):
            try:
                response = await self._client.get(path, params=params or None)
                if response.status_code in (429, 502, 503, 504):
                    wait = 2 ** attempt * 5  # 5s, 10s, 20s, 40s, 80s
                    logger.warning("HTTP %d on %s — retry %d in %ds", response.status_code, path, attempt + 1, wait)
                    await asyncio.sleep(wait)
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.TimeoutException:
                wait = 2 ** attempt * 5
                logger.warning("Timeout on %s — retry %d in %ds", path, attempt + 1, wait)
                await asyncio.sleep(wait)
        # Final attempt — let it raise
        response = await self._client.get(path, params=params or None)
        response.raise_for_status()
        return response.json()

    async def get_all(self, path: str, params: dict[str, Any] | None = None, max_pages: int = 500) -> list[dict[str, Any]]:
        all_results: list[dict[str, Any]] = []
        next_url: str | None = path
        page_params = dict(params or {})
        pages = 0
        while next_url and pages < max_pages:
            data = await self.get_page(next_url, page_params)
            results = data.get("results") or data
            if isinstance(results, list):
                all_results.extend(results)
            raw_next = data.get("next")
            if raw_next:
                parsed = urlparse(raw_next)
                next_url = f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
                page_params = {}
            else:
                next_url = None
            pages += 1
        return all_results
