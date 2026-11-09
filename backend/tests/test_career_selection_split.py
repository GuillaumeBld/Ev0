"""Tache 6 (plan complement carriere Transfermarkt) : separer le compartiment
Selection (matchs en equipe nationale) du compartiment Club a partir du meme
flux Transfermarkt.

Un match porte `gameInformation.isNationalGame` (booleen). Un bucket
(saison, competition) est "selection" si TOUS ses matchs sont internationaux
(`all()` sur les matchs JOUES du bucket) -> `is_national` sur
`player_career_seasons`.

Moteur SQLite en memoire (meme procede que tests/test_player_career_model.py
et tests/test_import_career_enriched.py) : pas de fixture partagee
`db_session` dans ce repo, chaque module de test cree son propre engine/session.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.scripts.import_career as import_career_mod
from app.models.bzzoiro import BzzPlayer
from app.models.player_career import PlayerCareerSeason
from app.scripts.import_career import import_career_data
from app.scripts.transfermarkt_career import aggregate_by_season_and_competition


def _game(*, competition_id: str, is_national: bool | None, **stats) -> dict:
    base = {
        "gameInformation": {
            "seasonId": 2024,
            "competitionId": competition_id,
            "season": {"display": "24/25"},
            "isNationalGame": is_national,
        },
        "statistics": {"generalStatistics": {"participationState": "played"}},
    }
    base["statistics"].update(stats)
    return base


def _club_game(**overrides) -> dict:
    return _game(
        competition_id="FR1",
        is_national=False,
        goalStatistics={"goalsScoredTotal": 1, "assists": 0},
        playingTimeStatistics={"playedMinutes": 90, "isStarting": True},
        **overrides,
    )


def _national_game(**overrides) -> dict:
    return _game(
        competition_id="EURO",
        is_national=True,
        goalStatistics={"goalsScoredTotal": 0, "assists": 1},
        playingTimeStatistics={"playedMinutes": 90, "isStarting": True},
        **overrides,
    )


def test_aggregation_splits_national_bucket_from_club_bucket():
    """Un games melant un match isNationalGame=True (comp EURO) et des
    matchs club -> 2 buckets, celui EURO a is_national=True, celui club
    is_national=False."""
    games = [
        _national_game(),
        _club_game(),
        _club_game(),
    ]

    out = aggregate_by_season_and_competition(client=None, games=games, cache={"competition_names": {}})

    assert len(out) == 2
    by_competition = {bucket["competition_code"]: bucket for bucket in out}

    assert by_competition["EURO"]["is_national"] is True
    assert by_competition["FR1"]["is_national"] is False


def test_mixed_bucket_with_one_non_national_match_is_not_national():
    """Un bucket avec un seul match non international, meme code de
    competition EURO (cas d'erreur de flux) -> is_national=False : ne jamais
    marquer selection a tort. `all()` sur un bucket non vide."""
    games = [
        _national_game(),
        _game(
            competition_id="EURO",
            is_national=False,
            goalStatistics={"goalsScoredTotal": 0, "assists": 0},
            playingTimeStatistics={"playedMinutes": 90, "isStarting": False},
        ),
    ]

    out = aggregate_by_season_and_competition(client=None, games=games, cache={"competition_names": {}})

    assert len(out) == 1
    assert out[0]["is_national"] is False


def test_missing_game_information_defaults_to_not_national():
    """Acces defensif : gameInformation absent ou isNationalGame absent ->
    traite comme non international, jamais une exception."""
    games = [
        {
            "gameInformation": {"seasonId": 2024, "competitionId": "FR1",
                                 "season": {"display": "24/25"}},
            "statistics": {"generalStatistics": {"participationState": "played"},
                           "goalStatistics": {"goalsScoredTotal": 1, "assists": 0},
                           "playingTimeStatistics": {"playedMinutes": 90, "isStarting": True}},
        },
    ]

    out = aggregate_by_season_and_competition(client=None, games=games, cache={"competition_names": {}})

    assert len(out) == 1
    assert out[0]["is_national"] is False


@pytest.fixture
async def db_session():
    # pg_insert() est specifique au dialecte Postgres et ne se compile pas
    # contre SQLite ; on bascule sur l'equivalent SQLite (API
    # on_conflict_do_update identique) uniquement pour ces tests.
    with patch.object(import_career_mod, "pg_insert", sqlite_insert):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            for table in (BzzPlayer.__table__, PlayerCareerSeason.__table__):
                await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add(
                BzzPlayer(
                    api_id=42,
                    name="Kylian Mbappe",
                    date_of_birth=dt.date(1998, 12, 20),
                )
            )
            await session.commit()
            yield session
        await engine.dispose()


@pytest.mark.asyncio
async def test_import_writes_distinct_national_and_club_rows(db_session):
    """Apres import, la ligne selection (EURO, is_national=True) est lisible
    et separee de la ligne club (FR1, is_national=False)."""
    player = {
        "input_name": "Kylian Mbappe",
        "input_nationality": "France",
        "input_dob": "1998-12-20",
        "input_club": "Paris Saint-Germain",
        "matched": True,
        "tm_id": "342229",
        "matched_name": "Kylian Mbappe",
        "seasons": [
            {
                "season": "24/25", "season_start_year": 2024,
                "competition_code": "EURO", "competition": "European Championship",
                "appearances": 1, "goals": 0, "assists": 1, "minutes": 90,
                "is_national": True,
            },
            {
                "season": "24/25", "season_start_year": 2024,
                "competition_code": "FR1", "competition": "Ligue 1",
                "appearances": 2, "goals": 2, "assists": 0, "minutes": 180,
                "is_national": False,
            },
        ],
    }

    stats = await import_career_data(db_session, [player])
    assert stats.seasons_upserted == 2

    rows = (
        await db_session.execute(
            select(PlayerCareerSeason).where(PlayerCareerSeason.player_api_id == 42)
        )
    ).scalars().all()
    by_code = {row.competition_code: row for row in rows}

    assert by_code["EURO"].is_national is True
    assert by_code["FR1"].is_national is False


@pytest.mark.asyncio
async def test_season_bucket_without_is_national_key_defaults_to_false(db_session):
    """Un bucket qui n'apporterait pas la cle `is_national` (defensif) ->
    False, jamais True devine."""
    player = {
        "input_name": "Kylian Mbappe",
        "input_nationality": "France",
        "input_dob": "1998-12-20",
        "input_club": "Paris Saint-Germain",
        "matched": True,
        "tm_id": "342229",
        "matched_name": "Kylian Mbappe",
        "seasons": [
            {
                "season": "24/25", "season_start_year": 2024,
                "competition_code": "FR1", "competition": "Ligue 1",
                "appearances": 2, "goals": 2, "assists": 0, "minutes": 180,
            },
        ],
    }

    stats = await import_career_data(db_session, [player])
    assert stats.seasons_upserted == 1

    row = (
        await db_session.execute(
            select(PlayerCareerSeason).where(PlayerCareerSeason.player_api_id == 42)
        )
    ).scalar_one()
    assert row.is_national is False
