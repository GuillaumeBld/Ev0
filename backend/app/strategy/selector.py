"""Strategy and selection logic.

Filters recommendations, ranks by value, checks correlations,
and applies exposure limits.
"""

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class RecommendationFilter:
    """Configuration for filtering recommendations."""
    
    # Edge filters
    min_edge: float = 0.05
    max_edge: float = 1.0  # Sanity check
    
    # Confidence filters
    min_confidence: float = 0.50
    
    # Odds filters
    min_odds: float = 1.3
    max_odds: float = 15.0
    
    # Market filters
    markets: list[str] = field(default_factory=lambda: ["goalscorer", "assist"])
    
    # League filters
    leagues: list[str] = field(default_factory=lambda: ["ligue1", "premier_league"])


@dataclass
class SelectionResult:
    """Result of selection process."""
    
    selected: list[dict[str, Any]]
    filtered_out: int
    total_stake: float
    reasons: dict[str, int] = field(default_factory=dict)


def filter_recommendations(
    recommendations: list[dict[str, Any]],
    config: RecommendationFilter,
) -> list[dict[str, Any]]:
    """
    Filter recommendations by config criteria.
    
    Returns recommendations that pass all filters.
    """
    result = []
    
    for rec in recommendations:
        # Edge filter
        edge = rec.get("edge", 0)
        if edge < config.min_edge or edge > config.max_edge:
            continue
        
        # Confidence filter
        confidence = rec.get("confidence", 0)
        if confidence < config.min_confidence:
            continue
        
        # Odds filter
        odds = rec.get("market_odds", 0)
        if odds < config.min_odds or odds > config.max_odds:
            continue
        
        # Market filter
        market = rec.get("market", "")
        if config.markets and market not in config.markets:
            continue
        
        # League filter
        league = rec.get("league", "")
        if config.leagues and league and league not in config.leagues:
            continue
        
        result.append(rec)
    
    return result


def rank_by_value(
    recommendations: list[dict[str, Any]],
    method: Literal["edge", "ev", "composite"] = "composite",
) -> list[dict[str, Any]]:
    """
    Rank recommendations by value metric.
    
    Methods:
    - edge: Sort by raw edge
    - ev: Sort by expected value (edge * odds)
    - composite: Sort by edge * confidence
    """
    def get_sort_key(rec: dict[str, Any]) -> float:
        if method == "edge":
            return rec.get("edge", 0)
        elif method == "ev":
            return rec.get("edge", 0) * rec.get("market_odds", 1)
        else:  # composite
            return rec.get("edge", 0) * rec.get("confidence", 1)
    
    return sorted(recommendations, key=get_sort_key, reverse=True)


def check_correlation(bet1: dict[str, Any], bet2: dict[str, Any]) -> float:
    """
    Check correlation between two bets.
    
    Returns correlation score 0-1:
    - 1.0: Perfectly correlated (same player, same market)
    - 0.7: Same match, same team
    - 0.5: Same match, different teams
    - 0.3: Same team, different match
    - 0.0: Completely independent
    """
    # Same fixture
    same_fixture = bet1.get("fixture_id") == bet2.get("fixture_id")
    
    # Same team
    same_team = bet1.get("team") == bet2.get("team")
    
    # Same player (extremely correlated for goal + assist)
    same_player = bet1.get("player") == bet2.get("player")
    
    if same_player:
        return 0.9
    
    if same_fixture and same_team:
        return 0.7
    
    if same_fixture:
        return 0.5
    
    if same_team:
        return 0.3
    
    return 0.1


