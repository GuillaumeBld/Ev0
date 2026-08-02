"""Tests for the Beta fields on POST /price/match (app.api.pricing.price_match).

Verifies the endpoint contract added on top of the existing (frozen) Alpha
response: for every player, beta_fair_odds_goal_supersub / beta_p_goal_supersub
/ beta_fair_odds_assist_supersub / beta_p_assist_supersub are present, computed
from compute_beta_allocations, and fall back to the Alpha value when no Beta
allocation is available for that player (missing career data, or a Beta
computation failure) — the endpoint must never break, only degrade.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.pricing import MatchPriceRequest, price_match
from app.pricing.team_xg import MatchPricingResult, PlayerAllocation

MODULE = "app.api.pricing"


def _alloc(player_id: int, team: str, fair_odds_goal_supersub: float = 5.0, p_goal_supersub: float = 0.18) -> PlayerAllocation:
    return PlayerAllocation(
        player_id=player_id,
        player_name=f"Player {player_id}",
        team=team,
        position="CF_lone",
        expected_minutes=80.0,
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
        p_goal_supersub=p_goal_supersub,
        fair_odds_goal_supersub=fair_odds_goal_supersub,
        p_assist_supersub=0.12,
        fair_odds_assist_supersub=8.3,
    )


def _make_fixture() -> MagicMock:
    fx = MagicMock()
    fx.id = 42
    fx.home_team = "Home FC"
    fx.away_team = "Away FC"
    return fx


def _make_db(fixture) -> AsyncMock:
    db = AsyncMock()
    fixture_result = MagicMock()
    fixture_result.scalar_one_or_none.return_value = fixture
    db.execute = AsyncMock(return_value=fixture_result)
    return db


def _make_pricing_result(fixture) -> MatchPricingResult:
    return MatchPricingResult(
        fixture_id=fixture.id,
        home_team=fixture.home_team,
        away_team=fixture.away_team,
        home_match_xg=1.6,
        away_match_xg=1.2,
        xg_source="market_implied",
        home_players=[_alloc(1, "Home FC", fair_odds_goal_supersub=6.0, p_goal_supersub=0.15)],
        away_players=[_alloc(2, "Away FC", fair_odds_goal_supersub=9.0, p_goal_supersub=0.10)],
        p00=0.10,
    )


class TestPriceMatchBetaFields:
    @pytest.mark.asyncio
    async def test_beta_fields_populated_from_compute_beta_allocations(self):
        fixture = _make_fixture()
        db = _make_db(fixture)
        pricing = _make_pricing_result(fixture)

        beta_home_alloc = _alloc(1, "Home FC", fair_odds_goal_supersub=3.2, p_goal_supersub=0.28)
        beta_away_alloc = _alloc(2, "Away FC", fair_odds_goal_supersub=7.5, p_goal_supersub=0.12)

        with patch("app.pricing.team_xg.load_match_pricing", new=AsyncMock(return_value=pricing)), \
             patch(
                 "app.pricing.beta_pricing.compute_beta_allocations",
                 new=AsyncMock(return_value=({1: beta_home_alloc}, {2: beta_away_alloc})),
             ):
            response = await price_match(MatchPriceRequest(fixture_id=42), db=db)

        home = response.home_players[0]
        assert home.fair_odds_goal_supersub == pytest.approx(6.0)  # Alpha unchanged
        assert home.beta_fair_odds_goal_supersub == pytest.approx(3.2)  # Beta, distinct
        assert home.beta_p_goal_supersub == pytest.approx(0.28)

        away = response.away_players[0]
        assert away.beta_fair_odds_goal_supersub == pytest.approx(7.5)

    @pytest.mark.asyncio
    async def test_player_without_beta_alloc_falls_back_to_alpha(self):
        """A player_id missing from the beta map (no career data) must get
        beta_* fields identical to their Alpha fields — no gap, no default 99.0."""
        fixture = _make_fixture()
        db = _make_db(fixture)
        pricing = _make_pricing_result(fixture)

        with patch("app.pricing.team_xg.load_match_pricing", new=AsyncMock(return_value=pricing)), \
             patch("app.pricing.beta_pricing.compute_beta_allocations", new=AsyncMock(return_value=({}, {}))):
            response = await price_match(MatchPriceRequest(fixture_id=42), db=db)

        home = response.home_players[0]
        assert home.beta_fair_odds_goal_supersub == pytest.approx(home.fair_odds_goal_supersub)
        assert home.beta_p_goal_supersub == pytest.approx(home.p_goal_supersub)
        assert home.beta_fair_odds_assist_supersub == pytest.approx(home.fair_odds_assist_supersub)
        assert home.beta_p_assist_supersub == pytest.approx(home.p_assist_supersub)

    @pytest.mark.asyncio
    async def test_beta_computation_failure_degrades_to_alpha_without_breaking_endpoint(self):
        """If compute_beta_allocations raises, the endpoint must still return
        the (already computed) Alpha response — beta_* mirrors alpha_*."""
        fixture = _make_fixture()
        db = _make_db(fixture)
        pricing = _make_pricing_result(fixture)

        with patch("app.pricing.team_xg.load_match_pricing", new=AsyncMock(return_value=pricing)), \
             patch("app.pricing.beta_pricing.compute_beta_allocations", new=AsyncMock(side_effect=RuntimeError("boom"))):
            response = await price_match(MatchPriceRequest(fixture_id=42), db=db)

        home = response.home_players[0]
        assert home.beta_fair_odds_goal_supersub == pytest.approx(home.fair_odds_goal_supersub)
        # Alpha response is untouched despite the beta failure.
        assert home.fair_odds_goal_supersub == pytest.approx(6.0)

    @pytest.mark.asyncio
    async def test_alpha_fields_unchanged_backward_compat(self):
        """Existing Alpha fields must be untouched by the beta addition."""
        fixture = _make_fixture()
        db = _make_db(fixture)
        pricing = _make_pricing_result(fixture)

        with patch("app.pricing.team_xg.load_match_pricing", new=AsyncMock(return_value=pricing)), \
             patch("app.pricing.beta_pricing.compute_beta_allocations", new=AsyncMock(return_value=({}, {}))):
            response = await price_match(MatchPriceRequest(fixture_id=42), db=db)

        assert response.home_players[0].fair_odds_goal == pytest.approx(3.85)
        assert response.home_players[0].prob_goal == pytest.approx(0.26)
        assert response.fixture_id == 42
