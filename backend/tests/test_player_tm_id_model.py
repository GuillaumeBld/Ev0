"""Jalon 1 (contournement rate-limit Transfermarkt) : store player_tm_ids.

Liaison joueur -> identifiant Transfermarkt, tracable par source ('squad' ou
'wikidata'). Fondation lue par les jalons suivants (effectifs, Wikidata) et
par l'import carriere, qui saute la recherche quand une ligne existe deja.

Moteur SQLite en memoire (meme procede que tests/test_player_career_model.py) :
pas de fixture partagee `db_session` dans ce repo, chaque module de test
cree son propre engine/session autour des tables qu'il exerce.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.bzzoiro import BzzPlayer
from app.models.player_tm_id import PlayerTmId


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        # bzz_players doit exister pour que SQLite compile la FK de
        # player_tm_ids.player_api_id, meme si SQLite ne l'applique pas.
        for table in (BzzPlayer.__table__, PlayerTmId.__table__):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_player_tm_id_insert_and_reread(db_session):
    row = PlayerTmId(player_api_id=2253, tm_id=342229, source="squad")
    db_session.add(row)
    await db_session.commit()

    got = (
        await db_session.execute(
            select(PlayerTmId).where(PlayerTmId.player_api_id == 2253)
        )
    ).scalar_one()
    assert got.tm_id == 342229
    assert got.source == "squad"
    assert got.updated_at is not None


@pytest.mark.asyncio
async def test_player_tm_id_one_row_per_player(db_session):
    db_session.add(PlayerTmId(player_api_id=99, tm_id=111, source="wikidata"))
    await db_session.commit()

    # PK = player_api_id : une deuxieme ligne pour le meme joueur n'est pas
    # permise, le jalon suivant doit faire un update, pas un insert.
    with pytest.raises(Exception):
        db_session.add(PlayerTmId(player_api_id=99, tm_id=222, source="squad"))
        await db_session.commit()
