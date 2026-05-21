"""Fetch correct API-Football team IDs and update canonical_teams.

Uses the official API-Football v3 API (api-sports.io).
Free tier: 100 requests/day — enough to cover all our leagues (6 leagues × 1 req = 6 requests).

Get a free key at: https://dashboard.api-sports.io (no credit card needed)

Usage:
    # Set your API key
    export API_FOOTBALL_KEY=your_key_here

    # Dry-run (show changes without writing)
    python scripts/fix_api_football_ids.py --dry-run

    # Apply changes
    python scripts/fix_api_football_ids.py
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
import unicodedata
import httpx

# API-Football league IDs (v3, https://v3.football.api-sports.io)
LEAGUES = {
    "Premier League":   (39, 2024),
    "Ligue 1":          (61, 2024),
    "Bundesliga":       (78, 2024),
    "La Liga":          (140, 2024),
    "Serie A":          (135, 2024),
    "Champions League": (2, 2024),
}

BASE = "https://v3.football.api-sports.io"


def normalize(s: str) -> str:
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower().strip()


async def fetch_league_teams(
    client: httpx.AsyncClient,
    league_id: int,
    season: int,
    api_key: str,
) -> list[dict]:
    url = f"{BASE}/teams"
    headers = {"x-apisports-key": api_key}
    params = {"league": league_id, "season": season}
    r = await client.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    errors = data.get("errors", {})
    if errors:
        raise ValueError(f"API error: {errors}")
    return data.get("response") or []


async def main(dry_run: bool, api_key: str) -> None:
    mapping: dict[str, tuple[int, str]] = {}  # normalized name → (api_football_id, canonical_name)

    async with httpx.AsyncClient() as client:
        for league_name, (league_id, season) in LEAGUES.items():
            print(f"\n=== {league_name} (league {league_id}, season {season}) ===")
            try:
                teams = await fetch_league_teams(client, league_id, season, api_key)
                for entry in teams:
                    team = entry.get("team", {})
                    apif_id = team.get("id")
                    name = team.get("name", "")
                    if not apif_id:
                        continue
                    print(f"  {name:40s} → {apif_id}")
                    mapping[normalize(name)] = (apif_id, name)
                time.sleep(0.35)  # ≤ 3 req/s free tier limit
            except Exception as e:
                print(f"  ERROR: {e}")

    print(f"\n\nCollected {len(mapping)} name→id entries from API-Football\n")

    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    import asyncpg
    from app.config import settings

    db_url = str(settings.database_url).replace("postgresql+asyncpg", "postgresql")
    conn = await asyncpg.connect(db_url)
    rows = await conn.fetch(
        "SELECT id, name_fr, name_en, aliases, api_football_id FROM canonical_teams ORDER BY name_fr"
    )

    updates: list[tuple[int, int, str, str]] = []
    unmatched: list[str] = []

    for row in rows:
        ct_id = row["id"]
        name_fr = row["name_fr"]
        name_en = row["name_en"] or ""
        aliases = row["aliases"] or []
        current_id = row["api_football_id"]

        candidates = [name_fr, name_en] + list(aliases)
        found = None
        matched_via = None
        for c in candidates:
            if not c:
                continue
            nk = normalize(c)
            if nk in mapping:
                found = mapping[nk]
                matched_via = c
                break

        if found:
            new_id, apif_name = found
            if new_id != current_id:
                updates.append((ct_id, new_id, name_fr, f"{matched_via} → {apif_name}"))
                print(f"  UPDATE: {name_fr:30s}  {str(current_id or '?'):>6} → {new_id}  (via '{matched_via}')")
            else:
                print(f"  OK    : {name_fr:30s}  {current_id}")
        else:
            unmatched.append(name_fr)

    print(f"\n{len(updates)} updates, {len(unmatched)} unmatched")
    if unmatched:
        print("Unmatched (fix manually in /dashboard/teams):")
        for u in unmatched:
            print(f"  - {u}")

    if not dry_run and updates:
        print(f"\nApplying {len(updates)} updates...")
        async with conn.transaction():
            # Nullify all IDs first to avoid unique constraint conflicts during shuffle
            for ct_id, _, _, _ in updates:
                await conn.execute(
                    "UPDATE canonical_teams SET api_football_id = NULL WHERE id = $1",
                    ct_id,
                )
            # Then assign correct IDs
            for ct_id, new_id, _, _ in updates:
                await conn.execute(
                    "UPDATE canonical_teams SET api_football_id = $1 WHERE id = $2",
                    new_id, ct_id,
                )
        print("Done.")
    elif dry_run:
        print("\n[DRY RUN — no changes written]")

    await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--api-key", default=os.environ.get("API_FOOTBALL_KEY", ""), help="API-Football key (or set API_FOOTBALL_KEY env var)")
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: provide --api-key or set API_FOOTBALL_KEY env var")
        print("  Free key at: https://dashboard.api-sports.io (no credit card needed)")
        raise SystemExit(1)

    asyncio.run(main(args.dry_run, args.api_key))
