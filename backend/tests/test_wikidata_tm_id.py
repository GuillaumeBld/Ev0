"""Tests pour la Tache 3 (jalon 3/5 contournement rate-limit Transfermarkt) :
resolveur Wikidata (`app.ingestion.wikidata_tm_id`).

`match_and_store` ne fait aucun appel reseau (la liste Wikidata est passee
en parametre) : tests avec un moteur SQLite en memoire REEL, meme pattern
que `test_player_tm_id_model.py`.
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.ingestion.wikidata_tm_id import match_and_store, normalize_name
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
        for table in (BzzPlayer.__table__, PlayerTmId.__table__):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _get_tm_id(session, player_api_id: int) -> PlayerTmId | None:
    return (
        await session.execute(
            select(PlayerTmId).where(PlayerTmId.player_api_id == player_api_id)
        )
    ).scalar_one_or_none()


# --------------------------------------------------------------------------
# Cas nominal : nom + dob uniques des deux cotes -> match, source='wikidata'.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unique_match_persists_wikidata_tm_id(db_session):
    db_session.add(
        BzzPlayer(
            api_id=101, name="Kevin De Bruyne", date_of_birth=dt.date(1991, 6, 28)
        )
    )
    await db_session.commit()

    rows = [{"tm_id": 88755, "name": "Kevin De Bruyne", "dob": "1991-06-28"}]

    stats = await match_and_store(db_session, rows)

    tm_row = await _get_tm_id(db_session, 101)
    assert tm_row is not None
    assert tm_row.tm_id == 88755
    assert tm_row.source == "wikidata"
    assert stats["matched"] == 1
    assert stats["skipped_no_dob"] == 0
    assert stats["skipped_ambiguous"] == 0
    assert stats["skipped_squad_exists"] == 0


# --------------------------------------------------------------------------
# Homonyme : 2 entrees Wikidata memes nom+dob -> ambigu, non retenu.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_same_name_and_dob_is_not_retained(db_session):
    db_session.add(
        BzzPlayer(api_id=202, name="John Smith", date_of_birth=dt.date(1995, 3, 10))
    )
    await db_session.commit()

    rows = [
        {"tm_id": 11111, "name": "John Smith", "dob": "1995-03-10"},
        {"tm_id": 22222, "name": "John Smith", "dob": "1995-03-10"},
    ]

    stats = await match_and_store(db_session, rows)

    assert await _get_tm_id(db_session, 202) is None
    assert stats["matched"] == 0
    assert stats["skipped_ambiguous"] == 1


# --------------------------------------------------------------------------
# source='squad' deja presente -> jamais ecrasee par 'wikidata'.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_squad_source_is_never_overwritten(db_session):
    db_session.add(
        BzzPlayer(api_id=303, name="Erling Haaland", date_of_birth=dt.date(2000, 7, 21))
    )
    db_session.add(PlayerTmId(player_api_id=303, tm_id=418560, source="squad"))
    await db_session.commit()

    rows = [{"tm_id": 999999, "name": "Erling Haaland", "dob": "2000-07-21"}]

    stats = await match_and_store(db_session, rows)

    tm_row = await _get_tm_id(db_session, 303)
    assert tm_row is not None
    assert tm_row.tm_id == 418560
    assert tm_row.source == "squad"
    assert stats["matched"] == 0
    assert stats["skipped_squad_exists"] == 1


# --------------------------------------------------------------------------
# bzz_player sans date_of_birth -> jamais retenu (on ne devine pas).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_player_without_dob_is_not_retained(db_session):
    db_session.add(BzzPlayer(api_id=404, name="Sans Date", date_of_birth=None))
    await db_session.commit()

    rows = [{"tm_id": 55555, "name": "Sans Date", "dob": "1990-01-01"}]

    stats = await match_and_store(db_session, rows)

    assert await _get_tm_id(db_session, 404) is None
    assert stats["matched"] == 0
    assert stats["skipped_no_dob"] == 1


# --------------------------------------------------------------------------
# Normalisation des accents : "Zlatko Tripić" (bzz) == "Zlatko Tripic" (wikidata).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accent_normalization_matches(db_session):
    db_session.add(
        BzzPlayer(api_id=505, name="Zlatko Tripić", date_of_birth=dt.date(1992, 1, 13))
    )
    await db_session.commit()

    rows = [{"tm_id": 71838, "name": "Zlatko Tripic", "dob": "1992-01-13"}]

    stats = await match_and_store(db_session, rows)

    tm_row = await _get_tm_id(db_session, 505)
    assert tm_row is not None
    assert tm_row.tm_id == 71838
    assert tm_row.source == "wikidata"
    assert stats["matched"] == 1


def test_normalize_name_strips_accents_case_and_spaces():
    assert normalize_name("Zlatko Tripić") == normalize_name("  Zlatko   Tripic ")
    assert normalize_name("ÉLAN  Foo") == "elan foo"


# --------------------------------------------------------------------------
# Dob mismatch (meme nom, dob differente) -> aucun match (pas un bug ambigu,
# simplement aucun candidat a cette cle).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dob_mismatch_yields_no_match(db_session):
    db_session.add(
        BzzPlayer(api_id=606, name="Someone Else", date_of_birth=dt.date(1988, 5, 5))
    )
    await db_session.commit()

    rows = [{"tm_id": 33333, "name": "Someone Else", "dob": "1990-05-05"}]

    stats = await match_and_store(db_session, rows)

    assert await _get_tm_id(db_session, 606) is None
    assert stats["matched"] == 0
    assert stats["skipped_no_dob"] == 0
    assert stats["skipped_ambiguous"] == 0


# --------------------------------------------------------------------------
# Existant source='wikidata' (deja ecrit par un run precedent) -> mise a
# jour normale (pas une collision squad), stats = matched.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_wikidata_source_is_updated(db_session):
    db_session.add(
        BzzPlayer(api_id=707, name="Updated Guy", date_of_birth=dt.date(1993, 2, 2))
    )
    db_session.add(PlayerTmId(player_api_id=707, tm_id=1, source="wikidata"))
    await db_session.commit()

    rows = [{"tm_id": 999, "name": "Updated Guy", "dob": "1993-02-02"}]

    stats = await match_and_store(db_session, rows)

    tm_row = await _get_tm_id(db_session, 707)
    assert tm_row is not None
    assert tm_row.tm_id == 999
    assert tm_row.source == "wikidata"
    assert stats["matched"] == 1


@pytest.mark.asyncio
async def test_fetch_skips_failed_page_without_truncating(monkeypatch):
    """Une page en echec definitif (None) est SAUTEE : les pages suivantes sont
    quand meme collectees (pas d'arret premature qui tronquerait le jeu)."""
    import app.ingestion.wikidata_tm_id as wd

    page0 = [{"tmid": {"value": "10"}, "name": {"value": "A"}, "dob": {"value": "1990-01-01"}}]
    page2 = [{"tmid": {"value": "20"}, "name": {"value": "B"}, "dob": {"value": "1991-02-02"}}]
    # offset 0 -> page pleine ; offset PAGE_SIZE -> echec (None) ; offset 2*PAGE_SIZE -> page pleine ; puis vide
    responses = {0: page0, wd.PAGE_SIZE: None, 2 * wd.PAGE_SIZE: page2, 3 * wd.PAGE_SIZE: []}

    # page pleine = PAGE_SIZE lignes pour forcer la poursuite ; on duplique.
    responses[0] = page0 * wd.PAGE_SIZE
    responses[2 * wd.PAGE_SIZE] = page2 * wd.PAGE_SIZE

    async def fake_fetch_page(client, offset):
        return responses.get(offset, [])

    monkeypatch.setattr(wd, "_fetch_page", fake_fetch_page)

    rows = await wd.fetch_wikidata_players()
    tmids = {r["tm_id"] for r in rows}
    # Les deux pages valides sont collectees malgre la page du milieu en echec.
    assert 10 in tmids and 20 in tmids


@pytest.mark.asyncio
async def test_fetch_stops_after_too_many_consecutive_failures(monkeypatch):
    """Garde-fou : si trop de pages consecutives echouent, la pagination s'arrete
    (pas de boucle infinie contre un endpoint HS)."""
    import app.ingestion.wikidata_tm_id as wd

    async def always_fail(client, offset):
        return None

    monkeypatch.setattr(wd, "_fetch_page", always_fail)
    rows = await wd.fetch_wikidata_players()
    assert rows == []
