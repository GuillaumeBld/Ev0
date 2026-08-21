"""Tests for sync_player_stats module."""
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.ingestion.bzzoiro.sync_player_stats as mod
from app.ingestion.bzzoiro.sync_player_stats import (
    build_stat_values,
    compute_derived_metrics,
    sync_player_stats_for_event,
)


def test_compute_derived_metrics_normal():
    row = {
        "total_shots": 5,
        "shots_on_target": 3,
        "expected_goals": 1.5,
        "goals": 2,
        "goal_assist": 1,
        "expected_assists": 0.8,
        "total_pass": 40,
        "accurate_pass": 32,
        "total_long_balls": None,
        "accurate_long_balls": None,
        "total_cross": None,
        "accurate_cross": None,
        "duel_won": 3,
        "duel_lost": 2,
        "aerial_won": None,
        "aerial_lost": None,
        "won_tackle": 4,
        "total_tackle": 5,
    }
    result = compute_derived_metrics(row)

    assert result["shot_accuracy"] == pytest.approx(0.6)
    assert result["xg_per_shot"] == pytest.approx(0.3)
    assert result["finishing_delta"] == pytest.approx(0.5)
    assert result["xa_delta"] == pytest.approx(0.2)
    assert result["pass_completion"] == pytest.approx(0.8)
    assert result["duel_win_rate"] == pytest.approx(0.6)
    assert result["tackle_success_rate"] == pytest.approx(0.8)
    # None denominators
    assert result["long_ball_accuracy"] is None
    assert result["cross_accuracy"] is None
    assert result["aerial_win_rate"] is None


def test_compute_derived_metrics_zero_denominator():
    row = {
        "total_shots": 0,
        "shots_on_target": None,
        "expected_goals": None,
        "goals": None,
        "goal_assist": None,
        "expected_assists": None,
        "total_pass": 0,
        "accurate_pass": None,
        "total_long_balls": None,
        "accurate_long_balls": None,
        "total_cross": None,
        "accurate_cross": None,
        "duel_won": 0,
        "duel_lost": 0,
        "aerial_won": None,
        "aerial_lost": None,
        "won_tackle": None,
        "total_tackle": 0,
    }
    result = compute_derived_metrics(row)

    assert result["shot_accuracy"] is None
    assert result["xg_per_shot"] is None
    assert result["finishing_delta"] is None
    assert result["xa_delta"] is None
    assert result["pass_completion"] is None
    assert result["long_ball_accuracy"] is None
    assert result["cross_accuracy"] is None
    assert result["duel_win_rate"] is None
    assert result["aerial_win_rate"] is None
    assert result["tackle_success_rate"] is None


def test_compute_derived_metrics_partial_duel():
    row = {
        "total_shots": None, "shots_on_target": None,
        "expected_goals": None, "goals": None,
        "goal_assist": None, "expected_assists": None,
        "total_pass": None, "accurate_pass": None,
        "total_long_balls": None, "accurate_long_balls": None,
        "total_cross": None, "accurate_cross": None,
        "duel_won": 5, "duel_lost": None,
        "aerial_won": None, "aerial_lost": 2,
        "won_tackle": None, "total_tackle": None,
    }
    result = compute_derived_metrics(row)
    assert result["duel_win_rate"] is None
    assert result["aerial_win_rate"] is None


# --- Ingestion par match ---------------------------------------------------
#
# L'endpoint accepte ?event=<id> et rend en une page les joueurs des deux
# equipes, chacun portant son identite sous la cle "player". Les cles "team"
# et "is_home" sont absentes : le camp se deduit en comparant player.team a
# event.home_team.


def _ligne(player_id: int, club: str, **extra):
    base = {
        "event": {"id": 223384, "home_team": "FC Schalke 04", "away_team": "Real Madrid"},
        "player": {"id": player_id, "name": f"Joueur {player_id}", "team": club},
        "minutes_played": 90, "goals": 1, "goal_assist": 0,
        "expected_goals": 0.4, "expected_assists": 0.1,
        "total_shots": 2, "shots_on_target": 1,
        "total_pass": 30, "accurate_pass": 24,
    }
    base.update(extra)
    return base


