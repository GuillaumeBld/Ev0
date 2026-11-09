"""Jalon 5/5 (contournement rate-limit Transfermarkt) : la file d'import
carriere priorise le chemin rapide (`known_tm_id` present, 0 recherche) avant
le repli par recherche (rate-limite).

`players_needing_career` fait deja un tri Python par vague (Task 4) : ce
module verifie que le critere PRIMAIRE devient « a un `known_tm_id` », la
vague restant secondaire. Meme patron de fixture (SQLite en memoire) que
`test_career_priority.py`, pour que le test porte sur l'ordre reellement
produit par la fonction plutot que sur un mock.
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.ingestion.career_import import players_needing_career
from app.models.bzzoiro import BzzPlayer
from app.models.canonical_teams import CanonicalTeam
from app.models.career_import import CareerImportAttempt
from app.models.player_career import PlayerCareerSeason
from app.models.player_tm_id import PlayerTmId
from app.models.selection_stats import PlayerSelectionSeasonStat

NOW = dt.datetime(2026, 11, 9, 3, 0, tzinfo=dt.UTC)


# `canonical_teams.aliases` est un `ARRAY(Text)` (type Postgres) : SQLite n'a
# pas d'equivalent, meme patron que `test_career_priority.py`.
@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for table in (
            BzzPlayer.__table__,
            CanonicalTeam.__table__,
            PlayerSelectionSeasonStat.__table__,
            CareerImportAttempt.__table__,
            PlayerCareerSeason.__table__,
            PlayerTmId.__table__,
        ):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_club_player(
    session, *, api_id: int, team_api_id: int, league_api_id: int, name: str
) -> None:
    await session.execute(
        insert(CanonicalTeam).values(
            name_fr=f"club-{team_api_id}",
            bzz_team_id=team_api_id,
            league_api_id=league_api_id,
        )
    )
    await session.execute(
        insert(BzzPlayer).values(
            api_id=api_id,
            name=name,
            nationality="FR",
            date_of_birth=dt.date(2000, 1, 1),
            current_team_api_id=team_api_id,
            current_team_name=f"club-{team_api_id}",
        )
    )


@pytest.mark.asyncio
async def test_tm_id_connu_sort_avant_vague_1_sans_tm_id(session_factory):
    """Le chemin rapide (tm_id connu) prime sur la vague : meme en vague 2,
    un joueur a tm_id connu sort avant un joueur de vague 1 sans tm_id, et
    `limit=1` retient bien celui-ci."""
    async with session_factory() as session:
        # Vague 2 (competition de coupe, league_api_id=7) mais tm_id connu.
        await _seed_club_player(
            session, api_id=9001, team_api_id=901, league_api_id=7, name="TmId Connu"
        )
        await session.execute(
            insert(PlayerTmId).values(
                player_api_id=9001, tm_id=342229, source="squad"
            )
        )
        # Vague 1 (ligue suivie, league_api_id=6) mais sans tm_id connu.
        await _seed_club_player(
            session, api_id=9002, team_api_id=902, league_api_id=6, name="Sans TmId"
        )
        await session.commit()

        queue = await players_needing_career(session, limit=1, now=NOW)

    assert [p["player_api_id"] for p in queue] == [9001], (
        "le joueur a tm_id connu doit sortir en premier (chemin rapide non "
        "rate-limite), meme s'il est en vague 2 et l'autre en vague 1"
    )


@pytest.mark.asyncio
async def test_deux_tm_id_connus_restent_ordonnes_par_vague(session_factory):
    """Entre deux joueurs a tm_id connu, la vague reste le critere
    secondaire : vague 1 avant vague 2."""
    async with session_factory() as session:
        await _seed_club_player(
            session, api_id=9101, team_api_id=911, league_api_id=7, name="TmId Vague 2"
        )
        await session.execute(
            insert(PlayerTmId).values(
                player_api_id=9101, tm_id=111111, source="squad"
            )
        )
        await _seed_club_player(
            session, api_id=9102, team_api_id=912, league_api_id=6, name="TmId Vague 1"
        )
        await session.execute(
            insert(PlayerTmId).values(
                player_api_id=9102, tm_id=222222, source="squad"
            )
        )
        await session.commit()

        queue = await players_needing_career(session, limit=10, now=NOW)

    ids = [p["player_api_id"] for p in queue]
    assert ids.index(9102) < ids.index(9101), (
        "entre deux joueurs a tm_id connu, celui de vague 1 doit sortir "
        "avant celui de vague 2"
    )
