"""Tests de l'elargissement de la file carriere a l'univers Atlas et de ses
vagues de priorite (Task 4).

`wave_of` est une fonction pure : testee sans base. `players_needing_career`
touche une vraie requete SQL (UNION canonical_teams / player_selection_
season_stats) : on la verifie contre une base SQLite en memoire plutot
qu'un mock de session, pour que le test porte sur le SQL reellement execute.
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
from app.ingestion.career_priority import FOLLOWED_SELECTION_IDS, wave_of
from app.models.bzzoiro import BzzPlayer
from app.models.canonical_teams import CanonicalTeam
from app.models.career_import import CareerImportAttempt
from app.models.player_career import PlayerCareerSeason
from app.models.selection_stats import PlayerSelectionSeasonStat

NOW = dt.datetime(2026, 11, 9, 3, 0, tzinfo=dt.UTC)


# `canonical_teams.aliases` est un `ARRAY(Text)` (type Postgres). SQLite n'a
# pas d'equivalent : on le compile comme `JSON` uniquement dans ce module de
# test (meme pattern que `test_sync_squads.py` pour `JSONB`), afin de pouvoir
# creer reellement la table sans toucher au modele de production.
@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


# --------------------------------------------------------------------------
# wave_of — fonction pure
# --------------------------------------------------------------------------

def test_wave_of_ligue_suivie():
    assert wave_of(6) == 1


def test_wave_of_competition_de_coupe():
    assert wave_of(7) == 2


def test_wave_of_nation_mondial():
    assert wave_of(27) == 1


def test_wave_of_aucune_ligue():
    assert wave_of(None) == 2


def test_followed_selection_ids():
    assert frozenset({27, 64, 65}) == FOLLOWED_SELECTION_IDS


# --------------------------------------------------------------------------
# players_needing_career — ordre par vagues sur une vraie base (SQLite)
# --------------------------------------------------------------------------

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
async def test_vague_1_club_avant_vague_2_club(session_factory):
    """A egalite de tentatives, un club de ligue 6 (vague 1) sort avant un
    club de ligue 7 (vague 2, competition de coupe)."""
    async with session_factory() as session:
        await _seed_club_player(
            session, api_id=1001, team_api_id=501, league_api_id=7, name="Joueur Coupe"
        )
        await _seed_club_player(
            session, api_id=1002, team_api_id=502, league_api_id=6, name="Joueur Ligue1"
        )
        await session.commit()

        queue = await players_needing_career(session, limit=10, now=NOW)

    ids = [p["player_api_id"] for p in queue]
    assert ids.index(1002) < ids.index(1001), (
        "le joueur de ligue 6 (vague 1) doit sortir avant celui de ligue 7 "
        "(vague 2, competition de coupe)"
    )


@pytest.mark.asyncio
async def test_joueur_selection_sans_club_integre_est_couvert(session_factory):
    """Un joueur present uniquement via player_selection_season_stats (pas de
    club dans canonical_teams) doit etre dans l'univers elargi, en vague 1
    (Mondial)."""
    async with session_factory() as session:
        await session.execute(
            insert(BzzPlayer).values(
                api_id=2001,
                name="Joueur Selection",
                nationality="MA",
                date_of_birth=dt.date(1999, 5, 5),
                current_team_api_id=None,
                current_team_name=None,
            )
        )
        await session.execute(
            insert(PlayerSelectionSeasonStat).values(
                player_api_id=2001,
                national_team_api_id=777,
                league_api_id=27,
                season="2026",
                source="bzzoiro",
            )
        )
        await session.commit()

        queue = await players_needing_career(session, limit=10, now=NOW)

    ids = [p["player_api_id"] for p in queue]
    assert 2001 in ids


@pytest.mark.asyncio
async def test_joueur_deja_tente_recemment_reste_exclu(session_factory):
    """Le garde-fou anti-martelement (should_retry / career_import_attempts)
    n'est pas affaibli par l'elargissement de l'univers."""
    async with session_factory() as session:
        await _seed_club_player(
            session, api_id=3001, team_api_id=601, league_api_id=6, name="Deja Tente"
        )
        await session.execute(
            insert(CareerImportAttempt).values(
                player_api_id=3001,
                attempted_at=NOW - dt.timedelta(hours=1),
                outcome="error",
                attempts=1,
            )
        )
        await session.commit()

        queue = await players_needing_career(session, limit=10, now=NOW)

    ids = [p["player_api_id"] for p in queue]
    assert 3001 not in ids


@pytest.mark.asyncio
async def test_joueur_avec_carriere_existante_reste_exclu(session_factory):
    """Un joueur qui a deja une carriere en base ne revient pas dans la file,
    meme elargie."""
    async with session_factory() as session:
        await _seed_club_player(
            session, api_id=4001, team_api_id=701, league_api_id=6, name="Deja En Base"
        )
        await session.execute(
            insert(PlayerCareerSeason).values(
                player_api_id=4001,
                season="2024-2025",
                competition_code="FR1",
                appearances=10,
                goals=1,
                assists=0,
                minutes=900,
            )
        )
        await session.commit()

        queue = await players_needing_career(session, limit=10, now=NOW)

    ids = [p["player_api_id"] for p in queue]
    assert 4001 not in ids


@pytest.mark.asyncio
async def test_limit_respecte_apres_tri_par_vague(session_factory):
    """Le plafond `limit` s'applique bien APRES le tri par vague, pas avant
    (sinon la vague 2 pourrait saturer la limite avant que la vague 1 ne soit
    drainee)."""
    async with session_factory() as session:
        # Deux joueurs de vague 2 (coupe), un de vague 1 (ligue suivie).
        await _seed_club_player(
            session, api_id=5001, team_api_id=801, league_api_id=7, name="Coupe A"
        )
        await _seed_club_player(
            session, api_id=5002, team_api_id=802, league_api_id=7, name="Coupe B"
        )
        await _seed_club_player(
            session, api_id=5003, team_api_id=803, league_api_id=6, name="Ligue1 A"
        )
        await session.commit()

        queue = await players_needing_career(session, limit=1, now=NOW)

    assert [p["player_api_id"] for p in queue] == [5003]
