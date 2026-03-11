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

# Team name map: Understat names (lowercase) → DB names (lowercase)
# Used for --fixtures mode to correlate Understat matches with DB fixtures
_TEAM_NAME_MAP = {
    "manchester city": "man city",
    "manchester united": "man utd",
    "nottingham forest": "nott'm forest",
    "newcastle united": "newcastle",
    "wolverhampton wanderers": "wolves",
    "tottenham hotspur": "tottenham",
    "brighton & hove albion": "brighton",
    "west ham united": "west ham",
    "leicester city": "leicester",
    "ipswich town": "ipswich",
    "paris saint-germain": "psg",
    "olympique de marseille": "marseille",
    "olympique lyonnais": "lyon",
    "stade de reims": "reims",
    "stade brestois 29": "brest",
    "rc strasbourg alsace": "strasbourg",
    "stade rennais fc": "rennes",
    "fc nantes": "nantes",
    "ogc nice": "nice",
    "montpellier hsc": "montpellier",
    "rc lens": "lens",
    "toulouse fc": "toulouse",
    "borussia dortmund": "dortmund",
    "bayer leverkusen": "leverkusen",
    "eintracht frankfurt": "frankfurt",
    "vfb stuttgart": "stuttgart",
    "sc freiburg": "freiburg",
    "1. fc union berlin": "union berlin",
    "1. fsv mainz 05": "mainz",
    "fc augsburg": "augsburg",
    "1. fc heidenheim 1846": "heidenheim",
    "sv werder bremen": "werder bremen",
    "borussia mönchengladbach": "gladbach",
    "vfl wolfsburg": "wolfsburg",
    "vfl bochum": "bochum",
    "fc st. pauli": "st. pauli",
    "atletico madrid": "atlético madrid",
    "deportivo alaves": "alavés",
    "leganes": "leganés",
    "real valladolid": "valladolid",
    "ac milan": "milan",
    "hellas verona": "verona",
}


def _norm(name: str) -> str:
    return _TEAM_NAME_MAP.get(name.lower().strip(), name.lower().strip())


def _teams_match(us_home: str, us_away: str, db_home: str, db_away: str) -> bool:
    return _norm(us_home) == db_home.lower().strip() and _norm(us_away) == db_away.lower().strip()


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
    parser.add_argument(
        "--fixtures", type=str, default=None,
        help="JSON file with list of {fixture_id, league, home, away, date} — targeted mode"
    )
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

        if args.fixtures:
            # Targeted mode: only fetch specific fixtures
            with open(args.fixtures) as f:
                pending = json.load(f)

            if not pending:
                print("No pending fixtures.")
                await browser.close()
                with open(args.output, "w") as f:
                    json.dump({}, f)
                return

            # Group by league
            by_league: dict[str, list] = {}
            for fx in pending:
                by_league.setdefault(fx["league"], []).append(fx)

            for league_key, fixtures in by_league.items():
                league_cfg = LEAGUES.get(league_key)
                if not league_cfg:
                    print(f"  SKIP: unknown league '{league_key}'")
                    continue
                slug, year = league_cfg
                print(f"\n=== {league_key} — {len(fixtures)} fixture(s) to fetch ===")
                dates_data = await get_dates_data(page, slug, year)
                finished = {mid: m for mid, m in dates_data.items() if m.get("isResult")}

                for fx in fixtures:
                    # Find matching Understat match by date + team names
                    match_ref = None
                    for mid, m in finished.items():
                        us_home = m.get("h", {}).get("title", "")
                        us_away = m.get("a", {}).get("title", "")
                        dt_str = m.get("datetime", "")[:10]
                        if dt_str == fx["date"] and _teams_match(us_home, us_away, fx["home"], fx["away"]):
                            match_ref = (mid, us_home, us_away, dt_str)
                            break

                    if match_ref is None:
                        print(f"  SKIP: no Understat match for {fx['home']} vs {fx['away']} ({fx['date']})")
                        continue

                    mid, home, away, dt_str = match_ref
                    if mid in results:
                        continue

                    print(f"  {home} vs {away} ({dt_str}) id={mid}")
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

        else:
            # Full mode: fetch all leagues (used for manual backfill)
            for league_key, (slug, year) in LEAGUES.items():
                print(f"\n=== {league_key} ({slug}/{year}) ===")
                dates_data = await get_dates_data(page, slug, year)

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
                    dt_str = m.get("datetime", "")[:10]

                    if mid in results:
                        continue

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
