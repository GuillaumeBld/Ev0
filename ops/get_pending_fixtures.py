"""get_pending_fixtures.py — list fixtures needing Understat roster data for auto-settle.

Run inside the backend container:
    docker exec -e PYTHONPATH=/app <container> python3 /tmp/get_pending_fixtures.py

Outputs JSON list of {fixture_id, league, home, away, date} for fixtures that:
- Have at least one approved rec with result=NULL
- Fixture status is 'finished'
- No PlayerMatchMinutes exist yet for the fixture
"""

import asyncio
import json
from datetime import timezone

from sqlalchemy import select

from app.db import async_session
from app.models.fixtures import Fixture
from app.models.player_match_minutes import PlayerMatchMinutes
from app.models.recommendations import Recommendation


async def main():
    async with async_session() as db:
        # Get distinct finished fixtures with unsettled approved recs
        stmt = (
            select(Fixture)
            .join(Recommendation, Recommendation.fixture_id == Fixture.id)
            .where(
                Recommendation.status == "approved",
                Recommendation.result.is_(None),
                Fixture.status == "finished",
            )
            .distinct()
        )
        fixtures = (await db.execute(stmt)).scalars().all()

        result = []
        for fx in fixtures:
            # Skip if PlayerMatchMinutes already imported for this fixture
            pmm = await db.execute(
                select(PlayerMatchMinutes)
                .where(PlayerMatchMinutes.fixture_id == fx.id)
                .limit(1)
            )
            if pmm.scalar_one_or_none() is not None:
                continue

            result.append({
                "fixture_id": fx.id,
                "league": fx.league,
                "home": fx.home_team,
                "away": fx.away_team,
                "date": fx.kickoff_utc.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            })

    print(json.dumps(result))


asyncio.run(main())
