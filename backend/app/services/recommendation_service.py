"""Recommendation generation service.

Combines pricing engine, odds data, and strategy to generate
actionable betting recommendations.
"""

from datetime import datetime, timezone
from typing import Any

from app.pricing.goalscorer import calculate_goalscorer_price, calculate_edge
from app.pricing.assist import calculate_assist_price
from app.strategy.selector import select_bets, RecommendationFilter


async def generate_recommendations(
    fixtures: list[dict[str, Any]],
    player_stats: dict[str, dict[str, Any]],  # player_name -> stats
    odds_data: dict[str, list[dict[str, Any]]],  # fixture_id -> odds list
    filter_config: RecommendationFilter | None = None,
) -> list[dict[str, Any]]:
    """
    Generate betting recommendations for upcoming fixtures.
    
    Pipeline:
    1. For each fixture, get relevant players
    2. For each player, calculate fair price
    3. Compare to market odds, calculate edge
    4. Apply strategy filters and selection
    
    Args:
        fixtures: List of upcoming fixtures
        player_stats: Player stats keyed by normalized name
        odds_data: Market odds keyed by fixture_id
        filter_config: Optional filter configuration
    
    Returns:
        List of recommendation dicts
    """
    all_recommendations = []
    
    for fixture in fixtures:
        fixture_id = fixture.get("fixture_id") or fixture.get("id")
        home_team = fixture.get("home_team")
        away_team = fixture.get("away_team")
        kickoff = fixture.get("kickoff_utc")
        league = fixture.get("league")
        
        # Get odds for this fixture
        fixture_odds = odds_data.get(fixture_id, [])
        
        for odds_entry in fixture_odds:
            player_name = odds_entry.get("player_name")
            market_type = odds_entry.get("market_type", "goalscorer")
            market_odds = odds_entry.get("odds", 0)
            bookmaker = odds_entry.get("bookmaker", "unknown")
            
            if not player_name or market_odds <= 1:
                continue
            
            # Find player stats
            stats = _find_player_stats(player_name, player_stats)
            if not stats:
                continue
            
            # Determine team
            team = stats.get("team") or _infer_team(player_name, home_team, away_team)
            
            # Calculate opponent factor
            opponent = away_team if team == home_team else home_team
            opponent_factor = _get_opponent_factor(opponent, market_type)
            
            # Calculate fair price
            if market_type == "goalscorer":
                pricing = calculate_goalscorer_price(
                    xg_per_90=stats.get("xg_per_90", 0.3),
                    expected_minutes=stats.get("expected_minutes", 75),
                    conversion_rate=stats.get("conversion_rate", 1.0),
                    opponent_xga_factor=opponent_factor,
                    form_factor=stats.get("form_factor", 1.0),
                )
            else:  # assist
                pricing = calculate_assist_price(
                    xa_per_90=stats.get("xa_per_90", 0.15),
                    expected_minutes=stats.get("expected_minutes", 75),
                    creation_score=stats.get("creation_score", 1.0),
                    teammate_finishing_factor=stats.get("teammate_finishing", 1.0),
                    opponent_defense_factor=opponent_factor,
                    form_factor=stats.get("form_factor", 1.0),
                )
            
            # Calculate edge
            fair_odds = pricing["fair_odds"]
            edge = calculate_edge(fair_odds, market_odds)
            
            # Determine classification
            if edge >= 0.10:
                classification = "VALUE"
                confidence = min(0.95, 0.7 + edge)
            elif edge >= 0.05:
                classification = "VALUE"
                confidence = 0.6 + edge
            elif edge >= 0.0:
                classification = "NO_VALUE"
                confidence = 0.5
            else:
                classification = "AVOID"
                confidence = max(0.2, 0.4 + edge)
            
            recommendation = {
                "fixture_id": fixture_id,
                "fixture_name": f"{home_team} vs {away_team}",
                "kickoff_utc": kickoff,
                "league": league,
                "player_name": player_name,
                "team": team,
                "market_type": market_type,
                "fair_probability": pricing["probability"],
                "fair_odds": fair_odds,
                "lambda_intensity": pricing["lambda_intensity"],
                "market_odds": market_odds,
                "best_bookmaker": bookmaker,
                "edge": edge,
                "classification": classification,
                "confidence": confidence,
                "explanation": pricing["explanation"],
            }
            
            all_recommendations.append(recommendation)
    
    # Apply strategy selection
    selection = select_bets(all_recommendations, filter_config)
    
    return selection.selected


def _find_player_stats(
    player_name: str,
    stats_dict: dict[str, dict],
) -> dict[str, Any] | None:
    """Find player stats by name (fuzzy matching)."""
    # Direct match
    if player_name in stats_dict:
        return stats_dict[player_name]
    
    # Normalized match
    normalized = player_name.lower().replace(" ", "-")
    for key, stats in stats_dict.items():
        if key.lower().replace(" ", "-") == normalized:
            return stats
    
    # Partial match (last name)
    parts = player_name.split()
    if parts:
        last_name = parts[-1].lower()
        for key, stats in stats_dict.items():
            if last_name in key.lower():
                return stats
    
    return None


def _infer_team(player_name: str, home_team: str, away_team: str) -> str:
    """Try to infer player's team (placeholder - needs proper mapping)."""
    # This would need a proper player-team mapping
    return home_team


def _get_opponent_factor(opponent: str, market_type: str) -> float:
    """
    Get opponent defensive factor.
    
    > 1.0 = weak defense (good for attacker)
    < 1.0 = strong defense (bad for attacker)
    
    Placeholder - should use actual xGA data.
    """
    # Default to neutral
    return 1.0


async def get_recommendations_for_date(
    target_date: datetime,
    filter_config: RecommendationFilter | None = None,
) -> list[dict[str, Any]]:
    """
    Get recommendations for a specific date.
    
    This is a high-level function that:
    1. Fetches fixtures for the date
    2. Fetches player stats
    3. Fetches latest odds
    4. Generates recommendations
    
    Placeholder - needs actual data layer integration.
    """
    # TODO: Integrate with actual data layer
    # For now, return empty
    return []
