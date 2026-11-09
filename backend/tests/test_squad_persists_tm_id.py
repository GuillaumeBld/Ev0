"""Tests pour la Tache 2 : `sync_squads` persiste les `tm_id` qu'elle produit
DEJA (source `squad`, exacte, zero requete TM supplementaire) dans le store
`player_tm_ids` (cree a la Tache 1, `app.models.player_tm_id.PlayerTmId`).

Suit le meme pattern que `test_sync_squads.py` : un moteur SQLite en memoire
REEL, `fetch_club_squad` mocke (reseau + parsing HTML), le reste (matching,
ecriture) tourne pour de vrai.

`pg_insert()` (`sqlalchemy.dialects.postgresql.insert`) ne se compile pas
contre SQLite (voir `test_import_career.py`, meme constat) : on bascule le
module `sync_squads` sur l'equivalent SQLite (API `on_conflict_do_update`
identique) uniquement pour ces tests.
"""
from __future__ import annotations

import datetime as dt
import unicodedata

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.ingestion.transfermarkt import sync_squads as sync_squads_mod
from app.ingestion.transfermarkt.squad_scraper import SquadResult, TMPlayer
from app.ingestion.transfermarkt.sync_squads import sync_squads
from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzPlayerMatchStat, BzzTeam
from app.models.canonical_teams import CanonicalTeam
from app.models.player_tm_id import PlayerTmId
from app.models.squad_sync import SquadSyncRun

TODAY = dt.date(2026, 8, 15)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


def _sqlite_unaccent(value: str | None) -> str | None:
    if value is None:
        return None
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@pytest.fixture(autouse=True)
def _use_sqlite_insert(monkeypatch):
    # pg_insert() est specifique au dialecte Postgres (cf. docstring module).
    monkeypatch.setattr(sync_squads_mod, "pg_insert", sqlite_insert)


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
        await conn.run_sync(lambda sync_conn: BzzTeam.__table__.create(sync_conn))
        await conn.run_sync(lambda sync_conn: BzzPlayer.__table__.create(sync_conn))
        await conn.run_sync(lambda sync_conn: SquadSyncRun.__table__.create(sync_conn))
        await conn.run_sync(lambda sync_conn: BzzEvent.__table__.create(sync_conn))
        await conn.run_sync(lambda sync_conn: BzzPlayerMatchStat.__table__.create(sync_conn))
        await conn.run_sync(lambda sync_conn: PlayerTmId.__table__.create(sync_conn))

    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


FAKE_CLIENT = object()


def _team(team_id: int, bzz_team_id: int, tm_club_id: int, name: str = "Club") -> CanonicalTeam:
    return CanonicalTeam(
        id=team_id, name_fr=name, bzz_team_id=bzz_team_id, transfermarkt_club_id=tm_club_id
    )


def _tm(name: str, tm_player_id: int, age: int | None = None) -> TMPlayer:
    return TMPlayer(name=name, dob=None, age=age, position="Centre-Back", tm_player_id=tm_player_id)


def _ok(club_id: int, players: list[TMPlayer]) -> SquadResult:
    return SquadResult(club_id=club_id, players=players, status="ok", raw_html="<html/>")


def _ko(club_id: int, status: str = "structure_error") -> SquadResult:
    return SquadResult(club_id=club_id, players=[], status=status, raw_html="")


def _patch_fetch(monkeypatch, by_tm_club_id: dict[int, SquadResult]) -> None:
    async def fake_fetch_club_squad(client, tm_club_id):  # noqa: ARG001
        return by_tm_club_id[tm_club_id]

    monkeypatch.setattr(
        "app.ingestion.transfermarkt.sync_squads.fetch_club_squad", fake_fetch_club_squad
    )


async def _seed(session_factory, players: list[BzzPlayer], teams: list[BzzTeam] | None = None) -> None:
    async with session_factory() as session:
        for team in teams or []:
            session.add(team)
        for player in players:
            session.add(player)
        await session.commit()


