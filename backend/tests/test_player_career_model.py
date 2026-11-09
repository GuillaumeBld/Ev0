"""Tache 1 (plan complement carriere Transfermarkt) : enrichissement du
modele PlayerCareerSeason au gabarit Bzzoiro complet + colonne `source`.

Moteur SQLite en memoire (meme procede que tests/test_aggregate_selection.py) :
pas de fixture partagee `db_session` dans ce repo, chaque module de test
cree son propre engine/session autour des tables qu'il exerce.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.bzzoiro import BzzPlayer
from app.models.player_career import PlayerCareerSeason


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        # bzz_players doit exister pour que SQLite compile la FK de
        # player_career_seasons.player_api_id, meme si SQLite ne l'applique pas.
        for table in (BzzPlayer.__table__, PlayerCareerSeason.__table__):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_career_season_stores_gabarit_fields(db_session):
    row = PlayerCareerSeason(
        player_api_id=2253, tm_id=342229, season="15/16",
        season_start_year=2015, competition_code="FR1", competition="Ligue 1",
        appearances=26, goals=12, assists=5, minutes=1941,
        starts=22, total_shots=70, shots_on_target=31,
        total_pass=900, accurate_pass=760, total_tackle=18, won_tackle=11,
        yellow_card=3, red_card=0, fouls_committed=20, fouls_suffered=35, offsides=14,
        source="transfermarkt",
    )
    db_session.add(row)
    await db_session.commit()
    got = (await db_session.execute(
        select(PlayerCareerSeason).where(PlayerCareerSeason.player_api_id == 2253)
    )).scalar_one()
    assert got.shots_on_target == 31
    assert got.fouls_suffered == 35
    assert got.source == "transfermarkt"
    # champ non renseigne -> NULL, pas 0
    got.total_shots = None
    await db_session.commit()
    assert got.total_shots is None
