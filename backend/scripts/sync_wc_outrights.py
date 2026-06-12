#!/usr/bin/env python3
"""Sync WC2026 outright odds (top scorer, winner…) depuis Unibet, PMU et Betclic.

À lancer en LOCAL (IP résidentielle FR) — les bookmakers bloquent les IP datacenter.

Prérequis Betclic :
    pip install playwright && playwright install chromium

Usage :
    python scripts/sync_wc_outrights.py [--dry-run] [--bookmaker unibet|pmu|betclic] [--api-url URL]

Exemples :
    # Scrape Betclic localement et pousse vers l'API prod
    python scripts/sync_wc_outrights.py --bookmaker betclic --api-url https://ev0-213-130-144-204.sslip.io

    # Scrape tout via l'API du backend (PMU + Unibet depuis VPS, Betclic local → API)
    python scripts/sync_wc_outrights.py --api-url https://ev0-213-130-144-204.sslip.io

    # Mode DB directe (si port 5432 accessible)
    python scripts/sync_wc_outrights.py --bookmaker pmu
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_DEFAULT_DSN = "postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@213.130.144.204:5432/ev0"


async def push_via_api(api_url: str, bookmaker: str, rows: list[dict]) -> None:
    """Envoie les cotes scrapées vers POST /api/v1/wc2026/pricing/store-odds."""
    import httpx
    url = f"{api_url.rstrip('/')}/api/v1/wc2026/pricing/store-odds"
    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        r = await client.post(url, params={"bookmaker": bookmaker}, json=rows)
        r.raise_for_status()
        data = r.json()
    logger.info(
        "✓ %s via API: %d upsertées, %d désactivées",
        bookmaker, data["scraped"], data["deactivated"],
    )


async def main(dry_run: bool, bookmaker: str | None, api_url: str | None) -> None:
    from collections import Counter

    from app.ingestion.wc2026.sync_wc_outrights import (
        scrape_betclic_wc_outrights,
        scrape_pmu_wc_outrights,
        scrape_unibet_wc_outrights,
    )

    scrapers: list[tuple[str, object]] = []
    if bookmaker in (None, "unibet"):
        scrapers.append(("unibet", scrape_unibet_wc_outrights()))
    if bookmaker in (None, "pmu"):
        scrapers.append(("pmu", scrape_pmu_wc_outrights()))
    if bookmaker in (None, "betclic"):
        scrapers.append(("betclic", scrape_betclic_wc_outrights()))

    results_by_bk: dict[str, list[dict]] = {}
    for name, coro in scrapers:
        logger.info("Scraping %s…", name)
        try:
            rows = await coro  # type: ignore[misc]
            logger.info("%s: %d cotes", name, len(rows))
            results_by_bk[name] = rows
        except Exception as exc:
            logger.error("%s failed: %s", name, exc)
            results_by_bk[name] = []

    all_results = [r for rows in results_by_bk.values() for r in rows]
    if not all_results:
        logger.warning("Aucune cote récupérée.")
        return

    summary = Counter(r["market_type"] for r in all_results)
    logger.info("Résumé par market_type: %s", dict(summary))
    logger.info("Résumé par bookmaker: %s", {k: len(v) for k, v in results_by_bk.items()})

    if dry_run:
        logger.info("--dry-run: pas de stockage. Aperçu (%d rows):", len(all_results))
        for row in all_results[:10]:
            logger.info("  %s", row)
        return

    if api_url:
        # Pousse vers le backend via HTTP (pas d'accès direct DB requis)
        for bk, rows in results_by_bk.items():
            if rows:
                await push_via_api(api_url, bk, rows)
    else:
        # Accès DB direct (nécessite que le port 5432 soit accessible)
        from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
        from sqlalchemy.orm import sessionmaker
        from app.ingestion.wc2026.sync_wc_outrights import store_wc_outrights_for_bookmaker

        dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
        async_dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(async_dsn, echo=False)
        AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with AsyncSessionLocal() as session:
            for bk, rows in results_by_bk.items():
                stats = await store_wc_outrights_for_bookmaker(session, rows, bk)
                logger.info("✓ %s: %d upsertées, %d désactivées", bk, stats["scraped"], stats["deactivated"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync WC2026 outright odds localement")
    parser.add_argument("--dry-run", action="store_true", help="Affiche les cotes sans stocker")
    parser.add_argument(
        "--bookmaker",
        choices=["unibet", "pmu", "betclic"],
        help="Limiter à un seul bookmaker",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("EV0_API_URL"),
        help="URL du backend (ex: https://ev0-213-130-144-204.sslip.io). "
             "Si absent, connexion DB directe.",
    )
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, bookmaker=args.bookmaker, api_url=args.api_url))
