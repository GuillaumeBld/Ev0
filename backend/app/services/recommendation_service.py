"""Recommendation generation service.

Combines pricing engine, odds data, and strategy to generate
actionable betting recommendations.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.odds import OddsAPIClient, SPORT_KEYS, MARKET_KEYS
from app.models.players import Player, PlayerStats
from app.pricing.goalscorer import calculate_goalscorer_price, calculate_edge
from app.pricing.assist import calculate_assist_price
from app.strategy.selector import select_bets, RecommendationFilter

logger = logging.getLogger(__name__)


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
    db: AsyncSession,
    filter_config: RecommendationFilter | None = None,
    debug: bool = False,
) -> list[dict[str, Any]] | tuple[list[dict[str, Any]], dict]:
    """
    Get recommendations for a specific date.

    1. Fetches fixtures from Odds API for each league
    2. Fetches player props for each event
    3. Loads player stats from DB
    4. Generates recommendations via the pricing pipeline

    If debug=True, returns (recs, debug_info) tuple.
    """
    debug_info: dict[str, Any] = {}

    try:
        odds_client = OddsAPIClient()
    except ValueError:
        logger.warning("ODDS_API_KEY not configured – returning empty recommendations")
        if debug:
            return [], {"error": "ODDS_API_KEY not configured"}
        return []

    fixtures: list[dict[str, Any]] = []
    odds_data: dict[str, list[dict[str, Any]]] = {}

    # 1. Fetch events + player props for each league
    for league, sport_key in SPORT_KEYS.items():
        try:
            events = await odds_client.get_events(sport_key)
        except Exception as exc:
            logger.error("Failed to fetch events for %s: %s", league, exc)
            continue

        for event in events:
            event_id = event.get("id")
            if not event_id:
                continue

            # Filter to target date (commence_time is ISO-8601)
            commence = event.get("commence_time", "")
            if commence:
                try:
                    event_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                    if event_dt.date() != target_date.date():
                        continue
                except (ValueError, TypeError):
                    pass

            fixtures.append({
                "fixture_id": event_id,
                "home_team": event.get("home_team", ""),
                "away_team": event.get("away_team", ""),
                "kickoff_utc": commence,
                "league": league,
            })

            # Fetch player props for goalscorer (primary market)
            fixture_odds: list[dict[str, Any]] = []
            for market_type, market_key in MARKET_KEYS.items():
                try:
                    props = await odds_client.get_player_props(
                        sport_key, event_id, market_key
                    )
                    for p in props:
                        fixture_odds.append({
                            "player_name": p["player_name"],
                            "market_type": market_type,
                            "odds": p["odds"],
                            "bookmaker": p["bookmaker"],
                        })
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch %s props for event %s: %s",
                        market_type, event_id, exc,
                    )
            odds_data[event_id] = fixture_odds

    if not fixtures:
        if debug:
            return [], {"fixtures_count": 0, "odds_data_keys": list(odds_data.keys()), "stage": "no_fixtures_for_date"}
        return []

    # 2. Load player stats from DB (latest snapshot per player)
    latest_subq = (
        select(
            PlayerStats.player_id,
            func.max(PlayerStats.as_of_utc).label("max_date"),
        )
        .group_by(PlayerStats.player_id)
        .subquery()
    )

    result = await db.execute(
        select(PlayerStats, Player.name, Player.team)
        .join(Player, Player.id == PlayerStats.player_id)
        .join(
            latest_subq,
            (PlayerStats.player_id == latest_subq.c.player_id)
            & (PlayerStats.as_of_utc == latest_subq.c.max_date),
        )
    )

    player_stats: dict[str, dict[str, Any]] = {}
    for row in result.all():
        stats: PlayerStats = row[0]
        player_name: str = row[1]
        team: str | None = row[2]

        minutes = stats.minutes_played or 0
        matches = stats.matches_played or 1
        expected_minutes = minutes / matches if matches > 0 else 75.0
        conversion_rate = (stats.goals / stats.xg) if stats.xg and stats.xg > 0 else 1.0

        player_stats[player_name] = {
            "xg_per_90": stats.xg_per_90 or 0.3,
            "xa_per_90": stats.xa_per_90 or 0.15,
            "expected_minutes": expected_minutes,
            "conversion_rate": conversion_rate,
            "team": team,
            "goals": stats.goals,
            "assists": stats.assists,
            "matches_played": stats.matches_played,
        }

    # 3. Generate recommendations
    recs = await generate_recommendations(fixtures, player_stats, odds_data, filter_config)

    # 4. Add unique IDs
    for rec in recs:
        rec["id"] = str(uuid.uuid4())

    if debug:
        # Count total odds entries across all fixtures
        total_odds = sum(len(v) for v in odds_data.values())
        debug_info = {
            "fixtures_count": len(fixtures),
            "player_stats_count": len(player_stats),
            "total_odds_entries": total_odds,
            "recommendations_before_filter": len(recs),
            "sample_fixture": fixtures[0] if fixtures else None,
            "sample_players": list(player_stats.keys())[:10],
            "odds_fixture_ids": list(odds_data.keys())[:5],
            "sample_odds": odds_data.get(fixtures[0]["fixture_id"], [])[:3] if fixtures else [],
        }
        return recs, debug_info

    return recs
