"""Tests for the Beta pricing module (app.pricing.beta_pricing).

Beta reuses team_xg.py's pure engine (compute_player_shares + allocate_player)
fed with career-blended rhythm instead of the mono-season rate, in the same
match context (team xG, pen taker, p_sub/avg_sub_time) as Alpha.

Covers:
  1. _apply_blended_rhythm — overrides npxg/xa rates only when a blended
     rhythm exists; tracks which player_ids got one.
  2. _build_beta_allocations — literal Alpha fallback for players without a
     blended rhythm; freshly computed allocation otherwise.
  3. compute_beta_allocations — full orchestration (player pool reload,
     pen-taker, budget, per-team wiring), player pool loading mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pricing.beta_pricing import (
    _apply_blended_rhythm,
    _build_beta_allocations,
    compute_beta_allocations,
)
from app.pricing.career_blend import BlendedRhythm
from app.pricing.team_xg import PlayerAllocation

MODULE = "app.pricing.beta_pricing"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _player_dict(
    player_id: int,
    name: str,
    position: str = "CF_lone",
    npxg_per_90: float = 0.30,
    xa_per_90: float = 0.10,
    matches_played: int = 20,
    minutes_played: int = 1800,
) -> dict:
    return {
        "player_id": player_id,
        "player_name": name,
        "position": position,
        "matches_played": matches_played,
        "minutes_played": minutes_played,
        "npxg_per_90": npxg_per_90,
        "xg_per_90": npxg_per_90,
        "xa_per_90": xa_per_90,
        "npxg_total": npxg_per_90 * matches_played,
        "goals_total": 5,
        "xa_total": xa_per_90 * matches_played,
        "assists_total": 3,
        "has_bzz_stats": True,
    }


def _alpha_alloc(
    player_id: int,
    p_sub: float = 0.35,
    avg_sub_time: float = 65.0,
    fair_odds_goal_supersub: float = 5.0,
    p_goal_supersub: float = 0.18,
) -> PlayerAllocation:
    """A fully-formed Alpha PlayerAllocation, distinguishable by its supersub
    fields, so fallback tests can assert on identity/equality precisely."""
    return PlayerAllocation(
        player_id=player_id,
        player_name=f"Player {player_id}",
        team="Home FC",
        position="CF_lone",
        expected_minutes=75.0,
        is_pen_taker=False,
        npxg_share=0.2,
        xa_share=0.1,
        quality_multiplier=1.0,
        lambda_open_play=0.3,
        lambda_penalty=0.0,
        lambda_total=0.3,
        prob_goal=0.26,
        fair_odds_goal=3.85,
        creation_multiplier=1.0,
        lambda_assist=0.15,
        prob_assist=0.14,
        fair_odds_assist=7.1,
        p_sub=p_sub,
        avg_sub_time=avg_sub_time,
        fair_odds_goal_supersub=fair_odds_goal_supersub,
        p_goal_supersub=p_goal_supersub,
    )


# ---------------------------------------------------------------------------
# _apply_blended_rhythm
# ---------------------------------------------------------------------------


class TestApplyBlendedRhythm:
    @pytest.mark.asyncio
    async def test_overrides_rates_when_blended_rhythm_available(self):
        players = [_player_dict(1, "Has Career", npxg_per_90=0.30, xa_per_90=0.10)]
        rhythm = BlendedRhythm(
            goal_rate_per_90=0.55, assist_rate_per_90=0.22, seasons_used=3, has_career=True
        )
        with patch(f"{MODULE}.blended_rhythm", new=AsyncMock(return_value=rhythm)):
            out, ids_with_career = await _apply_blended_rhythm(AsyncMock(), players)

        assert ids_with_career == {1}
        assert out[0]["npxg_per_90"] == pytest.approx(0.55)
        assert out[0]["xg_per_90"] == pytest.approx(0.55)
        assert out[0]["xa_per_90"] == pytest.approx(0.22)

    @pytest.mark.asyncio
    async def test_keeps_original_rate_when_no_blended_rhythm(self):
        players = [_player_dict(2, "No Career", npxg_per_90=0.30, xa_per_90=0.10)]
        with patch(f"{MODULE}.blended_rhythm", new=AsyncMock(return_value=None)):
            out, ids_with_career = await _apply_blended_rhythm(AsyncMock(), players)

        assert ids_with_career == set()
        assert out[0]["npxg_per_90"] == pytest.approx(0.30)
        assert out[0]["xa_per_90"] == pytest.approx(0.10)

    @pytest.mark.asyncio
    async def test_does_not_mutate_input_dicts(self):
        players = [_player_dict(1, "P", npxg_per_90=0.30, xa_per_90=0.10)]
        rhythm = BlendedRhythm(
            goal_rate_per_90=0.99, assist_rate_per_90=0.99, seasons_used=1, has_career=True
        )
        with patch(f"{MODULE}.blended_rhythm", new=AsyncMock(return_value=rhythm)):
            await _apply_blended_rhythm(AsyncMock(), players)

        assert players[0]["npxg_per_90"] == pytest.approx(0.30)


# ---------------------------------------------------------------------------
# _build_beta_allocations
# ---------------------------------------------------------------------------


class TestBuildBetaAllocations:
    def test_player_without_career_falls_back_to_literal_alpha_alloc(self):
        players_beta = [
            _player_dict(1, "No Career", npxg_per_90=0.30, xa_per_90=0.10),
            _player_dict(2, "Has Career", npxg_per_90=0.80, xa_per_90=0.10),
        ]
        alpha_alloc_1 = _alpha_alloc(1, fair_odds_goal_supersub=4.2, p_goal_supersub=0.21)
        alpha_by_id = {1: alpha_alloc_1, 2: _alpha_alloc(2)}

        out = _build_beta_allocations(
            players_beta,
            ids_with_career={2},  # only player 2 has a blended rhythm
            team="Home FC",
            lambda_team=1.5,
            pen_id=None,
            budget_assists=1.5 * 0.5,
            alpha_by_id=alpha_by_id,
        )

        # Player 1 (no career): identical object to Alpha's allocation.
        assert out[1] is alpha_alloc_1
        assert out[1].fair_odds_goal_supersub == pytest.approx(4.2)

    def test_player_with_career_gets_freshly_computed_allocation(self):
        players_beta = [
            _player_dict(1, "No Career", npxg_per_90=0.30, xa_per_90=0.10),
            _player_dict(2, "Has Career", npxg_per_90=0.80, xa_per_90=0.10),
        ]
        alpha_alloc_2 = _alpha_alloc(2, fair_odds_goal_supersub=5.0, p_goal_supersub=0.18)
        alpha_by_id = {1: _alpha_alloc(1), 2: alpha_alloc_2}

        out = _build_beta_allocations(
            players_beta,
            ids_with_career={2},
            team="Home FC",
            lambda_team=1.5,
            pen_id=None,
            budget_assists=1.5 * 0.5,
            alpha_by_id=alpha_by_id,
        )

        # Player 2 (career, boosted npxg_per_90 0.30->0.80): a NEW allocation
        # object, not reused from Alpha, and priced shorter (higher goal prob)
        # given the much higher blended rhythm.
        assert out[2] is not alpha_alloc_2
        assert out[2].fair_odds_goal_supersub < alpha_alloc_2.fair_odds_goal_supersub

    def test_reuses_alpha_p_sub_and_avg_sub_time_for_computed_player(self):
        players_beta = [_player_dict(2, "Has Career", npxg_per_90=0.80, xa_per_90=0.10)]
        alpha_alloc_2 = _alpha_alloc(2, p_sub=0.42, avg_sub_time=58.0)

        out = _build_beta_allocations(
            players_beta,
            ids_with_career={2},
            team="Home FC",
            lambda_team=1.5,
            pen_id=None,
            budget_assists=0.75,
            alpha_by_id={2: alpha_alloc_2},
        )

        assert out[2].p_sub == pytest.approx(0.42)
        assert out[2].avg_sub_time == pytest.approx(58.0)

    def test_no_alpha_match_still_computes_with_defaults(self):
        """If a player_id has no Alpha counterpart (edge case), compute Beta
        with default p_sub/avg_sub_time rather than crashing."""
        players_beta = [_player_dict(9, "Orphan", npxg_per_90=0.4, xa_per_90=0.1)]

        out = _build_beta_allocations(
            players_beta,
            ids_with_career={9},
            team="Home FC",
            lambda_team=1.5,
            pen_id=None,
            budget_assists=0.75,
            alpha_by_id={},
        )

        assert out[9].p_sub == pytest.approx(0.35)
        assert out[9].avg_sub_time == pytest.approx(65.0)

    def test_is_pen_taker_set_from_pen_id(self):
        players_beta = [_player_dict(3, "Taker", npxg_per_90=0.4, xa_per_90=0.1)]

        out = _build_beta_allocations(
            players_beta,
            ids_with_career={3},
            team="Home FC",
            lambda_team=1.5,
            pen_id=3,
            budget_assists=0.75,
            alpha_by_id={},
        )

        assert out[3].is_pen_taker is True


# ---------------------------------------------------------------------------
# compute_beta_allocations — full orchestration
# ---------------------------------------------------------------------------


def _make_fixture(league: str = "ligue_1") -> MagicMock:
    fx = MagicMock()
    fx.id = 1
    fx.league = league
    fx.home_team = "Home FC"
    fx.away_team = "Away FC"
    fx.home_bzz_team_id = None
    fx.away_bzz_team_id = None
    fx.external_id = None
    return fx


class TestComputeBetaAllocationsFullFlow:
    @pytest.mark.asyncio
    async def test_club_fixture_uses_load_team_players_and_wires_both_teams(self):
        fixture = _make_fixture(league="ligue_1")
        db = AsyncMock()

        home_players = [
            _player_dict(1, "Home No Career", npxg_per_90=0.30, xa_per_90=0.10),
            _player_dict(2, "Home Has Career", npxg_per_90=0.30, xa_per_90=0.10),
        ]
        away_players = [_player_dict(3, "Away Has Career", npxg_per_90=0.20, xa_per_90=0.05)]

        async def fake_load_team_players(_db, team, bzz_team_id=None):
            return home_players if team == "Home FC" else away_players

        rhythm = BlendedRhythm(
            goal_rate_per_90=0.90, assist_rate_per_90=0.30, seasons_used=2, has_career=True
        )

        async def fake_blended_rhythm(_db, player_api_id):
            # Player 1 has no career; players 2 and 3 do.
            return None if player_api_id == 1 else rhythm

        alpha_home = [_alpha_alloc(1, fair_odds_goal_supersub=6.0), _alpha_alloc(2, fair_odds_goal_supersub=6.0)]
        alpha_away = [_alpha_alloc(3, fair_odds_goal_supersub=9.0)]

        with patch(f"{MODULE}._load_team_players", new=AsyncMock(side_effect=fake_load_team_players)), \
             patch(f"{MODULE}.blended_rhythm", new=AsyncMock(side_effect=fake_blended_rhythm)):
            home_beta, away_beta = await compute_beta_allocations(
                db, fixture, alpha_home, alpha_away,
                home_match_xg=1.6, away_match_xg=1.2,
            )

        # Player 1: no career -> Beta strictly equals Alpha (literal fallback).
        assert home_beta[1] is alpha_home[0]
        # Player 2: has career -> recomputed, distinct from Alpha's alloc.
        assert home_beta[2] is not alpha_home[1]
        # Away side wired too.
        assert away_beta[3] is not alpha_away[0]

    @pytest.mark.asyncio
    async def test_pen_taker_override_applied_on_beta_side(self):
        fixture = _make_fixture(league="ligue_1")
        db = AsyncMock()
        home_players = [_player_dict(1, "P1"), _player_dict(2, "P2")]

        async def fake_load_team_players(_db, team, bzz_team_id=None):
            return home_players if team == "Home FC" else []

        with patch(f"{MODULE}._load_team_players", new=AsyncMock(side_effect=fake_load_team_players)), \
             patch(f"{MODULE}.blended_rhythm", new=AsyncMock(return_value=None)):
            home_beta, _away_beta = await compute_beta_allocations(
                db, fixture, [_alpha_alloc(1)], [_alpha_alloc(2)],
                home_match_xg=1.5, away_match_xg=1.0,
                home_pen_taker_override=2,
            )

        # Both players fall back to Alpha here (no career) so pen_id itself
        # doesn't change output, but this exercises the override wiring path
        # without raising — pen_id computation happens before the fallback
        # check, so no crash is the primary assertion.
        assert set(home_beta.keys()) == {1, 2}
