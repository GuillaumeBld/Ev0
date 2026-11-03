"""Tests pour `coverage_check.check_coverage` (Tache 5 : garde-fou « zero
joueur a moitie » de l'expansion ~57 competitions).

Moteur SQLite EN MEMOIRE REEL (meme procede que `test_sync_squads.py` /
`test_rebuild_registry_expansion.py`) : `canonical_teams.aliases` (ARRAY) et
`data_coverage_runs.detail` (JSONB) sont des types Postgres que SQLite ne
sait pas compiler nativement ; on enregistre, UNIQUEMENT dans ce module de
test, un compilateur DDL qui les fait passer pour `JSON` generique.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.ingestion.bzzoiro.coverage_check import check_coverage
from app.models.bzzoiro import BzzEvent, BzzPlayer, BzzPlayerMatchStat, BzzPlayerSeasonStat
from app.models.canonical_teams import CanonicalTeam
from app.models.data_coverage import DataCoverageRun

SEASON = "2026-2027"
# Fenetre de la saison 2026-2027 : [2026-08-01, 2027-08-01).
IN_SEASON_DATE = datetime(2026, 9, 1, tzinfo=UTC)

LID = 10  # id de ligue arbitraire pour les tests (hors registre, injecte via league_ids=)
LID_OTHER = 11


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


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
            CanonicalTeam.__table__,
            BzzPlayer.__table__,
            BzzEvent.__table__,
            BzzPlayerMatchStat.__table__,
            BzzPlayerSeasonStat.__table__,
            DataCoverageRun.__table__,
        ):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _runs_for(session, league_api_id: int) -> list[DataCoverageRun]:
    rows = (
        await session.execute(
            select(DataCoverageRun).where(DataCoverageRun.league_api_id == league_api_id)
        )
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Club complet -> ok
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_club_is_ok(session_factory):
    async with session_factory() as session:
        club = CanonicalTeam(
            name_fr="Club Complet",
            bzz_team_id=501,
            league_api_id=LID,
            season=SEASON,
        )
        session.add(club)

        session.add(
            BzzEvent(
                api_id=9001,
                league_api_id=LID,
                home_team_api_id=501,
                away_team_api_id=502,
                event_date=IN_SEASON_DATE,
                status="finished",
            )
        )
        session.add(
            BzzPlayer(api_id=101, name="Joueur Un", current_team_api_id=501)
        )
        session.add(
            BzzPlayerMatchStat(player_api_id=101, event_api_id=9001, team_api_id=501, minutes_played=90)
        )
        session.add(
            BzzPlayerSeasonStat(
                player_api_id=101, league_api_id=LID, season=SEASON, minutes_played=90,
            )
        )
        await session.commit()

        reports = await check_coverage(session, season=SEASON, league_ids=[LID])

        assert len(reports) == 1
        report = reports[0]
        assert report.league_api_id == LID
        assert report.clubs_total == 1
        assert report.clubs_ok == 1
        assert report.clubs_incomplets == 0
        assert report.joueurs_sans_stats == 0
        assert report.status == "ok"
        assert report.detail["incomplete_clubs"] == []

        runs = await _runs_for(session, LID)
        assert len(runs) == 1
        assert runs[0].status == "ok"
        assert runs[0].clubs_ok == 1
        assert runs[0].finished_at is not None


# ---------------------------------------------------------------------------
# Match termine sans match-stats -> club incomplet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finished_match_without_stats_marks_club_incomplete(session_factory):
    async with session_factory() as session:
        club = CanonicalTeam(
            name_fr="Club Match Trou",
            bzz_team_id=601,
            league_api_id=LID,
            season=SEASON,
        )
        session.add(club)

        # Match termine, aucune ligne BzzPlayerMatchStat pour cet evenement.
        session.add(
            BzzEvent(
                api_id=9101,
                league_api_id=LID,
                home_team_api_id=601,
                away_team_api_id=602,
                event_date=IN_SEASON_DATE,
                status="finished",
            )
        )
        # Le joueur de l'effectif a quand meme sa ligne season-stat : on veut
        # isoler le trou "match sans stats" du trou "joueur sans ligne".
        session.add(BzzPlayer(api_id=201, name="Joueur Deux", current_team_api_id=601))
        session.add(
            BzzPlayerSeasonStat(
                player_api_id=201, league_api_id=LID, season=SEASON, minutes_played=90,
            )
        )
        await session.commit()

        reports = await check_coverage(session, season=SEASON, league_ids=[LID])

        report = reports[0]
        assert report.clubs_total == 1
        assert report.clubs_ok == 0
        assert report.clubs_incomplets == 1
        assert report.joueurs_sans_stats == 0
        assert report.status == "partial"

        incomplete = report.detail["incomplete_clubs"]
        assert len(incomplete) == 1
        assert incomplete[0]["canonical_team_id"] == club.id
        assert incomplete[0]["missing_match_stats_events"] == [9101]
        assert incomplete[0]["players_without_season_stats"] == []

        runs = await _runs_for(session, LID)
        assert runs[0].status == "partial"
        assert runs[0].clubs_incomplets == 1


# ---------------------------------------------------------------------------
# Joueur sans ligne season-stat -> compte ; 0 minute avec ligne -> pas un trou
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_player_without_season_stat_row_counted_zero_minute_not_a_gap(session_factory):
    async with session_factory() as session:
        club = CanonicalTeam(
            name_fr="Club Effectif Trou",
            bzz_team_id=701,
            league_api_id=LID,
            season=SEASON,
        )
        session.add(club)

        # Match termine AVEC ses match-stats : on isole le trou "effectif".
        session.add(
            BzzEvent(
                api_id=9201,
                league_api_id=LID,
                home_team_api_id=701,
                away_team_api_id=702,
                event_date=IN_SEASON_DATE,
                status="finished",
            )
        )

        # Joueur A : a joue, a sa ligne season-stat (90 minutes) -> ok.
        session.add(BzzPlayer(api_id=301, name="Joueur A", current_team_api_id=701))
        session.add(
            BzzPlayerMatchStat(player_api_id=301, event_api_id=9201, team_api_id=701, minutes_played=90)
        )
        session.add(
            BzzPlayerSeasonStat(
                player_api_id=301, league_api_id=LID, season=SEASON, minutes_played=90,
            )
        )

        # Joueur B : n'a pas joue, mais A une ligne season-stat a 0 minute
        # -> NE DOIT PAS etre compte comme un trou (0 minute admis).
        session.add(BzzPlayer(api_id=302, name="Joueur B (0 min)", current_team_api_id=701))
        session.add(
            BzzPlayerSeasonStat(
                player_api_id=302, league_api_id=LID, season=SEASON, minutes_played=0,
            )
        )

        # Joueur C : de l'effectif, AUCUNE ligne season-stat -> trou.
        session.add(BzzPlayer(api_id=303, name="Joueur C (absent)", current_team_api_id=701))

        await session.commit()

        reports = await check_coverage(session, season=SEASON, league_ids=[LID])

        report = reports[0]
        assert report.clubs_total == 1
        assert report.clubs_ok == 0
        assert report.clubs_incomplets == 1
        assert report.joueurs_sans_stats == 1
        assert report.status == "partial"

        incomplete = report.detail["incomplete_clubs"]
        assert len(incomplete) == 1
        assert incomplete[0]["players_without_season_stats"] == [303]
        assert incomplete[0]["missing_match_stats_events"] == []

        runs = await _runs_for(session, LID)
        assert runs[0].joueurs_sans_stats == 1


# ---------------------------------------------------------------------------
# Echec d'enumeration d'une ligue -> failed, isole, pas d'exception propagee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_league_enumeration_failure_is_isolated_and_never_raises(session_factory, monkeypatch):
    import app.ingestion.bzzoiro.coverage_check as coverage_check_module

    original = coverage_check_module._check_league_coverage

    async def _boom(session, league_api_id, season, started_at):
        if league_api_id == LID:
            raise RuntimeError("enumeration cassee pour ce test")
        return await original(session, league_api_id, season, started_at)

    monkeypatch.setattr(coverage_check_module, "_check_league_coverage", _boom)

    async with session_factory() as session:
        # LID_OTHER n'a aucun club engage -> traite normalement (trivial ok),
        # ce qui prouve que la ligue en echec n'a pas bloque le traitement
        # des ligues suivantes.
        reports = await check_coverage(session, season=SEASON, league_ids=[LID, LID_OTHER])

        assert len(reports) == 2

        failed_report = next(r for r in reports if r.league_api_id == LID)
        assert failed_report.status == "failed"
        assert "error" in failed_report.detail

        ok_report = next(r for r in reports if r.league_api_id == LID_OTHER)
        assert ok_report.status == "ok"
        assert ok_report.clubs_total == 0

        failed_runs = await _runs_for(session, LID)
        assert len(failed_runs) == 1
        assert failed_runs[0].status == "failed"

        ok_runs = await _runs_for(session, LID_OTHER)
        assert len(ok_runs) == 1
        assert ok_runs[0].status == "ok"
