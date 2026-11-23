"""Cotes bookmakers reliées au joueur Atlas (tâche 8).

Le résolveur est pur : il prend le nom donné par le bookmaker et l'effectif
Atlas des deux équipes du match, et rend l'api_id du joueur — ou None
quand le nom est absent ou ambigu. Jamais de rapprochement approximatif.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.player_identity import SquadPlayer, resolve_odds_player

# ── Effectifs de test ────────────────────────────────────────────────────────

PSG = [
    SquadPlayer(api_id=1, name="Kylian Mbappé"),
    SquadPlayer(api_id=2, name="Lucas Hernández"),
    SquadPlayer(api_id=3, name="Theo Hernández"),
    SquadPlayer(api_id=4, name="Ousmane Dembélé"),
]

# Man City – Napoli du 23/11 : formes réellement vues chez les bookmakers.
CITY_NAPOLI = [
    SquadPlayer(api_id=10, name="Phil Foden"),
    SquadPlayer(api_id=11, name="Jérémy Doku"),
    SquadPlayer(api_id=12, name="Rasmus Højlund"),
    SquadPlayer(api_id=13, name="Allan Elias"),
    SquadPlayer(api_id=14, name="Floyd Samba"),
    SquadPlayer(api_id=15, name="Tyrone Samba"),
    SquadPlayer(api_id=16, name="Dexter Oliver"),
]


# ── Cas du brief ─────────────────────────────────────────────────────────────

def test_nom_exact():
    assert resolve_odds_player("Kylian Mbappé", PSG) == 1


def test_initiale_plus_nom():
    assert resolve_odds_player("K. Mbappé", PSG) == 1


def test_initiale_departage_deux_homonymes():
    assert resolve_odds_player("T. Hernández", PSG) == 3
    assert resolve_odds_player("L. Hernandez", PSG) == 2


def test_nom_seul_ambigu_rend_none():
    assert resolve_odds_player("Hernández", PSG) is None


def test_sans_accent():
    assert resolve_odds_player("Kylian Mbappe", PSG) == 1
    assert resolve_odds_player("Mbappe", PSG) == 1


def test_nom_absent():
    assert resolve_odds_player("Lionel Messi", PSG) is None
    assert resolve_odds_player("", PSG) is None
    assert resolve_odds_player("Kylian Mbappé", []) is None


# ── Formes réelles Man City – Napoli ─────────────────────────────────────────

def test_forme_nom_virgule_prenom():
    assert resolve_odds_player("Samba, Floyd", CITY_NAPOLI) == 14
    assert resolve_odds_player("Oliver, Dexter", CITY_NAPOLI) == 16


def test_deux_samba():
    assert resolve_odds_player("Samba", CITY_NAPOLI) is None
    assert resolve_odds_player("F. Samba", CITY_NAPOLI) == 14
    assert resolve_odds_player("T. Samba", CITY_NAPOLI) == 15


def test_allan_trois_formes():
    assert resolve_odds_player("Allan", CITY_NAPOLI) == 13               # PMU
    assert resolve_odds_player("Allan Elias", CITY_NAPOLI) == 13         # Betclic
    assert resolve_odds_player("Allan Andrade Elias", CITY_NAPOLI) == 13  # Unibet


def test_lettre_scandinave():
    assert resolve_odds_player("Rasmus Hojlund", CITY_NAPOLI) == 12
    assert resolve_odds_player("Rasmus Højlund", CITY_NAPOLI) == 12


def test_prenom_long_et_court():
    assert resolve_odds_player("Philip Foden", CITY_NAPOLI) == 10
    assert resolve_odds_player("Phil Foden", CITY_NAPOLI) == 10


def test_accent_prenom():
    assert resolve_odds_player("Jeremy Doku", CITY_NAPOLI) == 11


def test_prenom_incompatible_rend_none():
    # Même nom de famille, prénom sans rapport : on ne devine pas.
    assert resolve_odds_player("Kevin Doku", CITY_NAPOLI) is None


def test_meme_nom_dans_les_deux_equipes_ambigu():
    squad = [SquadPlayer(1, "Mohamed Salah"), SquadPlayer(2, "Mohamed Salah")]
    assert resolve_odds_player("Mohamed Salah", squad) is None


# ── Écriture des cotes : player_api_id renseigné ─────────────────────────────

def _scrape_result(names: list[str]):
    from app.ingestion.scrape_result import MatchScrapeResult, PlayerOdds

    return MatchScrapeResult(
        fixture_id=42,
        home_team="Manchester City", away_team="Napoli",
        kickoff_utc=None, league="champions_league",
        bookmaker="unibet",
        scraped_at=datetime(2026, 11, 23, 17, 0, tzinfo=UTC),
        goalscorer=[PlayerOdds(n, 3.0) for n in names],
    )


@pytest.mark.asyncio
async def test_storage_ecrit_player_api_id():
    from app.ingestion import odds_storage

    captured: list = []
    session = AsyncMock()

    async def fake_execute(stmt):
        captured.append(stmt)
        return MagicMock(rowcount=1)

    session.execute = fake_execute
    session.commit = AsyncMock()

    loader = AsyncMock(return_value=CITY_NAPOLI)
    with patch.object(odds_storage, "load_fixture_squad", loader):
        cache: dict = {}
        await odds_storage.store_match_scrape_result(
            _scrape_result(["Samba, Floyd", "Samba", "Philip Foden"]), session, squad_cache=cache,
        )
        # Deuxième bookmaker, même match : l'effectif n'est pas rechargé.
        await odds_storage.store_match_scrape_result(
            _scrape_result(["Phil Foden"]), session, squad_cache=cache,
        )

    assert loader.await_count == 1
    params = captured[0].compile().params
    by_name = {
        params[k]: params[k.replace("player_name", "player_api_id")]
        for k in params if k.startswith("player_name")
    }
    assert by_name == {"Samba, Floyd": 14, "Samba": None, "Philip Foden": 10}
    # Le nom du bookmaker reste stocké tel quel.
    assert "Samba, Floyd" in by_name


def test_player_row_porte_player_api_id():
    from app.ingestion.odds_storage import _player_row

    r = _scrape_result([])
    row = _player_row(r, "goalscorer", "F. Samba", 3.0, r.scraped_at, CITY_NAPOLI)
    assert row["player_api_id"] == 14
    assert row["player_name"] == "F. Samba"


def test_effectif_meme_source_que_le_calculateur():
    """Club = club_squad_filter ; international = national_team_api_id (comme le pricing)."""
    import inspect

    from app.services import player_identity

    src = inspect.getsource(player_identity.load_fixture_squad)
    assert "club_squad_filter" in src
    assert "national_team_api_id" in src
    assert "INTERNATIONAL_LEAGUES" in src
    assert "loan_team_api_id" not in src


# ── Recos : appariement par player_api_id ────────────────────────────────────

def _alloc(player_id: int, name: str, team: str = "Manchester City"):
    return SimpleNamespace(
        player_id=player_id, player_name=name, team=team, position="FW",
        fair_odds_goal=2.0, prob_goal=0.5, lambda_total=0.69, has_form_goal=True,
        fair_odds_goal_supersub=2.0, p_goal_supersub=0.5,
        fair_odds_assist=4.0, prob_assist=0.25, lambda_assist=0.29, has_form_assist=True,
        fair_odds_assist_supersub=4.0, p_assist_supersub=0.25,
        matches_played=12, is_pen_taker=False,
    )


def _odds(name: str, api_id: int | None, odds: float = 3.0, bookmaker: str = "unibet"):
    o = MagicMock()
    o.player_name = name
    o.player_api_id = api_id
    o.market_type = "goalscorer"
    o.odds = odds
    o.bookmaker = bookmaker
    o.scraped_at = datetime.now(UTC)
    return o


async def _run_process(odds_rows, allocs, existing_recs=None):
    from app.services.recommendation_service import process_scraped_fixtures

    fixture = MagicMock()
    fixture.id = 42
    fixture.home_team = "Manchester City"
    fixture.away_team = "Napoli"
    fixture.kickoff_utc = datetime(2026, 11, 23, 20, 0, tzinfo=UTC)

    pricing = MagicMock()
    pricing.home_match_xg = 1.8
    pricing.away_match_xg = 1.0
    pricing.xg_source = "market_implied"
    pricing.p00 = 0.06
    pricing.home_players = allocs
    pricing.away_players = []

    session = AsyncMock()
    added: list = []
    session.add = lambda rec: added.append(rec)
    session.commit = AsyncMock()
    calls = 0

    async def fake_execute(stmt):
        nonlocal calls
        calls += 1
        res = MagicMock()
        if calls == 1:
            res.scalars.return_value.all.return_value = [fixture]
        elif calls == 2:
            res.scalars.return_value.all.return_value = existing_recs or []
        elif calls == 4:
            res.scalars.return_value.all.return_value = odds_rows
        else:
            res.scalars.return_value.all.return_value = []
        return res

    session.execute = fake_execute
    alert = MagicMock()
    with patch(
        "app.services.recommendation_service.load_match_pricing",
        new=AsyncMock(return_value=pricing),
    ), patch("app.services.recommendation_service.alert_bg", alert), patch(
        "app.notifications.notify_value_movements", new=AsyncMock(),
    ):
        stats = await process_scraped_fixtures([42], session)
    return stats, added, alert


@pytest.mark.asyncio
async def test_reco_appariee_par_id_et_player_id_renseigne():
    allocs = [_alloc(14, "Floyd Samba"), _alloc(15, "Tyrone Samba")]
    rows = [_odds("Samba, Floyd", 14)]
    stats, added, alert = await _run_process(rows, allocs)

    player_recs = [r for r in added if r.market_type != "h2h"]
    assert player_recs, "une value sur Floyd Samba doit produire une reco"
    assert {r.player_id for r in player_recs} == {14}
    # Nom stocké = nom Atlas (allocation), stable quel que soit le bookmaker.
    assert {r.player_name for r in player_recs} == {"Floyd Samba"}
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_meilleure_cote_regroupee_par_joueur_entre_bookmakers():
    allocs = [_alloc(10, "Phil Foden")]
    rows = [
        _odds("Philip Foden", 10, odds=3.0, bookmaker="unibet"),
        _odds("Phil Foden", 10, odds=3.4, bookmaker="betclic"),
    ]
    _, added, _ = await _run_process(rows, allocs)
    std = [r for r in added if r.market_type == "standard"]
    assert len(std) == 1
    assert std[0].best_odds == 3.4
    assert std[0].best_bookmaker == "betclic"


@pytest.mark.asyncio
async def test_cote_non_reliee_pas_de_reco_et_alerte_au_dela_de_10pc():
    allocs = [_alloc(14, "Floyd Samba"), _alloc(15, "Tyrone Samba")]
    rows = [_odds("Samba", None), _odds("Samba, Floyd", 14)]
    _, added, alert = await _run_process(rows, allocs)

    assert all(r.player_id == 14 for r in added if r.market_type != "h2h")
    alert.assert_called_once()
    message, channel = alert.call_args.args
    assert channel == "incidents"
    assert "Samba" in message
    assert "Manchester City" in message


@pytest.mark.asyncio
async def test_vieille_cote_non_reliee_ne_declenche_pas_d_alerte():
    from datetime import timedelta

    allocs = [_alloc(14, "Floyd Samba")]
    stale = _odds("Samba", None)
    stale.scraped_at = datetime.now(UTC) - timedelta(days=3)
    _, _, alert = await _run_process([stale, _odds("Samba, Floyd", 14)], allocs)
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_pas_d_alerte_sous_10pc():
    allocs = [_alloc(i, f"Joueur {i}") for i in range(1, 12)]
    rows = [_odds(f"Joueur {i}", i, odds=1.5) for i in range(1, 11)] + [_odds("Inconnu", None)]
    _, _, alert = await _run_process(rows, allocs)
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_reco_existante_sous_nom_bookmaker_est_retrouvee():
    """Ancienne reco stockée avec la graphie bookmaker : mise à jour, pas de doublon."""
    from app.models.recommendations import MarketType

    old = MagicMock()
    old.fixture_id = 42
    old.player_id = None
    old.player_name = "Jeremy Doku"
    old.market_type = MarketType.STANDARD
    old.bet_type = "goalscorer"
    old.status = "pending"
    old.best_odds = 2.5
    old.edge = 0.25
    old.alerted_odds = 2.5

    allocs = [_alloc(11, "Jérémy Doku")]
    rows = [_odds("Jeremy Doku", 11, odds=3.0)]
    stats, added, _ = await _run_process(rows, allocs, existing_recs=[old])

    assert not [r for r in added if r.market_type == MarketType.STANDARD]
    assert old.best_odds == 3.0
    assert old.player_id == 11
    assert old.status == "pending"