def _session():
    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def joueur_toujours_present(monkeypatch):
    """Neutralise la creation de joueur : on teste l'ingestion, pas l'upsert."""
    async def _stub(session, player):
        return player["id"]

    monkeypatch.setattr(mod, "ensure_player_exists", _stub)


def test_build_stat_values_domicile():
    v = build_stat_values(_ligne(27598, "FC Schalke 04"), 223384, 500, True)
    assert v["player_api_id"] == 27598
    assert v["event_api_id"] == 223384
    assert v["team_api_id"] == 500
    assert v["is_home"] is True
    assert v["minutes_played"] == 90
    # les metriques derivees sont bien fusionnees dans la meme ligne
    assert v["shot_accuracy"] == pytest.approx(0.5)


def test_build_stat_values_exterieur():
    v = build_stat_values(_ligne(594, "Real Madrid"), 223384, 600, False)
    assert v["team_api_id"] == 600
    assert v["is_home"] is False


async def test_sync_par_match_rattache_chaque_joueur_a_son_camp(joueur_toujours_present):
    rows = [_ligne(27598, "FC Schalke 04"), _ligne(594, "Real Madrid")]
    client = MagicMock()
    client.get_all = AsyncMock(return_value=rows)

    ecrites = []
    session = _session()

    async def _capture(stmt):
        ecrites.append(stmt.compile().params)

    session.execute = AsyncMock(side_effect=_capture)

    count = await sync_player_stats_for_event(
        session, client, event_api_id=223384,
        home_team_api_id=500, away_team_api_id=600,
    )

    assert count == 2
    client.get_all.assert_called_once_with("/api/player-stats/", {"event": 223384})
    par_joueur = {p["player_api_id"]: p for p in ecrites}
    assert par_joueur[27598]["is_home"] is True
    assert par_joueur[27598]["team_api_id"] == 500
    assert par_joueur[594]["is_home"] is False
    assert par_joueur[594]["team_api_id"] == 600


async def test_sync_par_match_ignore_une_ligne_sans_identite(joueur_toujours_present):
    rows = [_ligne(27598, "FC Schalke 04"), {"event": {"id": 223384}, "minutes_played": 12}]
    client = MagicMock()
    client.get_all = AsyncMock(return_value=rows)

    count = await sync_player_stats_for_event(
        _session(), client, event_api_id=223384,
        home_team_api_id=500, away_team_api_id=600,
    )
    assert count == 1


async def test_sync_par_match_club_inconnu_laisse_le_camp_vide(joueur_toujours_present):
    """Un club ne correspondant a aucun camp ne se voit pas attribuer au hasard."""
    rows = [_ligne(999, "Club Fantome")]
    client = MagicMock()
    client.get_all = AsyncMock(return_value=rows)

    ecrites = []
    session = _session()

    async def _capture(stmt):
        ecrites.append(stmt.compile().params)

    session.execute = AsyncMock(side_effect=_capture)

    count = await sync_player_stats_for_event(
        session, client, event_api_id=223384,
        home_team_api_id=500, away_team_api_id=600,
    )
    assert count == 1
    assert ecrites[0]["is_home"] is None
    assert ecrites[0]["team_api_id"] is None
    # le reste des statistiques est conserve
    assert ecrites[0]["minutes_played"] == 90


async def test_sync_par_match_sans_stats_n_ecrit_rien():
    client = MagicMock()
    client.get_all = AsyncMock(return_value=[])
    session = _session()

    count = await sync_player_stats_for_event(
        session, client, event_api_id=999,
        home_team_api_id=500, away_team_api_id=600,
    )
    assert count == 0
    session.commit.assert_not_called()


async def test_sync_par_match_cree_le_joueur_manquant():
    """bzz_player_match_stats.player_api_id est une FK : sans creation, echec."""
    crees = []

    async def _stub(session, player):
        crees.append(player["id"])
        return player["id"]

    client = MagicMock()
    client.get_all = AsyncMock(return_value=[_ligne(27598, "FC Schalke 04")])

    import unittest.mock as um
    with um.patch.object(mod, "ensure_player_exists", _stub):
        await sync_player_stats_for_event(
            _session(), client, event_api_id=223384,
            home_team_api_id=500, away_team_api_id=600,
        )

    assert crees == [27598]
