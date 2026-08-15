"""Tests pour `sync_squads.sync_squads` (Tache 5 : orchestration complete de
la reconciliation d'effectifs Transfermarkt -> bzz_players).

Suit le meme pattern que `test_tm_player_match.py` : un moteur SQLite en
memoire REEL (pas de mock d'`AsyncSession`), avec la fonction `unaccent`
enregistree sur la connexion (necessaire ici aussi car `sync_squads` delegue
le matching a `match_players`, qui l'utilise).

`squad_sync_runs.detail` est une colonne JSONB (type Postgres). SQLite n'a
pas de type "JSONB" ; on enregistre un compilateur DDL local qui la fait
compiler comme `JSON` (type generique reconnu par SQLite) UNIQUEMENT dans ce
module de test, afin de pouvoir creer et exercer reellement la table
`squad_sync_runs` sans toucher au modele de production (qui reste JSONB pour
la vraie base Postgres, voir `test_squad_sync_migration.py` qui documente le
meme constat).

`fetch_club_squad` (reseau + parsing HTML) est mocke : on injecte des
`SquadResult` deja construits, cle par `tm_club_id`. Rien d'autre n'est
mocke : `match_players` tourne pour de vrai contre la base SQLite, exactement
comme en production.

Les `CanonicalTeam` passes en `clubs=` ne sont JAMAIS ajoutes a la session
(seuls leurs attributs Python `id` / `bzz_team_id` / `transfermarkt_club_id`
sont lus par `sync_squads`) : pas besoin de creer la table `canonical_teams`
(qui a de toute facon une colonne `ARRAY(Text)` non compilable sous SQLite).
"""
from __future__ import annotations

import datetime as dt
import unicodedata

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.ingestion.transfermarkt.squad_scraper import SquadResult, TMPlayer
from app.ingestion.transfermarkt.sync_squads import sync_squads
from app.models.bzzoiro import BzzPlayer, BzzTeam
from app.models.canonical_teams import CanonicalTeam
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

    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


# A fake TransfermarktClient sentinel: fetch_club_squad is always monkeypatched
# in these tests, so no real client behaviour is ever exercised.
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


async def _get_player(session_factory, api_id: int) -> BzzPlayer:
    async with session_factory() as session:
        return (
            await session.execute(select(BzzPlayer).where(BzzPlayer.api_id == api_id))
        ).scalar_one()


# --------------------------------------------------------------------------
# recrue ajoutee : joueur sans club, matche par TM -> current_team pose
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_recruit_gets_current_team_set(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [BzzPlayer(api_id=1, name="Nouvelle Recrue")],
        teams=[BzzTeam(api_id=10, name="Club X FC")],
    )
    _patch_fetch(monkeypatch, {100: _ok(100, [_tm("Nouvelle Recrue", tm_player_id=1)])})

    async with session_factory() as session:
        run, samples = await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    player = await _get_player(session_factory, 1)
    assert player.current_team_api_id == 10
    assert player.current_team_name == "Club X FC"  # nom pose depuis bzz_teams
    assert player.loan_team_api_id is None
    assert player.tm_absent_streak == 0
    assert run.status == "ok"
    assert run.players_updated == 1
    assert run.players_detached == 0


# --------------------------------------------------------------------------
# pret perime nettoye : joueur marque en pret ailleurs, matche a son club X
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_loan_is_cleared_on_match(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [
            BzzPlayer(
                api_id=2,
                name="Joueur Pretexpire",
                current_team_api_id=10,
                loan_team_api_id=999,
                loan_team_name="Club Pret Perime",
            )
        ],
    )
    _patch_fetch(monkeypatch, {100: _ok(100, [_tm("Joueur Pretexpire", tm_player_id=2)])})

    async with session_factory() as session:
        await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    player = await _get_player(session_factory, 2)
    assert player.current_team_api_id == 10
    assert player.loan_team_api_id is None
    assert player.loan_team_name is None


# --------------------------------------------------------------------------
# depart 1er run : absent de l'effectif TM -> streak=1, PAS detache
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_absence_increments_streak_without_detaching(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [BzzPlayer(api_id=3, name="Joueur Absent Une Fois", current_team_api_id=10)],
    )
    # Effectif TM du club X ne contient PAS ce joueur.
    _patch_fetch(monkeypatch, {100: _ok(100, [_tm("Autre Joueur", tm_player_id=99)])})

    async with session_factory() as session:
        run, samples = await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    player = await _get_player(session_factory, 3)
    assert player.tm_absent_streak == 1
    assert player.current_team_api_id == 10  # inchange
    assert run.players_detached == 0


