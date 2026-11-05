"""Tests Tache 3 de l'expansion ~57 competitions : la chaine d'ingestion
stats joueurs (events -> stats par match -> agregation saison) doit
desormais couvrir tout le LEAGUE_REGISTRY, pas seulement les six
competitions big-5+C1 codees en dur dans TARGET_LEAGUE_INTERNAL_ID_LIST.

Chainage prouve de bout en bout, moteur SQLite EN MEMOIRE REEL (meme
procede que tests/test_rebuild_registry_expansion.py et
tests/test_sync_squads.py pour le compilateur JSONB -> JSON) :

  sync_events(league_internal_ids=[10])         -> bzz_events (league 10)
  sync_player_stats(full_season=True)            -> bzz_player_match_stats
  aggregate_season_stats(league_api_id=10, ...)  -> bzz_player_season_stats

Le perimetre fixtures/cotes/pricing (TARGET_LEAGUE_INTERNAL_IDS, utilise par
sync_fixtures_from_bzz.py) ne doit pas bouger : seule la chaine stats joueurs
s'etend au registre.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

import app.ingestion.bzzoiro.sync_player_stats as sync_player_stats_module
from app.ingestion.bzzoiro.aggregate import aggregate_season_stats
from app.ingestion.bzzoiro.constants import TARGET_LEAGUE_INTERNAL_IDS, TARGET_LEAGUE_INTERNAL_ID_LIST
from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY, PLAYER_STATS_LEAGUE_IDS
from app.ingestion.bzzoiro.sync_events import sync_events
from app.ingestion.bzzoiro.sync_player_stats import sync_player_stats
from app.models.bzzoiro import BzzEvent, BzzPlayerMatchStat, BzzPlayerSeasonStat
from app.services.season_service import compute_season

# Eredivisie (Pays-Bas) — hors big-5, presente dans LEAGUE_REGISTRY (Tache 1)
# avec id interne Bzzoiro = 10. Choisie precisement parce qu'elle n'est dans
# aucune des constantes big-5/C1/international existantes.
EREDIVISIE_ID = 10
EVENT_API_ID = 9001
AJAX_API_ID = 501
FEYENOORD_API_ID = 502


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):  # noqa: ARG001
    return "JSON"


@pytest.fixture
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    from app.models.bzzoiro import BzzPlayer

    async with engine.begin() as conn:
        for table in (BzzPlayer.__table__, BzzEvent.__table__, BzzPlayerMatchStat.__table__, BzzPlayerSeasonStat.__table__):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


class FakeBzzClient:
    """Faux client Bzzoiro — aucun reseau. Rend des reponses selon le path
    et les params, exactement comme le ferait get_all()."""

    def __init__(self, event_date_iso: str):
        self.calls: list[tuple[str, dict]] = []
        self._event_date_iso = event_date_iso

    async def get_all(self, path: str, params: dict | None = None):
        params = dict(params or {})
        self.calls.append((path, params))

        if path == "/api/events/":
            if params.get("league") == EREDIVISIE_ID:
                return [
                    {
                        "id": EVENT_API_ID,
                        "league": {"id": EREDIVISIE_ID, "api_id": EREDIVISIE_ID},
                        "home_team_obj": {"id": AJAX_API_ID, "api_id": AJAX_API_ID, "name": "Ajax"},
                        "away_team_obj": {
                            "id": FEYENOORD_API_ID, "api_id": FEYENOORD_API_ID, "name": "Feyenoord",
                        },
                        "event_date": self._event_date_iso,
                        "status": "finished",
                        "home_score": 2,
                        "away_score": 1,
                    }
                ]
            return []

        if path == "/api/player-stats/":
            if params.get("event") == EVENT_API_ID:
                event_ctx = {"id": EVENT_API_ID, "home_team": "Ajax", "away_team": "Feyenoord"}
                return [
                    {
                        "event": event_ctx,
                        "player": {"id": 7001, "name": "Joueur Ajax", "team": "Ajax"},
                        "minutes_played": 90, "goals": 1, "goal_assist": 0,
                        "expected_goals": 0.5, "expected_assists": 0.1,
                        "total_shots": 3, "shots_on_target": 2,
                        "total_pass": 40, "accurate_pass": 32,
                        "rating": 7.5,
                    },
                    {
                        "event": event_ctx,
                        "player": {"id": 7002, "name": "Joueur Feyenoord", "team": "Feyenoord"},
                        "minutes_played": 90, "goals": 0, "goal_assist": 1,
                        "expected_goals": 0.2, "expected_assists": 0.3,
                        "total_shots": 1, "shots_on_target": 0,
                        "total_pass": 35, "accurate_pass": 28,
                        "rating": 6.8,
                    },
                ]
            return []

        raise AssertionError(f"chemin inattendu dans le faux client: {path} {params}")


# ---------------------------------------------------------------------------
# Les listes de ligues : la chaine stats joueurs couvre le registre,
# le perimetre fixtures ne bouge pas.
# ---------------------------------------------------------------------------


def test_les_stats_joueur_couvrent_desormais_le_registre_pas_seulement_big5():
    assert EREDIVISIE_ID in sync_player_stats_module._ALL_LEAGUE_IDS
    assert EREDIVISIE_ID not in TARGET_LEAGUE_INTERNAL_ID_LIST
    # Sanity : la liste derivee du registre est bien plus large que les 6
    # competitions big-5+C1 historiques.
    assert len(sync_player_stats_module._ALL_LEAGUE_IDS) > len(TARGET_LEAGUE_INTERNAL_ID_LIST)
    assert sync_player_stats_module._ALL_LEAGUE_IDS == PLAYER_STATS_LEAGUE_IDS
    # Tout id du registre y figure, sauf les deux tournois historiques
    # (Euro 2024 = 66, Copa America 2024 = 67) reserves au rattrapage de la
    # Tache 6.
    assert set(sync_player_stats_module._ALL_LEAGUE_IDS) == set(LEAGUE_REGISTRY) - {66, 67}


def test_le_perimetre_fixtures_big5_c1_n_a_pas_change():
    """Constante utilisee par sync_fixtures_from_bzz.py (betting/pricing) —
    doit rester exactement les 6 competitions historiques, jamais le
    registre entier."""
    assert TARGET_LEAGUE_INTERNAL_IDS == {
        "premier_league": 1,
        "ligue_1": 6,
        "bundesliga": 5,
        "la_liga": 3,
        "serie_a": 4,
        "champions_league": 7,
    }
    assert TARGET_LEAGUE_INTERNAL_ID_LIST == [1, 6, 5, 3, 4, 7]


# ---------------------------------------------------------------------------
# Chainage de bout en bout pour une ligue du registre hors big-5
# ---------------------------------------------------------------------------


async def test_chainage_events_stats_match_puis_season_stats_pour_ligue_hors_big5(
    session_factory,
):
    now = datetime.now(UTC)
    client = FakeBzzClient(event_date_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    season = compute_season(now.date())

    async with session_factory() as session:
        # 1) events -> bzz_events. On cible explicitement l'Eredivisie (10) :
        # le perimetre fixtures/worker reste scope big-5+international
        # (Tache 6 cablera le scheduler), mais sync_events reste capable de
        # traiter n'importe quelle ligue du registre quand on le lui demande.
        n_events = await sync_events(session, client, league_internal_ids=[EREDIVISIE_ID])
        assert n_events == 1

        stored_events = (await session.execute(select(BzzEvent))).scalars().all()
        assert len(stored_events) == 1
        assert stored_events[0].league_api_id == EREDIVISIE_ID
        assert stored_events[0].status == "finished"

        # 2) stats par match. sync_player_stats ne doit plus etre borne a
        # [1,3,4,5,6,7] : avec full_season=True, le match Eredivisie ecrit
        # ci-dessus doit etre repris.
        n_match_rows = await sync_player_stats(session, client, full_season=True)
        assert n_match_rows == 2

        match_stats = (await session.execute(select(BzzPlayerMatchStat))).scalars().all()
        assert len(match_stats) == 2
        par_joueur = {row.player_api_id: row for row in match_stats}
        assert par_joueur[7001].team_api_id == AJAX_API_ID
        assert par_joueur[7001].is_home is True
        assert par_joueur[7002].team_api_id == FEYENOORD_API_ID
        assert par_joueur[7002].is_home is False

        # 3) agregation saison, keyed par league_api_id=10.
        n_agg = await aggregate_season_stats(
            session, league_api_id=EREDIVISIE_ID, season=season,
        )
        assert n_agg == 2

        season_stats = (
            await session.execute(
                select(BzzPlayerSeasonStat).where(
                    BzzPlayerSeasonStat.league_api_id == EREDIVISIE_ID
                )
            )
        ).scalars().all()
        assert len(season_stats) == 2
        assert {row.season for row in season_stats} == {season}
        joueurs_agreges = {row.player_api_id for row in season_stats}
        assert joueurs_agreges == {7001, 7002}

    # Le faux client ne doit jamais avoir ete appele pour une competition
    # hors de ce qu'on lui a explicitement demande.
    event_calls = [c for c in client.calls if c[0] == "/api/events/"]
    assert all(c[1].get("league") == EREDIVISIE_ID for c in event_calls)
