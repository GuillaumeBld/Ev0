"""Bzzoiro Sports Data API — authenticated async HTTP client with pagination."""
from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://sports.bzzoiro.com"
DEFAULT_TIMEOUT = 30.0


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
        assert self._client, "Use BzzoiroClient as async context manager"
        response = await self._client.get(path, params=params or {})
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
                next_url = raw_next.replace(self._base_url, "")
                page_params = {}
            else:
                next_url = None
            pages += 1
        return all_results
