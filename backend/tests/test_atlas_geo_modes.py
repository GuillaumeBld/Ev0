"""Tests pour les ensembles `countries_clubs` / `countries_selections` de
GET /api/v1/atlas/geo — la carte Atlas doit allumer des pays différents
selon le mode Clubs/Sélections (switch, Task 3).

Même procédé que `tests/api/test_atlas_geo.py` : moteur SQLite en mémoire
réel, requêtes via httpx.AsyncClient sur l'app complète (ASGITransport +
override de get_db), avec compilation JSONB/ARRAY -> JSON pour les colonnes
Postgres-only que SQLite ne sait pas compiler nativement.
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


_TABLES = (
    League.__table__,
    CanonicalTeam.__table__,
    DataCoverageRun.__table__,
    BzzEvent.__table__,
    PlayerSelectionSeasonStat.__table__,
    AppConfig.__table__,
)


@pytest.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for table in _TABLES:
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


async def _seed(factory, *, leagues=(), teams=(), runs=(), events=(), selections=(), config=()):
    async with factory() as session:
        session.add_all([*leagues, *teams, *runs, *events, *selections, *config])
        await session.commit()


def _league_phase_c1_events(morocco_team_id: int) -> list[BzzEvent]:
    """18 matchs (round_number=2) -> phase de ligue de la C1 (voir
    _EUROPEAN_CUP_LEAGUE_IDS / _LEAGUE_PHASE_ROUND_MIN/MAX dans atlas.py).
    36 équipes distinctes (501..536), dont `morocco_team_id` (501), sans
    championnat domestique suivi dans ce test -> doit apparaître dans
    countries_clubs via la coupe uniquement.
    """
    team_ids = list(range(501, 537))
    assert morocco_team_id in team_ids
    events = []
    api_id = 1000
    event_date = datetime(2025, 10, 1, tzinfo=UTC)
    for i in range(0, len(team_ids), 2):
        api_id += 1
        events.append(
            BzzEvent(
                api_id=api_id,
                league_api_id=7,
                home_team_api_id=team_ids[i],
                away_team_api_id=team_ids[i + 1],
                event_date=event_date,
                round_number=2,
            )
        )
    assert len(events) == 18
    return events


@pytest.fixture
def base_config():
    # Saison fixée pour que les events de coupe retombent dans la bonne
    # fenêtre, indépendamment de la date du jour.
    return [AppConfig(key="current_season", value="2025-2026")]


async def test_geo_modes_sets_differ(client, base_config):
    """La Jordanie (sélection, pas de championnat suivi) n'apparaît que dans
    countries_selections ; le Maroc (club en coupe C1, pas de championnat
    suivi) n'apparaît que dans countries_clubs. La France (championnat suivi,
    pas de coupe dans ce test) apparaît dans countries_clubs avec
    has_cup_clubs=False."""
    ac, factory = client
    await _seed(
        factory,
        config=base_config,
        leagues=[
            League(
                id=6, name="Ligue 1", country="France", tier="1",
                kind="domestic_1", format="round_robin", expected_count=18, rank=10,
            ),
            League(
                id=7, name="Ligue des Champions", country=None, tier="1",
                kind="uefa_club", format="groups", expected_count=None, rank=1,
            ),
        ],
        teams=[
            CanonicalTeam(id=1, name_fr="Paris SG", league_api_id=6, bzz_team_id=1),
            # Club marocain en C1, sans championnat domestique suivi.
            CanonicalTeam(id=2, name_fr="Club Marocain", bzz_team_id=501, country="Morocco"),
            # Sélection Jordanie : entité canonique de la league 27 (Coupe du
            # monde), resolue via national_team_api_id = bzz_team_id.
            CanonicalTeam(id=3, name_fr="Jordanie", name_en="Jordan", bzz_team_id=9001, league_api_id=27),
        ],
        events=_league_phase_c1_events(morocco_team_id=501),
        selections=[
            PlayerSelectionSeasonStat(
                player_api_id=1, national_team_api_id=9001, league_api_id=27,
                season="2025-2026", source="bzzoiro", matches_played=3,
            ),
        ],
    )

    resp = await ac.get("/api/v1/atlas/geo")
    assert resp.status_code == 200
    data = resp.json()

    clubs_by_code = {c["code"]: c for c in data["countries_clubs"]}
    selections_by_code = {c["code"]: c for c in data["countries_selections"]}

    # Jordanie : sélection seulement.
    assert "JO" in selections_by_code
    assert selections_by_code["JO"]["name"] == "Jordan"
    assert selections_by_code["JO"]["national_team_api_id"] == 9001
    assert "JO" not in clubs_by_code

    # Maroc : club via coupe seulement, pas de championnat -> leagues vide.
    assert "MA" in clubs_by_code
    assert clubs_by_code["MA"]["has_cup_clubs"] is True
    assert clubs_by_code["MA"]["leagues"] == []
    assert "MA" not in selections_by_code

    # France : championnat suivi, pas de club en coupe dans ce test.
    assert "FR" in clubs_by_code
    assert clubs_by_code["FR"]["has_cup_clubs"] is False
    assert [l["league_api_id"] for l in clubs_by_code["FR"]["leagues"]] == [6]
    assert "FR" not in selections_by_code

    # `countries` et `international` restent inchangés (non-régression).
    assert {c["code"] for c in data["countries"]} == {"FR"}
    assert data["international"] == [
        {"league_api_id": 7, "name": "Ligue des Champions", "org": "UEFA", "kind": "uefa_club"}
    ]


async def test_geo_selections_gb_dedup_england_primes(client, base_config):
    """England et Scotland partagent GB (comme _COUNTRY_MAP) : si les deux
    ont des stats de sélection, une seule entité GB est rendue et elle porte
    le national_team_api_id de l'Angleterre."""
    ac, factory = client
    await _seed(
        factory,
        config=base_config,
        teams=[
            CanonicalTeam(id=1, name_fr="Angleterre", name_en="England", bzz_team_id=9002, league_api_id=27),
            CanonicalTeam(id=2, name_fr="Ecosse", name_en="Scotland", bzz_team_id=9003, league_api_id=27),
        ],
        selections=[
            PlayerSelectionSeasonStat(
                player_api_id=1, national_team_api_id=9002, league_api_id=27,
                season="2025-2026", source="bzzoiro", matches_played=3,
            ),
            PlayerSelectionSeasonStat(
                player_api_id=2, national_team_api_id=9003, league_api_id=27,
                season="2025-2026", source="bzzoiro", matches_played=3,
            ),
        ],
    )

    resp = await ac.get("/api/v1/atlas/geo")
    assert resp.status_code == 200
    data = resp.json()

    gb_rows = [c for c in data["countries_selections"] if c["code"] == "GB"]
    assert len(gb_rows) == 1
    assert gb_rows[0]["name"] == "England"
    assert gb_rows[0]["national_team_api_id"] == 9002


