"""Source unique Atlas : fusion des stats, filtre d'effectif, ecussons."""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.pricing import team_xg
from app.services import atlas_source
from app.services.atlas_source import club_squad_filter, merge_season_stats


def _row(**kw):
    base = dict.fromkeys(["matches_played", "minutes_played", "starts", "goals", "goal_assist", "expected_goals", "expected_assists", "total_shots", "shots_on_target", "key_pass", "total_cross", "accurate_cross", "total_pass", "accurate_pass", "total_long_balls", "accurate_long_balls", "duel_won", "duel_lost", "aerial_won", "aerial_lost", "total_tackle", "won_tackle", "interception", "ball_recovery", "yellow_card", "red_card", "saves", "avg_rating", "form_xg_5", "form_rating_5", "form_goals_5", "form_assists_5", "rating_trend", "league_api_id"])
    base.update(kw)
    return SimpleNamespace(**base)


def test_merge_season_stats_somme_les_competitions():
    rows = [
        _row(matches_played=10, minutes_played=900, goals=3, expected_goals=2.5, league_api_id=1),
        _row(matches_played=4, minutes_played=300, goals=1, expected_goals=0.7, league_api_id=2),
    ]
    m = merge_season_stats(rows, "2025-2026")
    assert m["minutes_played"] == 1200
    assert m["goals"] == 4
    assert m["xg_per_90"] == pytest.approx(3.2 / 1200 * 90)


def test_merge_season_stats_vide():
    assert merge_season_stats([], "2025-2026") is None


def test_club_squad_filter_ignore_le_pret():
    sql = str(club_squad_filter(42).compile(dialect=postgresql.dialect()))
    assert "loan_team_api_id" not in sql
    assert "current_team_api_id" in sql


def test_team_xg_sans_rapprochement_par_nom():
    src = inspect.getsource(team_xg)
    assert "_TEAM_NAME_ALIASES" not in src
    assert "contains(func.lower(" not in src
    assert "club_squad_filter" in inspect.getsource(team_xg._load_team_players)


def test_load_team_players_sans_id_alerte(monkeypatch):
    calls = []
    monkeypatch.setattr(team_xg, "alert_bg", lambda msg, channel: calls.append((msg, channel)))

    class Boom:
        async def execute(self, *a, **k):
            raise AssertionError("la base ne doit pas etre touchee")

    out = asyncio.run(team_xg._load_team_players(Boom(), "X", bzz_team_id=None))
    assert out == []
    assert calls == [("Calculateur : équipe non reliée à l'Atlas — X", "incidents")]


def test_assess_inputs_equipe_non_reliee():
    w = team_xg.assess_inputs([], "X")
    assert any("non reliée à l'Atlas" in x.detail for x in w)


def test_team_badge_forme():
    b = atlas_source.TeamBadge("A", "u", 1, "FR")
    assert b.country_iso == "FR"


def test_team_badges_mappe_les_lignes_et_filtre_les_ids():
    from unittest.mock import AsyncMock, MagicMock

    res = MagicMock()
    res.all.return_value = [
        (63, "Milan", "http://c/63.png", 489, "Italy"),
        (99, None, None, None, None),
    ]
    db = MagicMock()
    db.execute = AsyncMock(return_value=res)
    out = asyncio.run(atlas_source.team_badges(db, [63, None, 99, 63]))
    assert out[63] == atlas_source.TeamBadge("Milan", "http://c/63.png", 489, "IT")
    assert out[99] == atlas_source.TeamBadge(None, None, None, None)
    assert db.execute.await_count == 1
    sql = str(db.execute.call_args[0][0].compile(dialect=postgresql.dialect()))
    assert "bzz_team_id IN" in sql


def test_team_badges_vide_sans_requete():
    from unittest.mock import AsyncMock, MagicMock

    db = MagicMock()
    db.execute = AsyncMock()
    assert asyncio.run(atlas_source.team_badges(db, [None])) == {}
    assert asyncio.run(atlas_source.team_badges(db, [])) == {}
    db.execute.assert_not_awaited()