# --------------------------------------------------------------------------
# depart 2e run consecutif -> detache
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_consecutive_absence_detaches_player(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [
            BzzPlayer(
                api_id=4,
                name="Joueur Absent Deux Fois",
                current_team_api_id=10,
                current_team_name="Club X",
                tm_absent_streak=1,  # deja absent lors du run precedent
            )
        ],
    )
    _patch_fetch(monkeypatch, {100: _ok(100, [_tm("Autre Joueur", tm_player_id=99)])})

    async with session_factory() as session:
        run, samples = await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    player = await _get_player(session_factory, 4)
    assert player.tm_absent_streak == 2
    assert player.current_team_api_id is None
    assert player.current_team_name is None
    assert player.loan_team_api_id is None
    assert run.players_detached == 1


# --------------------------------------------------------------------------
# reassignation X -> Y (les 2 clubs OK) : current=Y, PAS detache de X
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transfer_between_two_ok_clubs_reassigns_without_detaching(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100, name="Club X")
    club_y = _team(team_id=2, bzz_team_id=20, tm_club_id=200, name="Club Y")
    await _seed(
        session_factory,
        [
            BzzPlayer(
                api_id=5,
                name="Joueur Transfere",
                current_team_api_id=10,
                current_team_name="Club X",
            )
        ],
        teams=[BzzTeam(api_id=10, name="Club X"), BzzTeam(api_id=20, name="Club Y")],
    )
    _patch_fetch(
        monkeypatch,
        {
            # X ne le liste plus.
            100: _ok(100, [_tm("Autre Joueur X", tm_player_id=98)]),
            # Y le liste desormais.
            200: _ok(200, [_tm("Joueur Transfere", tm_player_id=5)]),
        },
    )

    async with session_factory() as session:
        run, samples = await sync_squads(session, FAKE_CLIENT, [club_x, club_y], mode="daily", today=TODAY)

    player = await _get_player(session_factory, 5)
    assert player.current_team_api_id == 20
    assert player.current_team_name == "Club Y"  # reassignation : nom mis a jour vers Y
    assert player.tm_absent_streak == 0
    assert run.players_detached == 0
    assert run.players_updated == 1


# --------------------------------------------------------------------------
# club KO -> 0 ecriture sur ses joueurs
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_club_writes_nothing_for_its_players(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100)
    await _seed(
        session_factory,
        [
            BzzPlayer(
                api_id=6,
                name="Joueur Club KO",
                current_team_api_id=10,
                tm_absent_streak=1,
            )
        ],
    )
    _patch_fetch(monkeypatch, {100: _ko(100)})

    async with session_factory() as session:
        run, samples = await sync_squads(session, FAKE_CLIENT, [club_x], mode="daily", today=TODAY)

    player = await _get_player(session_factory, 6)
    # Rien n'a bouge : ni le streak, ni le rattachement.
    assert player.tm_absent_streak == 1
    assert player.current_team_api_id == 10
    assert run.clubs_failed == 1
    assert run.clubs_ok == 0
    assert run.players_updated == 0
    assert run.players_detached == 0
    # Un seul club, 100% KO -> sentinelle declenchee -> run failed.
    assert run.status == "failed"


# --------------------------------------------------------------------------
# sentinelle > 30% clubs KO -> 0 ecriture d'effectif + run failed persiste
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sentinel_over_30pct_failed_clubs_blocks_all_writes(session_factory, monkeypatch):
    club_x = _team(team_id=1, bzz_team_id=10, tm_club_id=100, name="Club X")
    club_y = _team(team_id=2, bzz_team_id=20, tm_club_id=200, name="Club Y")
    club_z = _team(team_id=3, bzz_team_id=30, tm_club_id=300, name="Club Z")
    await _seed(
        session_factory,
        [
            # Ce joueur serait normalement matche/rattache a X si le run
            # n'etait pas bloque par la sentinelle.
            BzzPlayer(api_id=7, name="Joueur Sentinelle"),
        ],
    )
    _patch_fetch(
        monkeypatch,
        {
            # X est OK et matcherait le joueur ci-dessus...
            100: _ok(100, [_tm("Joueur Sentinelle", tm_player_id=7)]),
            # ...mais Y et Z (2/3 = 67% > 30%) sont KO -> sentinelle.
            200: _ko(200),
            300: _ko(300, status="empty"),
        },
    )

    async with session_factory() as session:
        run, samples = await sync_squads(
            session, FAKE_CLIENT, [club_x, club_y, club_z], mode="daily", today=TODAY
        )

    player = await _get_player(session_factory, 7)
    assert player.current_team_api_id is None  # aucune ecriture, meme pour le club OK
    assert player.tm_absent_streak == 0

    assert run.status == "failed"
    assert run.clubs_total == 3
    assert run.clubs_ok == 1
    assert run.clubs_failed == 2
    assert run.players_updated == 0
    assert run.players_detached == 0

    # La ligne de run est bien persistee malgre l'echec.
    async with session_factory() as session:
        stored = (
            await session.execute(select(SquadSyncRun).where(SquadSyncRun.id == run.id))
        ).scalar_one()
        assert stored.status == "failed"
        assert len(stored.detail["failed_clubs"]) == 2
