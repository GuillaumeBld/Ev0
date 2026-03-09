#!/usr/bin/env python3
"""fetch_sofascore.py — récupère les stats Sofascore via Playwright.

Usage : python ops/fetch_sofascore.py [output.json]
Output par défaut : /tmp/sofascore_data.json
"""

import asyncio
import json
import sys

from playwright.async_api import async_playwright

OUTPUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/sofascore_data.json"

# tournament_id / season_id 2025-26
# Endpoint : www.sofascore.com/api/v1/unique-tournament/{tid}/season/{sid}/statistics
LEAGUES = {
    "ligue_1":        {"tid": 34,  "sid": 77356, "label": "Ligue 1 25/26"},
    "premier_league": {"tid": 17,  "sid": 76986, "label": "Premier League 25/26"},
    # Bundesliga / La Liga / Serie A — season IDs à confirmer quand actifs dans l'app
    # "bundesliga":   {"tid": 35, "sid": ???, "label": "Bundesliga 25/26"},
    # "la_liga":      {"tid": 8,  "sid": ???, "label": "La Liga 25/26"},
    # "serie_a":      {"tid": 23, "sid": ???, "label": "Serie A 25/26"},
}

STAT_FIELDS = (
    "goals,assists,keyPasses,bigChancesCreated,"
    "accurateCrosses,totalCrosses,"
    "shotsOnTarget,totalShots,"
    "touchesInAttackPenaltyArea,throughBalls,"
    "minutesPlayed,appearances,rating"
)
BASE = "https://www.sofascore.com/api/v1/unique-tournament"
PAGE_SIZE = 100
MAX_PLAYERS = 500
RATE_LIMIT = 1.2  # secondes entre pages


async def fetch_league(page, key: str, cfg: dict) -> list[dict]:
    players = []
    offset = 0
    while offset < MAX_PLAYERS:
        url = (
            f"{BASE}/{cfg['tid']}/season/{cfg['sid']}/statistics"
            f"?limit={PAGE_SIZE}&offset={offset}"
            f"&order=-rating&fields={STAT_FIELDS}&accumulation=total"
        )
        resp = await page.request.get(url)
        if resp.status != 200:
            print(f"  [{key}] HTTP {resp.status} à offset {offset} — arrêt")
            break
        data = await resp.json()
        results = data.get("results", [])
        if not results:
            break
        players.extend(results)
        offset += PAGE_SIZE
        if len(results) < PAGE_SIZE:
            break
        await asyncio.sleep(RATE_LIMIT)
    return players


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # Charger la page principale pour obtenir les cookies Cloudflare
        await page.goto("https://www.sofascore.com/", wait_until="domcontentloaded", timeout=20000)

        all_data: dict[str, list] = {}
        for key, cfg in LEAGUES.items():
            print(f"Fetching {cfg['label']}...")
            results = await fetch_league(page, key, cfg)
            all_data[key] = results
            print(f"  → {len(results)} joueurs")
            await asyncio.sleep(2)

        await browser.close()

    with open(OUTPUT, "w") as f:
        json.dump(all_data, f)

    total = sum(len(v) for v in all_data.values())
    print(f"\nSauvegardé dans {OUTPUT} ({total} records)")


asyncio.run(main())
