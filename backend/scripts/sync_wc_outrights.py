#!/usr/bin/env python3
"""Sync WC2026 outright odds (top scorer, winner…) depuis Unibet, PMU et Betclic.

À lancer en LOCAL (IP résidentielle FR) — les bookmakers bloquent les IP datacenter.

Prérequis Betclic :
    pip install playwright && playwright install chromium

Usage :
    python scripts/sync_wc_outrights.py [--dry-run] [--bookmaker unibet|pmu|betclic]

Exemples :
    python scripts/sync_wc_outrights.py --dry-run
    python scripts/sync_wc_outrights.py
    python scripts/sync_wc_outrights.py --bookmaker unibet
    python scripts/sync_wc_outrights.py --bookmaker pmu
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

# Allow running from repo root or scripts/ dir
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_DEFAULT_DSN = "postgresql://ev0:eqv2pWEYjMchXWAVVouiAb4nD2uKBug@213.130.144.204:5432/ev0"


async def main(dry_run: bool, bookmaker: str | None) -> None:
    from collections import Counter

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from app.ingestion.wc2026.sync_wc_outrights import (
        scrape_betclic_wc_outrights,
        scrape_pmu_wc_outrights,
        scrape_unibet_wc_outrights,
        store_wc_outrights,
    )

    dsn = os.environ.get("DATABASE_URL", _DEFAULT_DSN)
    async_dsn = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(async_dsn, echo=False)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Choix des scrapers à lancer
    scrapers: list[tuple[str, object]] = []
    if bookmaker in (None, "unibet"):
        scrapers.append(("unibet", scrape_unibet_wc_outrights()))
    if bookmaker in (None, "pmu"):
        scrapers.append(("pmu", scrape_pmu_wc_outrights()))
    if bookmaker in (None, "betclic"):
        scrapers.append(("betclic", scrape_betclic_wc_outrights()))

    all_results: list[dict] = []
    for name, coro in scrapers:
        logger.info("Scraping %s…", name)
        try:
            rows = await coro  # type: ignore[misc]
            logger.info("%s: %d cotes", name, len(rows))
            all_results.extend(rows)
        except Exception as exc:
            logger.error("%s failed: %s", name, exc)

    if not all_results:
        logger.warning("Aucune cote récupérée.")
        return

    summary = Counter(r["market_type"] for r in all_results)
    logger.info("Résumé par market_type: %s", dict(summary))
    by_book = Counter(r["bookmaker"] for r in all_results)
    logger.info("Résumé par bookmaker: %s", dict(by_book))

    if dry_run:
        logger.info("--dry-run: pas de stockage. Aperçu (%d rows):", len(all_results))
        for row in all_results[:10]:
            logger.info("  %s", row)
        return

    async with AsyncSessionLocal() as session:
        await store_wc_outrights(session, all_results)
    logger.info("✓ %d cotes upsertées dans wc2026_outright_odds", len(all_results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync WC2026 outright odds locally")
    parser.add_argument("--dry-run", action="store_true", help="Affiche les cotes sans stocker")
    parser.add_argument(
        "--bookmaker",
        choices=["unibet", "pmu", "betclic"],
        help="Limiter à un seul bookmaker",
    )
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, bookmaker=args.bookmaker))
