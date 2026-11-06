"""Tests pour GET /api/v1/atlas/geo — arbre géographique (carte + menus Atlas).

Moteur SQLite en mémoire réel, requêtes via httpx.AsyncClient sur l'app
complète (ASGITransport + override de get_db) — même procédé de compilation
DDL que `test_coverage_check.py` pour les colonnes Postgres-only
(`canonical_teams.aliases` ARRAY, `data_coverage_runs.detail` JSONB) que
SQLite ne sait pas compiler nativement.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.main import app
from app.models.app_config import AppConfig
from app.models.bzzoiro import BzzEvent
from app.models.canonical_teams import CanonicalTeam
from app.models.data_coverage import DataCoverageRun
from app.models.leagues import League
from app.models.selection_stats import PlayerSelectionSeasonStat


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


@compiles(ARRAY, "sqlite")
def _compile_array_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


# BzzEvent / PlayerSelectionSeasonStat / AppConfig : ajoutés quand
# /atlas/geo s'est étendu aux ensembles countries_clubs/countries_selections
# (Task 2 du plan atlas-modes-coupes-selections) -- l'endpoint interroge
# désormais ces tables même quand les tests ci-dessous ne seedent aucune
# ligne, donc elles doivent exister dans le schéma SQLite en mémoire.
@pytest.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for table in (
            League.__table__,
            CanonicalTeam.__table__,
            DataCoverageRun.__table__,
            BzzEvent.__table__,
            PlayerSelectionSeasonStat.__table__,
            AppConfig.__table__,
        ):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def _seed(factory, *, leagues=(), teams=(), runs=()):
    async with factory() as session:
        session.add_all([*leagues, *teams, *runs])
        await session.commit()


async def test_arbre_france_espagne_et_international(client):
    ac, factory = client
    await _seed(
        factory,
        leagues=[
            League(
                id=6, name="Ligue 1", country="France", tier="1",
                kind="domestic_1", format="round_robin", expected_count=18, rank=10,
            ),
            League(
                id=7, name="Ligue 2", country="France", tier="2",
                kind="domestic_2", format="round_robin", expected_count=20, rank=20,
            ),
            League(
                id=8, name="La Liga", country="Spain", tier="1",
                kind="domestic_1", format="round_robin", expected_count=20, rank=5,
            ),
            League(
                id=2, name="Ligue des Champions", country=None, tier="1",
                kind="uefa_club", format="groups", expected_count=None, rank=1,
            ),
            League(
                id=3, name="Coupe du Monde", country=None, tier="selection",
                kind="selection", format="groups", expected_count=None, rank=2,
            ),
        ],
        teams=[
            CanonicalTeam(id=1, name_fr="Paris SG", league_api_id=6),
            CanonicalTeam(id=2, name_fr="Marseille", league_api_id=6),
            CanonicalTeam(id=3, name_fr="Real Madrid", league_api_id=8),
        ],
        runs=[
            # Ligue 1 : le run le plus ancien est 'partial', le plus récent
            # est 'ok' -- c'est ce dernier qui doit compter.
            DataCoverageRun(id=1, started_at=datetime(2026, 1, 1, tzinfo=UTC),
                             league_api_id=6, status="partial"),
            DataCoverageRun(id=2, started_at=datetime(2026, 6, 1, tzinfo=UTC),
                             league_api_id=6, status="ok"),
            DataCoverageRun(id=3, started_at=datetime(2026, 1, 1, tzinfo=UTC),
                             league_api_id=8, status="partial"),
        ],
    )

    resp = await ac.get("/api/v1/atlas/geo")
    assert resp.status_code == 200
    data = resp.json()

    by_code = {c["code"]: c for c in data["countries"]}
    assert "FR" in by_code
    fr = by_code["FR"]
    assert fr["name"] == "France"
    assert fr["continent"] == "EUR"
    assert [l["league_api_id"] for l in fr["leagues"]] == [6, 7]  # trié par rank
    l1, l2 = fr["leagues"]
    assert l1["club_count"] == 2
    assert l1["coverage"] == "full"  # run le plus recent = ok
    assert l2["club_count"] == 0
    assert l2["coverage"] == "part"  # pas de run, tier != 1

    es = by_code["ES"]
    assert es["continent"] == "EUR"
    assert es["leagues"][0]["club_count"] == 1
    assert es["leagues"][0]["coverage"] == "part"  # dernier run = partial

    # L'Espagne (rank min 5) doit passer avant la France (rank min 10).
    assert [c["code"] for c in data["countries"]] == ["ES", "FR"]

    intl_by_id = {i["league_api_id"]: i for i in data["international"]}
    assert intl_by_id[2]["org"] == "UEFA"
    assert intl_by_id[2]["kind"] == "uefa_club"
    assert intl_by_id[3]["org"] == "FIFA"
    assert intl_by_id[3]["kind"] == "selection"
    # Tri international par rank : UCL (1) avant la Coupe du Monde (2).
    assert [i["league_api_id"] for i in data["international"]] == [2, 3]


async def test_england_et_scotland_fusionnent_sous_gb(client):
    ac, factory = client
    await _seed(
        factory,
        leagues=[
            League(
                id=1, name="Premier League", country="England", tier="1",
                kind="domestic_1", format="round_robin", expected_count=20, rank=1,
            ),
            League(
                id=4, name="Premiership", country="Scotland", tier="1",
                kind="domestic_1", format="round_robin", expected_count=12, rank=30,
            ),
        ],
    )

    resp = await ac.get("/api/v1/atlas/geo")
    assert resp.status_code == 200
    data = resp.json()

    gb_countries = [c for c in data["countries"] if c["code"] == "GB"]
    assert len(gb_countries) == 1
    gb = gb_countries[0]
    assert gb["name"] == "Royaume-Uni"
    assert gb["continent"] == "EUR"
    assert [l["league_api_id"] for l in gb["leagues"]] == [1, 4]
    # Aucun run de coverage -> defaut base sur le tier (tier 1 -> full).
    assert all(l["coverage"] == "full" for l in gb["leagues"])


async def test_pays_sans_mapping_est_ignore_sans_planter(client, caplog):
    ac, factory = client
    await _seed(
        factory,
        leagues=[
            League(
                id=99, name="Liga inconnue", country="Atlantis", tier="1",
                kind="domestic_1", format="round_robin", expected_count=10, rank=1,
            ),
        ],
    )

    resp = await ac.get("/api/v1/atlas/geo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["countries"] == []
    assert data["international"] == []


async def test_kind_world_avec_pays_va_dans_countries_sans_pays_va_dans_international(client):
    """Les championnats `kind="world"` (Brésil, Arabie S., MLS, etc. — voir
    league_registry.py) disparaissaient de la carte car le filtre atlas.py
    ne connaissait que domestic_1|domestic_2|uefa_club|selection. L'aiguillage
    doit se faire par présence de pays, pas par kind : un `world` avec un
    country mappé (Brasileirão) va dans `countries`, un `world` sans country
    (Copa Libertadores, club continental) va dans `international`."""
    ac, factory = client
    await _seed(
        factory,
        leagues=[
            League(
                id=9, name="Brasileirão Série A", country="Brazil", tier="4",
                kind="world", format="round_robin", expected_count=20, rank=34,
            ),
            League(
                id=32, name="Copa Libertadores", country=None, tier="4",
                kind="world", format="groups", expected_count=None, rank=46,
            ),
        ],
    )

    resp = await ac.get("/api/v1/atlas/geo")
    assert resp.status_code == 200
    data = resp.json()

    by_code = {c["code"]: c for c in data["countries"]}
    assert "BR" in by_code
    br = by_code["BR"]
    assert br["continent"] == "SAM"
    assert [l["league_api_id"] for l in br["leagues"]] == [9]
    # Pas de DataCoverageRun, mais kind=='world' -> coverage par défaut 'full'.
    assert br["leagues"][0]["coverage"] == "full"

    intl_by_id = {i["league_api_id"]: i for i in data["international"]}
    assert 32 in intl_by_id
    assert intl_by_id[32]["org"] == "CONMEBOL"
    assert intl_by_id[32]["kind"] == "world"
