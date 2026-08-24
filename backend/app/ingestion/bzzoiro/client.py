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


class PaginationTronqueeError(RuntimeError):
    """Le parcours des pages n'a pas ramene tout ce que l'API annonce."""


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
        """Parcourt toutes les pages. Leve si le resultat est tronque.

        Une troncature silencieuse a deja coute cher : la liste mondiale des
        joueurs fait 2 349 pages pour un plafond a 500, et 78 % des joueurs
        n'ont jamais ete rafraichis sans qu'aucun signal ne l'indique.
        """
        all_results: list[dict[str, Any]] = []
        next_url: str | None = path
        page_params = dict(params or {})
        pages = 0
        total_annonce: int | None = None
        while next_url and pages < max_pages:
            data = await self.get_page(next_url, page_params)
            if total_annonce is None and isinstance(data, dict):
                total_annonce = data.get("count")
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

        if next_url:
            raise PaginationTronqueeError(
                f"{path} : plafond de {max_pages} pages atteint alors qu'il reste "
                f"des pages ({len(all_results)} lignes ramenees). Reduire le "
                f"perimetre de la requete plutot que d'accepter un resultat partiel."
            )

        if isinstance(total_annonce, int) and len(all_results) != total_annonce:
            raise PaginationTronqueeError(
                f"{path} : l'API annonce {total_annonce} lignes, "
                f"{len(all_results)} recues"
            )

        return all_results
