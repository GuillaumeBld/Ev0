"""Tests for migration 049 (Transfermarkt squad sync): verify the new
columns/table exist on the ORM models and behave as expected. Mirrors the
approach used in test_import_career.py — a real (SQLite in-memory) engine
introspected via SQLAlchemy, rather than mocks, since we care about actual
DDL shape (columns, nullability, defaults).
"""
from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models.bzzoiro import BzzPlayer
from app.models.canonical_teams import CanonicalTeam
from app.models.squad_sync import SquadSyncRun


@pytest.fixture
async def engine():
    # Note: canonical_teams.aliases (ARRAY) and squad_sync_runs.detail
    # (JSONB) are postgres-specific types SQLite cannot compile, so those
    # two tables are verified structurally instead (below). bzz_players has
    # no such dialect-specific column and round-trips fine on SQLite, which
    # lets us prove the tm_absent_streak default is actually applied by a
    # real INSERT, not just declared on the model.
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(lambda sync_conn: BzzPlayer.__table__.create(sync_conn))
    try:
        yield eng
    finally:
        await eng.dispose()


# --------------------------------------------------------------------------
# canonical_teams.transfermarkt_club_id
# --------------------------------------------------------------------------


def test_canonical_team_has_transfermarkt_club_id_column():
    columns = {c.name for c in CanonicalTeam.__table__.columns}
    assert "transfermarkt_club_id" in columns
    col = CanonicalTeam.__table__.c.transfermarkt_club_id
    assert col.nullable is True
    assert col.unique is True


def test_canonical_team_transfermarkt_club_id_is_placed_alongside_sibling_external_ids():
    # Consistency check: transfermarkt_club_id must follow the same nullable
    # single-column-unique shape as the other *_id external references on
    # canonical_teams (api_football_id, bzz_team_id, sofascore_team_id).
    table = CanonicalTeam.__table__
    for name in ("api_football_id", "bzz_team_id", "sofascore_team_id"):
        sibling = table.c[name]
        new = table.c.transfermarkt_club_id
        assert new.type.python_type is sibling.type.python_type
        assert new.nullable == sibling.nullable
        assert new.unique == sibling.unique


# --------------------------------------------------------------------------
# squad_sync_runs
# --------------------------------------------------------------------------


def test_squad_sync_run_has_expected_columns():
    columns = {c.name for c in SquadSyncRun.__table__.columns}
    assert columns == {
        "id",
        "started_at",
        "finished_at",
        "mode",
        "clubs_total",
        "clubs_ok",
        "clubs_failed",
        "players_updated",
        "players_detached",
        "status",
        "detail",
        "created_at",
    }
    assert SquadSyncRun.__table__.c.started_at.nullable is False
    assert SquadSyncRun.__table__.c.finished_at.nullable is True
    assert SquadSyncRun.__table__.c.created_at.nullable is False


def test_squad_sync_run_detail_is_jsonb():
    from sqlalchemy.dialects.postgresql import JSONB

    assert isinstance(SquadSyncRun.__table__.c.detail.type, JSONB)
    assert SquadSyncRun.__table__.c.detail.nullable is True


def test_squad_sync_run_compiles_as_valid_postgres_ddl():
    # canonical_teams.aliases (ARRAY) and squad_sync_runs.detail (JSONB) are
    # postgres-specific types that SQLite cannot compile (verified as a
    # regression check: this used to raise CompileError against SQLite,
    # which is exactly why the round-trip tests below target SQLite tables
    # that don't use these types instead). Here we confirm the DDL compiles
    # cleanly for the real target dialect (postgres).
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    ddl = str(CreateTable(SquadSyncRun.__table__).compile(dialect=postgresql.dialect()))
    assert "squad_sync_runs" in ddl
    assert "JSONB" in ddl
    assert "started_at" in ddl and "NOT NULL" in ddl


def test_squad_sync_run_object_defaults_before_flush():
    run = SquadSyncRun(started_at=dt.datetime.now(dt.timezone.utc), mode="weekly")
    assert run.status is None
    assert run.finished_at is None
    assert run.mode == "weekly"


# --------------------------------------------------------------------------
# bzz_players.tm_absent_streak
# --------------------------------------------------------------------------


def test_bzz_player_has_tm_absent_streak_column():
    col = BzzPlayer.__table__.c.tm_absent_streak
    assert col.nullable is False
    assert col.default.arg == 0


@pytest.mark.asyncio
async def test_bzz_player_tm_absent_streak_defaults_to_zero(engine):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        player = BzzPlayer(api_id=1, name="Test Player")
        session.add(player)
        await session.commit()

        row = (
            await session.execute(select(BzzPlayer).where(BzzPlayer.api_id == 1))
        ).scalar_one()
        assert row.tm_absent_streak == 0
