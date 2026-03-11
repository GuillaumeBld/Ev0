"""import_understat_rosters.py — import Understat player minutes into PlayerMatchMinutes table.

Run inside the backend container:
    docker exec -e PYTHONPATH=/app <container> python /tmp/import_understat_rosters.py

Reads /tmp/understat_rosters.json and correlates each match with a DB fixture
by home_team + away_team + kickoff_date (with team name normalization).
Inserts PlayerMatchMinutes rows (skips existing on conflict).
"""

import asyncio
import json
from datetime import date, timedelta, timezone, datetime as dt

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import async_session
from app.models.fixtures import Fixture
from app.models.player_match_minutes import PlayerMatchMinutes

DATA_PATH = "/tmp/understat_rosters.json"

TEAM_NAME_MAP = {
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


def norm_team(name: str) -> str:
    n = name.lower().strip()
    return TEAM_NAME_MAP.get(n, n)


def teams_match(understat_name: str, db_name: str) -> bool:
    return norm_team(understat_name) == db_name.lower().strip()


async def find_fixture(session, home: str, away: str, match_date_str: str) -> Fixture | None:
    """Find a DB fixture by team names + date (window: -1 day to +2 days to handle timezone shifts)."""
    match_date = date.fromisoformat(match_date_str)
    # Load fixtures in -1/+2 day window then match by name
    from_dt = dt(match_date.year, match_date.month, match_date.day, tzinfo=timezone.utc) - timedelta(days=1)
    to_dt = dt(match_date.year, match_date.month, match_date.day, tzinfo=timezone.utc) + timedelta(days=2)
    result = await session.execute(
        select(Fixture).where(
            Fixture.kickoff_utc >= from_dt,
            Fixture.kickoff_utc <= to_dt,
            Fixture.status == "finished",
        )
    )
    fixtures = result.scalars().all()
    for fx in fixtures:
        if teams_match(home, fx.home_team) and teams_match(away, fx.away_team):
            return fx
    return None


async def main():
    with open(DATA_PATH) as f:
        rosters: dict = json.load(f)

    print(f"Loaded {len(rosters)} matches from {DATA_PATH}")

    total_inserted = 0
    total_skipped = 0
    not_found = 0

    async with async_session() as session:
        for mid, match in rosters.items():
            home = match["home"]
            away = match["away"]
            match_date = match["date"]
            players = match["players"]

            fixture = await find_fixture(session, home, away, match_date)
            if fixture is None:
                print(f"  SKIP: no fixture for {home} vs {away} ({match_date})")
                not_found += 1
                continue

            inserted = 0
            for p in players:
                if not p["name"]:
                    continue
                # Use INSERT ... ON CONFLICT DO NOTHING for idempotency
                stmt = pg_insert(PlayerMatchMinutes).values(
                    fixture_id=fixture.id,
                    player_name=p["name"],
                    minutes_played=p["minutes"],
                ).on_conflict_do_nothing(constraint="uq_player_match_minutes")
                result = await session.execute(stmt)
                if result.rowcount > 0:
                    inserted += 1
                else:
                    total_skipped += 1

            total_inserted += inserted
            print(f"  {home} vs {away} ({match_date}) fixture={fixture.id}: {inserted} rows inserted")

        await session.commit()

    print(f"\nDone — {total_inserted} rows inserted, {total_skipped} skipped (already exist), {not_found} matches not found in DB")


asyncio.run(main())