async def test_geo_selection_nation_sans_mapping_ignoree(client, base_config, caplog):
    """Une nation sans entrée dans _NATION_ISO2 est loggée et omise, jamais
    d'erreur 500 (règle anti-faux : pas de code ISO2 deviné)."""
    ac, factory = client
    await _seed(
        factory,
        config=base_config,
        teams=[
            CanonicalTeam(id=1, name_fr="Atlantide", name_en="Atlantis", bzz_team_id=7777, league_api_id=27),
        ],
        selections=[
            PlayerSelectionSeasonStat(
                player_api_id=1, national_team_api_id=7777, league_api_id=27,
                season="2025-2026", source="bzzoiro", matches_played=1,
            ),
        ],
    )

    resp = await ac.get("/api/v1/atlas/geo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["countries_selections"] == []


def test_every_nation_iso2_has_a_continent():
    """Garde-fou : toute nation listée dans _NATION_ISO2 doit avoir un
    continent dans _NATION_CONTINENT. Sinon le code retombe silencieusement
    sur 'EUR' (règle anti-faux : pas de fallback muet). Ce test a attrapé le
    Sénégal (SN) ajouté à l'ISO2 mais oublié côté continent."""
    from app.api.atlas import _NATION_CONTINENT, _NATION_ISO2

    manquants = sorted(set(_NATION_ISO2.values()) - set(_NATION_CONTINENT))
    assert manquants == [], f"ISO2 sans continent: {manquants}"
