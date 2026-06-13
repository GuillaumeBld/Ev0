import pytest
from app.models.recommendations import MarketType


def make_allocation(
    player_name="Mbappé",
    fixture_id="bzz_123",
    position="FW",
    prob_goal=0.28, fair_odds_goal=3.57,
    prob_assist=0.12, fair_odds_assist=8.33,
    p_goal_supersub=0.33, fair_odds_goal_supersub=3.03,
    p_assist_supersub=0.14, fair_odds_assist_supersub=7.14,
    p_sub=0.60, avg_sub_time=65.0,
    lambda_total=0.32, lambda_assist=0.13,
):
    from unittest.mock import MagicMock
    alloc = MagicMock()
    alloc.player_name = player_name
    alloc.fixture_id = fixture_id
    alloc.position = position
    # Standard pricing (real allocation field names)
    alloc.prob_goal = prob_goal
    alloc.fair_odds_goal = fair_odds_goal
    alloc.prob_assist = prob_assist
    alloc.fair_odds_assist = fair_odds_assist
    # Supersub pricing
    alloc.p_goal_supersub = p_goal_supersub
    alloc.fair_odds_goal_supersub = fair_odds_goal_supersub
    alloc.p_assist_supersub = p_assist_supersub
    alloc.fair_odds_assist_supersub = fair_odds_assist_supersub
    # Sub stats
    alloc.p_sub = p_sub
    alloc.avg_sub_time = avg_sub_time
    # Lambda
    alloc.lambda_total = lambda_total
    alloc.lambda_assist = lambda_assist
    return alloc


def test_generates_standard_and_supersub_recommendations():
    from app.services.recommendations_service import build_recommendations_for_player
    alloc = make_allocation()
    market_odds = {
        "goal_standard": 4.00, "goal_supersub": 3.50,
        "assist_standard": 9.00, "assist_supersub": 8.00,
    }
    recos = build_recommendations_for_player(alloc, market_odds)
    market_types = {r.market_type for r in recos}
    assert MarketType.STANDARD in market_types
    assert MarketType.SUPERSUB in market_types


def test_supersub_has_higher_or_equal_prob_than_standard():
    from app.services.recommendations_service import build_recommendations_for_player
    alloc = make_allocation()
    market_odds = {
        "goal_standard": 4.50, "goal_supersub": 4.00,
        "assist_standard": 11.0, "assist_supersub": 9.50,
    }
    recos = build_recommendations_for_player(alloc, market_odds)
    goal_std = next((r for r in recos if r.market_type == MarketType.STANDARD and r.bet_type == "goal"), None)
    goal_ss  = next((r for r in recos if r.market_type == MarketType.SUPERSUB  and r.bet_type == "goal"), None)
    if goal_std and goal_ss:
        assert goal_ss.fair_probability >= goal_std.fair_probability


def test_ev_calculated_correctly():
    from app.services.recommendations_service import build_recommendations_for_player
    alloc = make_allocation(prob_goal=0.28, fair_odds_goal=3.57)
    market_odds = {"goal_standard": 4.00, "goal_supersub": 3.50, "assist_standard": 0, "assist_supersub": 0}
    recos = build_recommendations_for_player(alloc, market_odds)
    std_goal = next(r for r in recos if r.market_type == MarketType.STANDARD and r.bet_type == "goal")
    expected_ev = round(0.28 * 4.00 - 1.0, 4)
    assert std_goal.ev == pytest.approx(expected_ev, abs=0.001)


def test_skips_zero_odds():
    from app.services.recommendations_service import build_recommendations_for_player
    alloc = make_allocation()
    market_odds = {
        "goal_standard": 4.00, "goal_supersub": 0,
        "assist_standard": 0, "assist_supersub": 0,
    }
    recos = build_recommendations_for_player(alloc, market_odds)
    # Only one reco — standard goal
    assert len(recos) == 1
    assert recos[0].market_type == MarketType.STANDARD
    assert recos[0].bet_type == "goal"


def test_edge_calculated_correctly():
    from app.services.recommendations_service import build_recommendations_for_player
    alloc = make_allocation(prob_goal=0.28, fair_odds_goal=3.57)
    market_odds = {"goal_standard": 4.00, "goal_supersub": 0, "assist_standard": 0, "assist_supersub": 0}
    recos = build_recommendations_for_player(alloc, market_odds)
    std_goal = recos[0]
    expected_edge = round((4.00 / 3.57) - 1.0, 4)
    assert std_goal.edge == pytest.approx(expected_edge, abs=0.001)
