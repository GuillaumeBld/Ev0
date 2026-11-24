"""Bzzoiro Sports Data API — authenticated async HTTP client with pagination."""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

BASE_URL = "https://sports.bzzoiro.com"
DEFAULT_TIMEOUT = 30.0

logger = logging.getLogger(__name__)


class PaginationTronqueeError(RuntimeError):
    """Le parcours des pages n'a pas ramene tout ce que l'API annonce."""


class BzzoiroQuotaExhausted(RuntimeError):
    """Quota quotidien Bzzoiro épuisé : plus aucune réponse avant minuit UTC."""


# Quota quotidien. Le 24/11/2026 Bzzoiro a répondu « daily limit reached — the
# free football API allows 7,500 requests a day, it resets at midnight UTC » :
# plus de compos, de scores ni de statuts jusqu'à minuit. Tant que le quota est
# épuisé, on ne rappelle plus l'API (chaque appel attendait 155 s de reprises
# pour rien) ; les compteurs disent qui consomme quoi.
_quota_exhausted_until: datetime | None = None
_calls_day: str | None = None
_calls_today: Counter[str] = Counter()


def _endpoint(path: str) -> str:
    """Famille d'un appel, pour le compteur : « events », « events/incidents »..."""
    parts = [p for p in urlparse(path).path.split("/") if p and p not in ("api", "v2")]
    return "/".join(p for p in parts if not p.isdigit()) or "?"


def _count_call(path: str) -> None:
    global _calls_day
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if today != _calls_day:
        _calls_day = today
        _calls_today.clear()
    _calls_today[_endpoint(path)] += 1


def calls_today() -> dict[str, int]:
    """Appels Bzzoiro de la journée UTC en cours (depuis le démarrage du worker), par famille."""
    if _calls_day != datetime.now(UTC).strftime("%Y-%m-%d"):
        return {}
    return dict(_calls_today)


def quota_exhausted() -> bool:
    return _quota_exhausted_until is not None and datetime.now(UTC) < _quota_exhausted_until


def _mark_quota_exhausted() -> None:
    global _quota_exhausted_until
    first = not quota_exhausted()
    now = datetime.now(UTC)
    _quota_exhausted_until = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
    if first:
        logger.error(
            "Bzzoiro : quota quotidien épuisé, plus aucun appel avant minuit UTC. "
            "Appels du jour par famille : %s", dict(_calls_today.most_common()),
        )
        try:
            from app.alerts import alert_bg

            alert_bg(
                "🚨 <b>[Ev0] Quota Bzzoiro épuisé</b>\n\nPlus de compos, de scores ni de statuts "
                "jusqu'à minuit UTC (1 h à Paris). Le xG (PS3838) et les cotes des books ne sont "
                f"pas touchés.\nAppels du jour : {dict(_calls_today.most_common(6))}",
                "incidents",
            )
        except Exception:
            pass


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
        if quota_exhausted():
            raise BzzoiroQuotaExhausted("quota quotidien Bzzoiro épuisé")
        for attempt in range(5):
            try:
                _count_call(path)
                response = await self._client.get(path, params=params or None)
                if response.status_code == 429 and "daily limit" in response.text.lower():
                    # Pas une limite de débit passagère : inutile de réessayer.
                    _mark_quota_exhausted()
                    raise BzzoiroQuotaExhausted("quota quotidien Bzzoiro épuisé")
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
        _count_call(path)
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
