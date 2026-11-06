"""Tests pour la table isolee player_selection_season_stats (selections
nationales) et pour le resolveur nationalite -> equipe nationale.

Debut Task 1 (smoke test d'import du modele) ; complete en Task 2 avec
`national_team_id_by_player` (app/ingestion/bzzoiro/selection_nationality.py).

Moteur SQLite EN MEMOIRE REEL (meme procede que tests/test_coverage_check.py
et tests/test_attach_european.py) : canonical_teams.aliases (ARRAY) est un
type Postgres que SQLite ne sait pas compiler nativement ; on enregistre,
UNIQUEMENT dans ce module de test, un compilateur DDL qui le fait passer
pour JSON generique.
"""
from __future__ import annotations

import pytest
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.ingestion.bzzoiro.selection_nationality import national_team_id_by_player
from app.models.bzzoiro import BzzPlayer
from app.models.canonical_teams import CanonicalTeam

WORLD_CUP_LEAGUE_ID = 27


@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


def test_selection_model_imports():
    from app.models.selection_stats import PlayerSelectionSeasonStat

    assert PlayerSelectionSeasonStat.__tablename__ == "player_selection_season_stats"


# ---------------------------------------------------------------------------
# Resolveur nationalite -> equipe nationale (Task 2)
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for table in (CanonicalTeam.__table__, BzzPlayer.__table__):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def seed_selection_fixtures(db_session):
    """1 equipe nationale canonique France (league 27, bzz_team_id=1000) +
    joueur api_id=50 nationality="France" + joueur api_id=52
    nationality="Wakanda" (nationalite inconnue, ne se resout a aucune
    equipe)."""
    db_session.add(
        CanonicalTeam(
            name_fr="France",
            name_en="France",
            league_api_id=WORLD_CUP_LEAGUE_ID,
            bzz_team_id=1000,
        )
    )
    db_session.add(BzzPlayer(api_id=50, name="Joueur 50", nationality="France"))
    db_session.add(BzzPlayer(api_id=52, name="Joueur 52", nationality="Wakanda"))
    await db_session.commit()


@pytest.fixture
async def seed_cabo_verde_fixtures(db_session):
    """Equipe canonique Cabo Verde (league 27, bzz_team_id=1001) + joueur
    api_id=51 nationality="Cape Verde" (libelle Bzzoiro divergent, corrige
    par _NATIONALITY_OVERRIDES)."""
    db_session.add(
        CanonicalTeam(
            name_fr="Cap-Vert",
            name_en="Cabo Verde",
            league_api_id=WORLD_CUP_LEAGUE_ID,
            bzz_team_id=1001,
        )
    )
    db_session.add(BzzPlayer(api_id=51, name="Joueur 51", nationality="Cape Verde"))
    await db_session.commit()


@pytest.mark.asyncio
async def test_maps_player_to_national_team(db_session, seed_selection_fixtures):
    mapping = await national_team_id_by_player(db_session)
    assert mapping[50] == 1000


@pytest.mark.asyncio
async def test_override_cabo_verde(db_session, seed_cabo_verde_fixtures):
    mapping = await national_team_id_by_player(db_session)
    assert mapping[51] == 1001


@pytest.mark.asyncio
async def test_unknown_nationality_absent(db_session, seed_selection_fixtures):
    mapping = await national_team_id_by_player(db_session)
    assert 52 not in mapping