def calculate_kelly_stake(
    probability: float,
    odds: float,
    bankroll: float,
    fraction: float = 0.25,
    max_stake: float | None = None,
) -> float:
    """
    Calculate Kelly criterion stake.
    
    Kelly formula: f* = (bp - q) / b
    where:
        b = odds - 1 (net odds)
        p = probability of winning
        q = 1 - p (probability of losing)
    
    Args:
        probability: Our estimated probability
        odds: Decimal odds offered
        bankroll: Current bankroll
        fraction: Kelly fraction (0.25 = quarter Kelly)
        max_stake: Maximum stake cap
    
    Returns:
        Recommended stake (0 if no edge)
    """
    if odds <= 1 or probability <= 0 or probability >= 1:
        return 0.0
    
    b = odds - 1
    p = probability
    q = 1 - p
    
    # Kelly fraction
    kelly = (b * p - q) / b
    
    # No bet if negative edge
    if kelly <= 0:
        return 0.0
    
    # Apply fraction and calculate stake
    stake = kelly * fraction * bankroll
    
    # Apply max cap
    if max_stake is not None:
        stake = min(stake, max_stake)
    
    return round(stake, 2)


def apply_exposure_limits(
    bets: list[dict[str, Any]],
    max_per_match: float = 100.0,
    max_per_day: float = 500.0,
    max_per_team: float = 200.0,
) -> list[dict[str, Any]]:
    """
    Apply exposure limits to bets.
    
    Reduces stakes to comply with:
    - Maximum exposure per match
    - Maximum exposure per day
    - Maximum exposure per team
    
    Returns bets with adjusted stakes.
    """
    if not bets:
        return []
    
    result = []
    
    # Track exposure
    match_exposure: dict[str, float] = {}
    team_exposure: dict[str, float] = {}
    total_exposure = 0.0
    
    # Sort by value (edge * confidence) to prioritize best bets
    sorted_bets = sorted(
        bets,
        key=lambda b: b.get("edge", 0) * b.get("confidence", 1),
        reverse=True,
    )
    
    for bet in sorted_bets:
        fixture_id = bet.get("fixture_id", "unknown")
        team = bet.get("team", "unknown")
        stake = bet.get("stake", 0)
        
        # Check match limit
        current_match = match_exposure.get(fixture_id, 0)
        if current_match + stake > max_per_match:
            stake = max(0, max_per_match - current_match)
        
        # Check team limit
        current_team = team_exposure.get(team, 0)
        if current_team + stake > max_per_team:
            stake = max(0, max_per_team - current_team)
        
        # Check daily limit
        if total_exposure + stake > max_per_day:
            stake = max(0, max_per_day - total_exposure)
        
        if stake <= 0:
            continue
        
        # Update exposure tracking
        match_exposure[fixture_id] = match_exposure.get(fixture_id, 0) + stake
        team_exposure[team] = team_exposure.get(team, 0) + stake
        total_exposure += stake
        
        # Add bet with adjusted stake
        adjusted_bet = bet.copy()
        adjusted_bet["stake"] = stake
        result.append(adjusted_bet)
    
    return result


def select_bets(
    recommendations: list[dict[str, Any]],
    filter_config: RecommendationFilter | None = None,
    bankroll: float = 1000.0,
    kelly_fraction: float = 0.25,
    max_per_match: float = 100.0,
    max_per_day: float = 500.0,
) -> SelectionResult:
    """
    Complete selection pipeline.
    
    1. Filter by criteria
    2. Rank by value
    3. Calculate stakes
    4. Apply exposure limits
    """
    config = filter_config or RecommendationFilter()
    
    # Step 1: Filter
    filtered = filter_recommendations(recommendations, config)
    filtered_out = len(recommendations) - len(filtered)
    
    # Step 2: Rank
    ranked = rank_by_value(filtered, method="composite")
    
    # Step 3: Calculate stakes
    for rec in ranked:
        stake = calculate_kelly_stake(
            probability=1 / rec.get("fair_odds", 2),
            odds=rec.get("market_odds", 2),
            bankroll=bankroll,
            fraction=kelly_fraction,
        )
        rec["stake"] = stake
    
    # Filter out zero stakes
    ranked = [r for r in ranked if r.get("stake", 0) > 0]
    
    # Step 4: Apply limits
    selected = apply_exposure_limits(
        ranked,
        max_per_match=max_per_match,
        max_per_day=max_per_day,
    )
    
    total_stake = sum(b.get("stake", 0) for b in selected)
    
    return SelectionResult(
        selected=selected,
        filtered_out=filtered_out,
        total_stake=total_stake,
    )
