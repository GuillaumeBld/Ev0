"""Tests Tache 5 : agregation des stats de match de selection nationale
(bzz_player_match_stats, ligues 27 Coupe du monde / 64-65 Ligue des Nations)
vers la table isolee player_selection_season_stats.

Moteur SQLite EN MEMOIRE REEL (meme procede que
tests/test_selection_nationality.py et tests/test_expansion_ingestion.py) :
canonical_teams.aliases (ARRAY) et bzz_events.{shotmap,...} (JSONB) sont des
types Postgres que SQLite ne sait pas compiler nativement ; on enregistre,
UNIQUEMENT dans ce module de test, des compilateurs DDL qui les font passer
pour JSON generique.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.ingestion.bzzoiro.aggregate_selection import aggregate_selection_stats
from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzPlayerMatchStat
from app.models.canonical_teams import CanonicalTeam
from app.models.selection_stats import PlayerSelectionSeasonStat

WORLD_CUP_LEAGUE_ID = 27
SEASON = "2025-2026"


@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for table in (
            CanonicalTeam.__table__,
            BzzPlayer.__table__,
            BzzEvent.__table__,
            BzzPlayerMatchStat.__table__,
            PlayerSelectionSeasonStat.__table__,
        ):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _mid_season_date() -> datetime:
    """Date dans la fenetre de SEASON (1er aout 2025 inclus -> 1er aout 2026 exclu)."""
    return datetime(2025, 12, 1, tzinfo=UTC)


@pytest.fixture
async def seed_wc_match_stats(db_session):
    """Equipe nationale canonique France (bzz_team_id=1000, ligue 27) ;
    joueur api_id=50 nationality="France" ; 2 evenements ligue 27
    status='finished' dans la fenetre de saison ; 2 bzz_player_match_stats
    (goals 1 et 2, minutes 90 et 90)."""
    db_session.add(
        CanonicalTeam(
            name_fr="France",
            name_en="France",
            league_api_id=WORLD_CUP_LEAGUE_ID,
            bzz_team_id=1000,
        )
    )
    db_session.add(BzzPlayer(api_id=50, name="Joueur 50", nationality="France"))

    base = _mid_season_date()
    db_session.add(
        BzzEvent(
            api_id=9001,
            league_api_id=WORLD_CUP_LEAGUE_ID,
            event_date=base,
            status="finished",
        )
    )
    db_session.add(
        BzzEvent(
            api_id=9002,
            league_api_id=WORLD_CUP_LEAGUE_ID,
            event_date=base + timedelta(days=4),
            status="finished",
        )
    )
    await db_session.flush()

    db_session.add(
        BzzPlayerMatchStat(
            player_api_id=50,
            event_api_id=9001,
            minutes_played=90,
            goals=1,
            goal_assist=0,
            rating=7.0,
        )
    )
    db_session.add(
        BzzPlayerMatchStat(
            player_api_id=50,
            event_api_id=9002,
            minutes_played=90,
            goals=2,
            goal_assist=1,
            rating=8.0,
        )
    )
    await db_session.commit()


@pytest.fixture
async def seed_wc_unmapped_player(db_session):
    """Joueur nationality="Wakanda" (aucune equipe canonique correspondante)
    avec des stats de match sur un evenement de selection -> ne doit produire
    aucune ligne."""
    db_session.add(BzzPlayer(api_id=52, name="Joueur 52", nationality="Wakanda"))

    base = _mid_season_date()
    db_session.add(
        BzzEvent(
            api_id=9003,
            league_api_id=WORLD_CUP_LEAGUE_ID,
            event_date=base,
            status="finished",
        )
    )
    await db_session.flush()

    db_session.add(
        BzzPlayerMatchStat(
            player_api_id=52,
            event_api_id=9003,
            minutes_played=90,
            goals=3,
            goal_assist=0,
            rating=9.0,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_aggregates_selection_match_stats(db_session, seed_wc_match_stats):
    n = await aggregate_selection_stats(db_session, season=SEASON)
    assert n == 1
    row = (
        await db_session.execute(
            select(PlayerSelectionSeasonStat).where(
                PlayerSelectionSeasonStat.player_api_id == 50
            )
        )
    ).scalars().one()
    assert row.national_team_api_id == 1000
    assert row.league_api_id == WORLD_CUP_LEAGUE_ID
    assert row.source == "bzzoiro"
    assert row.goals == 3
    assert row.matches_played == 2


@pytest.mark.asyncio
async def test_unmapped_player_skipped(db_session, seed_wc_unmapped_player):
    n = await aggregate_selection_stats(db_session, season=SEASON)
    assert n == 0
    rows = (await db_session.execute(select(PlayerSelectionSeasonStat))).scalars().all()
    assert rows == []
