#!/usr/bin/env python3
"""fetch_understat_rosters.py — scrape per-match player minutes from Understat via Playwright.

Understat is a React SPA. HTML is a 27KB shell; data is in window.datesData / window.rostersData
after JavaScript renders. Playwright renders the SPA and we evaluate JS to get the data.

Usage:
    python ops/fetch_understat_rosters.py [output.json]
    python ops/fetch_understat_rosters.py --limit 20  # fetch only first 20 matches (for testing)

Output: /tmp/understat_rosters.json (or specified path)
"""

import argparse
import asyncio
import json
import re

from playwright.async_api import async_playwright

OUTPUT = "/tmp/understat_rosters.json"
RATE_LIMIT = 2.0  # seconds between requests

# Understat league slugs
LEAGUES = {
    "ligue_1": ("Ligue_1", 2025),
    "premier_league": ("EPL", 2025),
    "bundesliga": ("Bundesliga", 2025),
    "la_liga": ("La_liga", 2025),
    "serie_a": ("Serie_A", 2025),
}


async def get_dates_data(page, slug: str, year: int) -> dict:
    """Fetch datesData for a league/season via Playwright JS evaluation."""
    url = f"https://understat.com/league/{slug}/{year}"
    print(f"  Loading {url}...")
    await page.goto(url, wait_until="networkidle", timeout=60000)
    # Understat injects data into window variables after React mounts
    data = await page.evaluate("() => window.datesData ? JSON.parse(JSON.stringify(window.datesData)) : null")
    if data is None:
        # Fallback: try to extract from script tags (old embed format)
        content = await page.content()
        match = re.search(r"datesData\s*=\s*JSON\.parse\('(.+?)'\)", content)
        if match:
            raw = match.group(1).encode().decode("unicode_escape")
            data = json.loads(raw)
    # datesData is a list of match objects — convert to dict keyed by match id
    if isinstance(data, list):
        data = {m["id"]: m for m in data if "id" in m}
    return data or {}


async def get_rosters_data(page, understat_id: str) -> dict:
    """Fetch rostersData for a single match via Playwright JS evaluation."""
    url = f"https://understat.com/match/{understat_id}"
    await page.goto(url, wait_until="networkidle", timeout=60000)
    data = await page.evaluate("() => window.rostersData ? JSON.parse(JSON.stringify(window.rostersData)) : null")
    if data is None:
        # Fallback: try to extract from script tags
        content = await page.content()
        match = re.search(r"rostersData\s*=\s*JSON\.parse\('(.+?)'\)", content)
        if match:
            raw = match.group(1).encode().decode("unicode_escape")
            data = json.loads(raw)
    return data or {}


def parse_roster(rosters_data: dict, home: str, away: str, match_date: str) -> dict:
    """Convert raw rostersData into our output format."""
    players = []
    for side in ("h", "a"):
        side_data = rosters_data.get(side, {})
        for _pid, p in side_data.items():
            players.append({
                "name": p.get("player", ""),
                "team_side": side,
                "minutes": int(p.get("time", 0) or 0),
                "goals": int(p.get("goals", 0) or 0),
                "assists": int(p.get("assists", 0) or 0),
            })
    return {
        "home": home,
        "away": away,
        "date": match_date,
        "players": players,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=OUTPUT)
    parser.add_argument("--limit", type=int, default=None, help="Max matches per league (for testing)")
    args = parser.parse_args()

    results: dict[str, dict] = {}

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

        for league_key, (slug, year) in LEAGUES.items():
            print(f"\n=== {league_key} ({slug}/{year}) ===")
            dates_data = await get_dates_data(page, slug, year)

            # Filter to finished matches only
            finished = {
                mid: m for mid, m in dates_data.items()
                if m.get("isResult")
            }
            print(f"  {len(finished)} finished matches found")

            match_ids = list(finished.keys())
            if args.limit:
                match_ids = match_ids[:args.limit]

            for i, mid in enumerate(match_ids):
                m = finished[mid]
                home = m.get("h", {}).get("title", "")
                away = m.get("a", {}).get("title", "")
                dt_str = m.get("datetime", "")[:10]  # "2025-08-17"

                if mid in results:
                    continue  # already fetched

                print(f"  [{i+1}/{len(match_ids)}] {home} vs {away} ({dt_str}) id={mid}")
                try:
                    rosters_data = await get_rosters_data(page, mid)
                    if not rosters_data:
                        print(f"    WARNING: empty rostersData for match {mid}")
                        continue
                    results[mid] = parse_roster(rosters_data, home, away, dt_str)
                    print(f"    → {len(results[mid]['players'])} players")
                except Exception as e:
                    print(f"    ERROR: {e}")
                    continue

                await asyncio.sleep(RATE_LIMIT)

        await browser.close()

    with open(args.output, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\nSaved {len(results)} matches to {args.output}")


asyncio.run(main())
