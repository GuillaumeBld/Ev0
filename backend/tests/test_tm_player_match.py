"""Tests pour `player_match.match_players` (Tache 4 : matching effectif TM ->
bzz_players par nom complet + age).

Suit le pattern etabli par `test_squad_sync_migration.py` / `test_import_career.py`
: un moteur SQLite en memoire, reel (pas de mock d'`AsyncSession`), pour
exercer le vrai chemin de requete SQL plutot que de le contourner.

`bzz_players` a ~106k lignes en production ; le module sous test pre-filtre
les candidats via `lower(unaccent(bzz_players.name))` (extension Postgres
deja utilisee ailleurs dans ce backend, voir `app.api.wc2026`). SQLite n'a
pas nativement `unaccent()` -> on enregistre ici une fonction SQL du meme nom
sur la connexion (NFD + retrait des marques combinantes, sans lowering :
meme repartition des responsabilites que cote Postgres ou `lower()` est un
appel SQL separe), afin de tester le VRAI chemin de requete du module plutot
que de le mocker.
"""
from __future__ import annotations

import datetime as dt
import unicodedata

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.ingestion.transfermarkt.player_match import match_players
from app.ingestion.transfermarkt.squad_scraper import TMPlayer
from app.models.bzzoiro import BzzPlayer

TODAY = dt.date(2026, 8, 15)


def _sqlite_unaccent(value: str | None) -> str | None:
    if value is None:
        return None
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _register_unaccent(dbapi_connection, _connection_record):
        dbapi_connection.create_function("unaccent", 1, _sqlite_unaccent)

    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: BzzPlayer.__table__.create(sync_conn))

    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _tm(name: str, age: int | None, tm_player_id: int = 1) -> TMPlayer:
    return TMPlayer(name=name, dob=None, age=age, position="Centre-Back", tm_player_id=tm_player_id)


async def _seed(session_factory, players: list[BzzPlayer]) -> None:
    async with session_factory() as session:
        for player in players:
            session.add(player)
        await session.commit()


# --------------------------------------------------------------------------
# nom + age concordants (+/-1)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matches_on_exact_name_and_age_within_tolerance(session_factory):
    # Joueur a 27 ans pile aujourd'hui (TODAY - 27 ans).
    dob = dt.date(1999, 8, 15)
    await _seed(session_factory, [BzzPlayer(api_id=100, name="Lucas Chevalier", date_of_birth=dob)])

    async with session_factory() as session:
        report = await match_players(session, [_tm("Lucas Chevalier", age=28, tm_player_id=1)], TODAY)

    assert report.matched == {1: 100}
    assert report.unmatched == []


# --------------------------------------------------------------------------
# accents : Dembélé / Dembele
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matches_accent_insensitive(session_factory):
    dob = dt.date(1997, 5, 15)  # 29 ans a TODAY
    await _seed(session_factory, [BzzPlayer(api_id=200, name="Ousmane Dembélé", date_of_birth=dob)])

    async with session_factory() as session:
        report = await match_players(session, [_tm("Ousmane Dembele", age=29, tm_player_id=2)], TODAY)

    assert report.matched == {2: 200}


# --------------------------------------------------------------------------
# homonymes, ages differents -> matche le bon
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_homonyms_different_ages_matches_correct_one(session_factory):
    young = BzzPlayer(api_id=301, name="Kevin Diaz", date_of_birth=dt.date(2005, 8, 15))  # 21 ans
    old = BzzPlayer(api_id=302, name="Kevin Diaz", date_of_birth=dt.date(1990, 8, 15))  # 36 ans
    await _seed(session_factory, [young, old])

    async with session_factory() as session:
        report = await match_players(session, [_tm("Kevin Diaz", age=22, tm_player_id=3)], TODAY)

    assert report.matched == {3: 301}


# --------------------------------------------------------------------------
# homonymes, meme nom + meme age -> unmatched (jamais de devinette)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_homonyms_same_name_and_age_is_unmatched(session_factory):
    a = BzzPlayer(api_id=401, name="Paul Martin", date_of_birth=dt.date(1998, 8, 15))  # 28 ans
    b = BzzPlayer(api_id=402, name="Paul Martin", date_of_birth=dt.date(1999, 1, 1))  # 27 ans
    await _seed(session_factory, [a, b])

    async with session_factory() as session:
        report = await match_players(session, [_tm("Paul Martin", age=27, tm_player_id=4)], TODAY)

    assert report.matched == {}
    assert report.unmatched == [_tm("Paul Martin", age=27, tm_player_id=4)]


# --------------------------------------------------------------------------
# date_of_birth NULL cote candidat -> age inconnu, non retenu
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_date_of_birth_candidate_not_retained_when_age_known(session_factory):
    await _seed(session_factory, [BzzPlayer(api_id=500, name="Jean Dupont", date_of_birth=None)])

    async with session_factory() as session:
        report = await match_players(session, [_tm("Jean Dupont", age=25, tm_player_id=5)], TODAY)

    assert report.matched == {}
    assert len(report.unmatched) == 1
    assert report.unmatched[0].tm_player_id == 5


# --------------------------------------------------------------------------
# tm.age is None + nom unique -> matche quand meme
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_age_matches_when_name_unique(session_factory):
    await _seed(session_factory, [BzzPlayer(api_id=600, name="Alassane Toure", date_of_birth=None)])

    async with session_factory() as session:
        report = await match_players(session, [_tm("Alassane Toure", age=None, tm_player_id=6)], TODAY)

    assert report.matched == {6: 600}


# --------------------------------------------------------------------------
# tm.age is None + nom NON unique -> unmatched
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_age_unmatched_when_name_not_unique(session_factory):
    a = BzzPlayer(api_id=701, name="Sofiane Bakir", date_of_birth=dt.date(1995, 8, 15))
    b = BzzPlayer(api_id=702, name="Sofiane Bakir", date_of_birth=dt.date(2001, 3, 3))
    await _seed(session_factory, [a, b])

    async with session_factory() as session:
        report = await match_players(session, [_tm("Sofiane Bakir", age=None, tm_player_id=7)], TODAY)

    assert report.matched == {}
    assert len(report.unmatched) == 1
    assert report.unmatched[0].tm_player_id == 7
