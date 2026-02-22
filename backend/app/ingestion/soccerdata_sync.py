import pandas as pd
import soccerdata as sd
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.fixtures import generate_fixture_id, normalize_team_name
from app.ingestion.player_stats import normalize_player_name
from app.ingestion.storage import store_player_stats, upsert_fixture, upsert_player


async def sync_fbref_fixtures(session: AsyncSession, leagues: list[str]):
    """Sync fixtures using soccerdata FBref."""
    fbref = sd.FBref(leagues=leagues, seasons="2425")
    schedule = fbref.read_schedule()

    for (league, _season, _game), row in schedule.iterrows():
        fixture_data = {
            "fixture_id": generate_fixture_id(
                str(row["date"]),
                normalize_team_name(row["home_team"]),
                normalize_team_name(row["away_team"]),
            ),
            "league": league.lower().replace(" ", "_"),
            "season": "2024-2025",
            "date": str(row["date"]),
            "time": str(row["time"]) if pd.notnull(row.get("time")) else "00:00",
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "home_score": int(row["home_score"]) if pd.notnull(row.get("home_score")) else None,
            "away_score": int(row["away_score"]) if pd.notnull(row.get("away_score")) else None,
        }
        await upsert_fixture(session, fixture_data)


async def sync_fbref_player_stats(session: AsyncSession, leagues: list[str]):
    """Sync player stats using soccerdata FBref."""
    fbref = sd.FBref(leagues=leagues, seasons="2425")

    # Read different stat types
    shooting = fbref.read_player_season_stats(stat_type="shooting")
    passing = fbref.read_player_season_stats(stat_type="passing")

    # Join and iterate
    stats_combined = shooting.join(passing, lsuffix="_shoot", rsuffix="_pass")

    for (league, _season, team, player), row in stats_combined.iterrows():
        # 1. Upsert player
        player_obj = await upsert_player(
            session,
            {"player_name": player, "team": team, "normalized_name": normalize_player_name(player)},
        )

        # 2. Store stats
        await store_player_stats(
            session,
            player_obj.id,
            league.lower(),
            "2024-2025",
            {
                "minutes": row.get("minutes_shoot", 0),
                "goals": row.get("goals", 0),
                "xg": row.get("xg_shoot", 0.0),
                "assists": row.get("assists", 0),
                "xa": row.get("xg_assist", 0.0),
                "xg_per_90": row.get("xg_per90", 0.0),
            },
        )
