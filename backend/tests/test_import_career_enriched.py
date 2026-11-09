"""Tache 3 (plan complement carriere Transfermarkt) : l'import doit ecrire
les 12 nouvelles colonnes du gabarit (au-dela des 4 historiques) + `source`,
et le re-run (upsert) doit les mettre a jour sur une ligne ancienne.

Moteur SQLite en memoire (meme procede que tests/test_player_career_model.py
et tests/test_import_career.py) : pas de fixture partagee `db_session` dans
ce repo, chaque module de test cree son propre engine/session.
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


def _gabarit_season(**overrides) -> dict:
    season = {
        "appearances": 26,
        "goals": 12,
        "assists": 5,
        "minutes": 1941,
        "season": "15/16",
        "season_start_year": 2015,
        "competition_code": "FR1",
        "competition": "Ligue 1",
        "starts": 22,
        "total_shots": 70,
        "shots_on_target": 31,
        "total_pass": 900,
        "accurate_pass": 760,
        "total_tackle": 18,
        "won_tackle": 11,
        "yellow_card": 3,
        "red_card": 0,
        "fouls_committed": 20,
        "fouls_suffered": 35,
        "offsides": 14,
    }
    season.update(overrides)
    return season


def _matched_player(seasons: list[dict]) -> dict:
    return {
        "input_name": "Lucas Chevalier",
        "input_nationality": "France",
        "input_dob": "2001-11-06",
        "input_club": "Paris Saint-Germain",
        "matched": True,
        "tm_id": "463600",
        "matched_name": "Lucas Chevalier",
        "seasons": seasons,
    }


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
                    name="Lucas Chevalier",
                    date_of_birth=dt.date(2001, 11, 6),
                )
            )
            await session.commit()
            yield session
        await engine.dispose()


@pytest.mark.asyncio
async def test_import_persists_enriched_gabarit_fields(db_session):
    player = _matched_player([_gabarit_season()])

    stats = await import_career_data(db_session, [player])
    assert stats.seasons_upserted == 1

    row = (
        await db_session.execute(
            select(PlayerCareerSeason).where(PlayerCareerSeason.player_api_id == 42)
        )
    ).scalar_one()

    assert row.starts == 22
    assert row.total_shots == 70
    assert row.shots_on_target == 31
    assert row.total_pass == 900
    assert row.accurate_pass == 760
    assert row.total_tackle == 18
    assert row.won_tackle == 11
    assert row.yellow_card == 3
    assert row.red_card == 0
    assert row.fouls_committed == 20
    assert row.fouls_suffered == 35
    assert row.offsides == 14
    assert row.source == "transfermarkt"


@pytest.mark.asyncio
async def test_reimport_updates_enriched_fields_on_existing_row(db_session):
    """Regression : une ligne ancienne restee a 4 champs (NULL partout
    ailleurs) doit etre corrigee par un re-import, pas laissee intacte."""
    stale_row = PlayerCareerSeason(
        player_api_id=42,
        tm_id=463600,
        season="15/16",
        season_start_year=2015,
        competition_code="FR1",
        competition="Ligue 1",
        appearances=1,
        goals=0,
        assists=0,
        minutes=10,
        source="transfermarkt",
    )
    db_session.add(stale_row)
    await db_session.commit()

    player = _matched_player([_gabarit_season()])
    stats = await import_career_data(db_session, [player])
    assert stats.seasons_upserted == 1

    # L'upsert est un INSERT Core, pas un flush ORM : la map d'identite de
    # cette session garde encore `stale_row` en memoire tel qu'avant l'ecriture.
    # On force un rechargement depuis la base pour verifier ce qui y est
    # vraiment, plutot que l'objet Python perime.
    db_session.expire_all()

    row = (
        await db_session.execute(
            select(PlayerCareerSeason).where(PlayerCareerSeason.player_api_id == 42)
        )
    ).scalar_one()

    assert row.appearances == 26
    assert row.shots_on_target == 31
    assert row.fouls_suffered == 35
    assert row.offsides == 14
    assert row.source == "transfermarkt"


@pytest.mark.asyncio
async def test_missing_gabarit_key_does_not_crash_and_stays_unknown(db_session):
    """Un bucket d'agregation qui ne fournirait pas une cle (normalement
    toujours presente, a 0 sinon) ne doit jamais faire planter l'ecriture.
    La colonne est nullable precisement pour ne jamais deviner 0 a la place
    d'une valeur inconnue (cf. commentaire du modele) -> cle absente == NULL,
    pas 0."""
    season = _gabarit_season()
    del season["offsides"]
    player = _matched_player([season])

    stats = await import_career_data(db_session, [player])
    assert stats.seasons_upserted == 1

    row = (
        await db_session.execute(
            select(PlayerCareerSeason).where(PlayerCareerSeason.player_api_id == 42)
        )
    ).scalar_one()
    assert row.offsides is None
