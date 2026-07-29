"""Tests for the career import script (parsing/matching, DB mocked)."""
from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.scripts.import_career import (
    ImportStats,
    _normalize_name,
    find_player_api_id,
    import_career_data,
)


def _make_session_with_rows(rows: list[tuple[int, str]]) -> MagicMock:
    """Mock AsyncSession whose execute().all() returns the given (api_id, name) rows."""
    result = MagicMock()
    result.all.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


# --------------------------------------------------------------------------
# _normalize_name
# --------------------------------------------------------------------------

def test_normalize_name_strips_accents_and_case():
    assert _normalize_name("Dro Fernández") == _normalize_name("dro fernandez")


def test_normalize_name_collapses_whitespace():
    assert _normalize_name("  Lucas   Chevalier ") == _normalize_name("Lucas Chevalier")


# --------------------------------------------------------------------------
# find_player_api_id
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_player_api_id_matches_by_name_and_dob():
    session = _make_session_with_rows([(555, "Lucas Chevalier")])
    api_id = await find_player_api_id(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert api_id == 555


@pytest.mark.asyncio
async def test_find_player_api_id_matches_accent_insensitive():
    session = _make_session_with_rows([(777, "Dro Fernandez")])
    api_id = await find_player_api_id(session, "Dro Fernández", dt.date(2008, 1, 12))
    assert api_id == 777


@pytest.mark.asyncio
async def test_find_player_api_id_no_dob_match_returns_none():
    session = _make_session_with_rows([])
    api_id = await find_player_api_id(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert api_id is None


@pytest.mark.asyncio
async def test_find_player_api_id_dob_matches_but_name_differs_returns_none():
    # Same birthdate, different person -> must not guess.
    session = _make_session_with_rows([(1, "Someone Else")])
    api_id = await find_player_api_id(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert api_id is None


@pytest.mark.asyncio
async def test_find_player_api_id_ambiguous_multiple_name_matches_returns_none():
    # Two bzz_players rows share the dob AND normalized name -> too ambiguous, skip.
    session = _make_session_with_rows([(1, "Lucas Chevalier"), (2, "Lucas Chevalier")])
    api_id = await find_player_api_id(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert api_id is None


# --------------------------------------------------------------------------
# import_career_data
# --------------------------------------------------------------------------

def _matched_player(seasons: list[dict] | None = None) -> dict:
    return {
        "input_name": "Lucas Chevalier",
        "input_nationality": "France",
        "input_dob": "2001-11-06",
        "input_club": "Paris Saint-Germain",
        "matched": True,
        "tm_id": "463600",
        "matched_name": "Lucas Chevalier",
        "seasons": seasons
        if seasons is not None
        else [
            {
                "appearances": 17,
                "goals": 0,
                "assists": 0,
                "minutes": 1530,
                "season": "25/26",
                "season_start_year": 2025,
                "competition_code": "FR1",
                "competition": "Ligue 1",
            }
        ],
    }


def _unmatched_player() -> dict:
    return {
        "input_name": "Dro Fernández",
        "input_nationality": "Spain",
        "input_dob": "2008-01-12",
        "input_club": "Paris Saint-Germain",
        "matched": False,
        "tm_id": None,
        "matched_name": None,
        "seasons": [],
    }


@pytest.mark.asyncio
async def test_import_career_data_maps_matched_player_and_upserts_seasons():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.scripts.import_career.find_player_api_id", new=AsyncMock(return_value=42)
    ) as mock_find:
        stats = await import_career_data(session, [_matched_player()])

    mock_find.assert_awaited_once_with(session, "Lucas Chevalier", dt.date(2001, 11, 6))
    assert stats.players_matched == 1
    assert stats.seasons_upserted == 1
    assert stats.players_skipped_not_matched == 0
    assert stats.players_skipped_unresolved == 0
    session.execute.assert_awaited_once()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_import_career_data_skips_unmatched_player():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.scripts.import_career.find_player_api_id", new=AsyncMock()
    ) as mock_find:
        stats = await import_career_data(session, [_unmatched_player()])

    mock_find.assert_not_awaited()
    session.execute.assert_not_awaited()
    assert stats.players_skipped_not_matched == 1
    assert stats.players_matched == 0
    assert stats.seasons_upserted == 0


@pytest.mark.asyncio
async def test_import_career_data_skips_when_no_bzz_player_found():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.scripts.import_career.find_player_api_id", new=AsyncMock(return_value=None)
    ):
        stats = await import_career_data(session, [_matched_player()])

    session.execute.assert_not_awaited()
    assert stats.players_skipped_unresolved == 1
    assert stats.players_matched == 0
    assert stats.seasons_upserted == 0


@pytest.mark.asyncio
async def test_import_career_data_skips_matched_player_missing_dob():
    player = _matched_player()
    player["input_dob"] = None
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    with patch(
        "app.scripts.import_career.find_player_api_id", new=AsyncMock()
    ) as mock_find:
        stats = await import_career_data(session, [player])

    mock_find.assert_not_awaited()
    assert stats.players_skipped_unresolved == 1


@pytest.mark.asyncio
async def test_import_career_data_is_idempotent_stats_type():
    assert isinstance(ImportStats(), ImportStats)


# --------------------------------------------------------------------------
# Idempotence end-to-end contre une vraie contrainte unique : le bug d'origine
# est un comportement SQL (NULL != NULL dans une contrainte UNIQUE), donc un
# mock de session ne peut pas le detecter -> il faut une vraie DB. SQLite en
# memoire suit la meme semantique ANSI que Postgres pour les contraintes
# UNIQUE (NULL non-egal a NULL), c'est donc un remplacant fidele ici.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_twice_with_null_competition_code_does_not_duplicate(monkeypatch):
    """Deux imports successifs d'un joueur avec une saison competition_code=None
    (match amical, pas de competitionId Transfermarkt) ne doivent PAS produire
    de doublon en base : la 2e ecriture doit mettre a jour la ligne existante,
    pas en creer une nouvelle. Avant le fix (competition_code=None non
    coalesce), NULL != NULL cote SQL empechait ON CONFLICT de matcher -> 2
    lignes, compteurs de stats gonfles.
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    import app.scripts.import_career as import_career_mod
    from app.models.player_career import PlayerCareerSeason

    # pg_insert() est specifique au dialecte Postgres et ne se compile pas
    # contre SQLite ; on bascule sur l'equivalent SQLite (API
    # on_conflict_do_update identique) uniquement pour ce test.
    monkeypatch.setattr(import_career_mod, "pg_insert", sqlite_insert)

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: PlayerCareerSeason.__table__.create(sync_conn))

        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        player = _matched_player(
            seasons=[
                {
                    "appearances": 2,
                    "goals": 1,
                    "assists": 0,
                    "minutes": 90,
                    "season": "19/20",
                    "season_start_year": 2019,
                    "competition_code": None,  # amical, pas de competitionId TM
                    "competition": None,
                }
            ]
        )

        async def _import_once() -> ImportStats:
            async with session_factory() as session:
                with patch(
                    "app.scripts.import_career.find_player_api_id",
                    new=AsyncMock(return_value=42),
                ):
                    return await import_career_data(session, [player])

        stats1 = await _import_once()
        stats2 = await _import_once()

        assert stats1.seasons_upserted == 1
        assert stats2.seasons_upserted == 1
        assert stats1.players_matched == 1
        assert stats2.players_matched == 1

        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(PlayerCareerSeason).where(
                        PlayerCareerSeason.player_api_id == 42,
                        PlayerCareerSeason.season == "19/20",
                    )
                )
            ).scalars().all()

        assert len(rows) == 1, f"attendu 1 ligne, trouve {len(rows)} -> doublon (regression du bug NULL)"
        row = rows[0]
        assert row.competition_code == ""  # coalesce, jamais NULL
        # Le 2e import doit avoir MIS A JOUR la ligne existante, pas cumule
        # les compteurs (compteurs stables entre les 2 runs, pas doubles).
        assert row.appearances == 2
        assert row.goals == 1
        assert row.minutes == 90
    finally:
        await engine.dispose()
