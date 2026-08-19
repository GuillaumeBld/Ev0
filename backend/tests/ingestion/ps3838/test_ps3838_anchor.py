from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.ingestion.ps3838.anchor import match_event, norm_team, resolve_anchors
from app.ingestion.ps3838.client import Ps3838Event
from app.models.fixtures import Fixture

KO = datetime(2026, 8, 19, 19, 0, tzinfo=UTC)

# Coup d'envoi loin dans le futur : independant de la date reelle d'execution
# des tests (resolve_anchors filtre sur kickoff_utc > now()).
FUTURE_KO = datetime(2030, 6, 15, 19, 0, tzinfo=UTC)


def _ev(eid, home, away, ko=KO):
    return Ps3838Event(eid, home, away, ko, "Spain - La Liga", {"home": 1.4, "draw": 5.0, "away": 8.0}, {"over_3.0": 1.8, "under_3.0": 2.0}, 3.0)


def _fx(home, away, ko=KO):
    return SimpleNamespace(id=1, home_team=home, away_team=away, kickoff_utc=ko)


def test_norm_folds_accents_and_strips_club_suffixes():
    assert norm_team("Atlético Madrid") == norm_team("Atletico Madrid")
    assert norm_team("Málaga CF") == norm_team("Malaga")
    assert "madrid" in norm_team("Real Madrid CF")


def test_exact_match_resolves():
    evs = [_ev(111, "Atletico Madrid", "Malaga")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs).event_id == 111


def test_same_teams_different_day_does_not_resolve():
    evs = [_ev(111, "Atletico Madrid", "Malaga", KO + timedelta(days=1))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_two_hour_tolerance():
    evs = [_ev(111, "Atletico Madrid", "Malaga", KO + timedelta(hours=1, minutes=59))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs).event_id == 111
    evs = [_ev(222, "Atletico Madrid", "Malaga", KO + timedelta(hours=2, minutes=1))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_reversed_teams_do_not_resolve():
    """Domicile et exterieur inverses : ce n'est pas le meme match."""
    evs = [_ev(111, "Malaga", "Atletico Madrid")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_ambiguous_candidates_do_not_resolve():
    """Deux evenements plausibles a la meme heure : on ne devine pas."""
    evs = [_ev(111, "Atletico Madrid", "Malaga"), _ev(222, "Atletico Madrid", "Malaga")]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


def test_partial_team_overlap_is_not_enough():
    """'Real Madrid' vs 'Real Sociedad' partagent un token : insuffisant."""
    evs = [_ev(111, "Real Sociedad", "Malaga")]
    assert match_event(_fx("Real Madrid", "Málaga CF"), evs) is None


def test_two_hour_tolerance_is_symmetric_for_events_before_kickoff():
    """abs() doit couvrir les deux sens : un evenement ANTERIEUR au coup
    d'envoi de la fixture doit aussi passer la tolerance de 2h (et etre
    rejete au-dela). Sans cet abs(), un evenement avance de 2h+ serait
    accepte a tort par une simple comparaison signee.
    """
    evs = [_ev(111, "Atletico Madrid", "Malaga", KO - timedelta(hours=1, minutes=59))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs).event_id == 111
    evs = [_ev(222, "Atletico Madrid", "Malaga", KO - timedelta(hours=2, minutes=1))]
    assert match_event(_fx("Atlético Madrid", "Málaga CF"), evs) is None


# ---------------------------------------------------------------------------
# resolve_anchors : moteur SQLite en memoire REEL (pas de mock de session),
# meme schema de mise en place que tests/test_sync_squads.py.
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Fixture.__table__.create(sync_conn))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _row(fx_id, home, away, ko=FUTURE_KO, ps3838_event_id=None, status="scheduled"):
    return Fixture(
        id=fx_id,
        external_id=f"ext-{fx_id}",
        league="la_liga",
        season="2025-26",
        home_team=home,
        away_team=away,
        kickoff_utc=ko,
        status=status,
        ps3838_event_id=ps3838_event_id,
    )


async def test_duplicate_target_event_id_anchors_only_the_first_no_exception(session_factory):
    """Deux fixtures se rapprocheraient toutes deux du meme event_id : la
    contrainte unique en base interdirait la double affectation. resolve_anchors
    doit l'empecher lui-meme (ensemble `taken` mis a jour au fil de la boucle),
    sans jamais lever d'exception au commit.
    """
    fx1 = _row(1, "Atletico Madrid", "Malaga")
    fx2 = _row(2, "Atletico Madrid", "Malaga")
    async with session_factory() as session:
        session.add_all([fx1, fx2])
        await session.commit()

    events = [_ev(111, "Atletico Madrid", "Malaga", FUTURE_KO)]

    async with session_factory() as session:
        resolved, unresolved = await resolve_anchors(session, events)

    assert resolved == 1
    assert len(unresolved) == 1

    async with session_factory() as session:
        rows = (
            await session.execute(select(Fixture).order_by(Fixture.id))
        ).scalars().all()

    anchored_ids = [r.ps3838_event_id for r in rows]
    assert anchored_ids.count(111) == 1
    assert anchored_ids.count(None) == 1
    # La fixture rencontree en premier (id=1) est celle qui a ete ancree.
    assert rows[0].ps3838_event_id == 111
    assert rows[1].ps3838_event_id is None


async def test_event_id_already_taken_in_db_is_not_reused(session_factory):
    """Un event_id deja porte par une autre fixture en base ne doit jamais
    etre reattribue, meme si le rapprochement candidat est par ailleurs
    parfait (equipes + date)."""
    already_anchored = _row(1, "Barcelona", "Sevilla", ps3838_event_id=111)
    candidate = _row(2, "Atletico Madrid", "Malaga")
    async with session_factory() as session:
        session.add_all([already_anchored, candidate])
        await session.commit()

    events = [_ev(111, "Atletico Madrid", "Malaga", FUTURE_KO)]

    async with session_factory() as session:
        resolved, unresolved = await resolve_anchors(session, events)

    assert resolved == 0
    assert len(unresolved) == 1

    async with session_factory() as session:
        stored = (
            await session.execute(select(Fixture).where(Fixture.id == 2))
        ).scalar_one()
    assert stored.ps3838_event_id is None


async def test_unmatched_fixture_is_unresolved_and_labelled(session_factory):
    """Aucun evenement ne correspond : la fixture ressort en non-resolue,
    avec un libelle exploitable pour le surfacage (pas un identifiant muet)."""
    fx = _row(1, "PSG", "Marseille")
    async with session_factory() as session:
        session.add(fx)
        await session.commit()

    events = [_ev(111, "Atletico Madrid", "Malaga", FUTURE_KO)]

    async with session_factory() as session:
        resolved, unresolved = await resolve_anchors(session, events)

    assert resolved == 0
    assert len(unresolved) == 1
    assert "PSG" in unresolved[0]
    assert "Marseille" in unresolved[0]


async def test_nominal_case_persists_event_id_after_commit(session_factory):
    """Cas nominal : un seul candidat propre -> ps3838_event_id est bien
    persiste en base apres le commit de resolve_anchors."""
    fx = _row(1, "Atletico Madrid", "Malaga")
    async with session_factory() as session:
        session.add(fx)
        await session.commit()

    events = [_ev(111, "Atletico Madrid", "Malaga", FUTURE_KO)]

    async with session_factory() as session:
        resolved, unresolved = await resolve_anchors(session, events)

    assert resolved == 1
    assert unresolved == []

    async with session_factory() as session:
        stored = (
            await session.execute(select(Fixture).where(Fixture.id == 1))
        ).scalar_one()
    assert stored.ps3838_event_id == 111