async def _get_tm_id(session_factory, player_api_id: int) -> PlayerTmId | None:
    async with session_factory() as session:
        return (
            await session.execute(
                select(PlayerTmId).where(PlayerTmId.player_api_id == player_api_id)
            )
        ).scalar_one_or_none()


# --------------------------------------------------------------------------
# Cas nominal : club OK, joueur matche -> player_tm_ids recoit la liaison.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matched_player_persists_tm_id_with_source_squad(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [BzzPlayer(api_id=678, name="Nouvelle Recrue")],
        teams=[BzzTeam(api_id=10, name="Club X FC")],
    )
    _patch_fetch(monkeypatch, {100: _ok(100, [_tm("Nouvelle Recrue", tm_player_id=12345)])})

    async with session_factory() as session:
        await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    tm_id_row = await _get_tm_id(session_factory, 678)
    assert tm_id_row is not None
    assert tm_id_row.tm_id == 12345
    assert tm_id_row.source == "squad"


# --------------------------------------------------------------------------
# Ecrasement : une liaison existante (autre source, ou meme source avec un
# tm_id perime) est ecrasee par la source 'squad' du run courant.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_squad_source_overwrites_existing_tm_id(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [BzzPlayer(api_id=678, name="Nouvelle Recrue")],
        teams=[BzzTeam(api_id=10, name="Club X FC")],
    )
    async with session_factory() as session:
        session.add(PlayerTmId(player_api_id=678, tm_id=99999, source="wikidata"))
        await session.commit()

    _patch_fetch(monkeypatch, {100: _ok(100, [_tm("Nouvelle Recrue", tm_player_id=12345)])})

    async with session_factory() as session:
        await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    tm_id_row = await _get_tm_id(session_factory, 678)
    assert tm_id_row is not None
    assert tm_id_row.tm_id == 12345
    assert tm_id_row.source == "squad"


# --------------------------------------------------------------------------
# Club KO -> aucune ecriture bzz_players pour ses joueurs, donc aucune
# liaison tm_id non plus (meme garde-fou que le reste de la synchro).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_club_writes_no_tm_id(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [BzzPlayer(api_id=6, name="Joueur Club KO", current_team_api_id=10)],
    )
    _patch_fetch(monkeypatch, {100: _ko(100)})

    async with session_factory() as session:
        await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    assert await _get_tm_id(session_factory, 6) is None


# --------------------------------------------------------------------------
# Sentinelle globale franchie -> aucune ecriture d'effectif -> aucune
# liaison tm_id persistee, meme pour un club individuellement OK.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sentinel_blocks_tm_id_writes_too(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100, name="Club X")
    club_y = _team(team_id=2, bzz_team_id=20, tm_club_id=200, name="Club Y")
    club_z = _team(team_id=3, bzz_team_id=30, tm_club_id=300, name="Club Z")
    await _seed(
        session_factory,
        [BzzPlayer(api_id=7, name="Joueur Sentinelle")],
    )
    _patch_fetch(
        monkeypatch,
        {
            100: _ok(100, [_tm("Joueur Sentinelle", tm_player_id=7777)]),
            200: _ko(200),
            300: _ko(300, status="empty"),
        },
    )

    async with session_factory() as session:
        run, _samples = await sync_squads(
            session, FAKE_CLIENT, [club_x, club_y, club_z], mode="daily", today=TODAY
        )

    assert run.status == "failed"
    assert await _get_tm_id(session_factory, 7) is None


# --------------------------------------------------------------------------
# Garde-fou tm_player_id <= 0 : jamais persiste.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_positive_tm_player_id_is_never_persisted(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [BzzPlayer(api_id=42, name="Id TM Degenere")],
        teams=[BzzTeam(api_id=10, name="Club X FC")],
    )
    _patch_fetch(monkeypatch, {100: _ok(100, [_tm("Id TM Degenere", tm_player_id=0)])})

    async with session_factory() as session:
        await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    assert await _get_tm_id(session_factory, 42) is None
