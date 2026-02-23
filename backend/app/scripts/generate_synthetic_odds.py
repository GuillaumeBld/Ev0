"""Synthetic odds generator for backtesting.

Generates artificial bookmaker odds from the Poisson pricing model by applying
realistic margin (overround) and per-player noise. This creates both positive-
and negative-edge scenarios, allowing the backtest pipeline to validate
calibration and edge detection.

The synthetic odds are **deterministic** — uses ``random.Random(42)`` so the
same run always produces identical output.
"""

import random
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session
from app.models.fixtures import Fixture
from app.models.odds import OddsSnapshot
from app.models.players import Player, PlayerStats
from app.pricing.assist import calculate_assist_price
from app.pricing.goalscorer import calculate_goalscorer_price
from app.services.recommendation_service import (
    POSITION_DEFAULTS,
    _normalize_position,
)


def _apply_margin_and_noise(fair_odds: float, overround: float, noise: float) -> float:
    """Apply bookmaker margin and random noise to fair odds.

    Args:
        fair_odds: Model-derived fair decimal odds (e.g. 3.50)
        overround: Per-fixture margin (e.g. 0.08 = 8% overround)
        noise: Per-player noise (e.g. 0.05 or -0.05)

    Returns:
        Synthetic bookmaker odds, clamped to [1.10, 51.0].

    How it works:
        ``synthetic = fair_odds / ((1 + overround) * (1 + noise))``

        - Positive overround → shorter odds (bookmaker takes margin)
        - Positive noise → even shorter odds (no edge for bettor)
        - Negative noise → longer odds (creates edge opportunity)
    """
    divisor = (1 + overround) * (1 + noise)
    if divisor <= 0:
        divisor = 0.01
    synthetic = fair_odds / divisor
    return max(1.10, min(51.0, round(synthetic, 2)))


def _compute_fair_odds(
    player_info: dict[str, Any],
    market_type: str,
) -> float | None:
    """Compute fair odds for a player in a given market using the pricing engine.

    Returns None if pricing fails or produces degenerate output.
    """
    position = player_info.get("position")
    if position == "GK":
        return None

    xg_per_90 = player_info.get("xg_per_90", 0.0)
    xa_per_90 = player_info.get("xa_per_90", 0.0)
    minutes = player_info.get("minutes", 0)
    matches = player_info.get("matches", 1)
    expected_minutes = minutes / matches if matches > 0 else 75.0

    if market_type == "goalscorer":
        if xg_per_90 <= 0:
            return None
        result = calculate_goalscorer_price(
            xg_per_90=xg_per_90,
            expected_minutes=expected_minutes,
        )
    elif market_type == "assist":
        if xa_per_90 <= 0:
            return None
        result = calculate_assist_price(
            xa_per_90=xa_per_90,
            expected_minutes=expected_minutes,
        )
    else:
        return None

    fair_odds = result["fair_odds"]
    # Skip degenerate odds
    if fair_odds <= 1.0 or fair_odds > 100.0:
        return None

    return fair_odds


async def _load_player_stats_by_team(
    session: AsyncSession,
    league: str,
    season: str,
) -> dict[str, list[dict[str, Any]]]:
    """Load player stats grouped by team for a league/season.

    Returns dict of team_name → list of player info dicts.
    Only includes players with ``minutes >= 90``.
    """
    result = await session.execute(
        select(PlayerStats, Player.name, Player.team, Player.position)
        .join(Player, Player.id == PlayerStats.player_id)
        .where(PlayerStats.league == league)
        .where(PlayerStats.season == season)
        .where(PlayerStats.minutes_played >= 90)
    )

    by_team: dict[str, list[dict[str, Any]]] = {}
    for row in result.all():
        ps: PlayerStats = row[0]
        name: str = row[1]
        team: str | None = row[2]
        raw_pos: str | None = row[3]

        if not team or not name:
            continue

        position = _normalize_position(raw_pos)
        if position == "GK":
            continue

        # Use position defaults if per-90 values are missing
        pos_defaults = POSITION_DEFAULTS.get(position or "", {"xg_per_90": 0.10, "xa_per_90": 0.08})

        info = {
            "name": name,
            "team": team,
            "position": position,
            "minutes": ps.minutes_played or 0,
            "matches": ps.matches_played or 1,
            "xg_per_90": ps.xg_per_90 if ps.xg_per_90 is not None else pos_defaults["xg_per_90"],
            "xa_per_90": ps.xa_per_90 if ps.xa_per_90 is not None else pos_defaults["xa_per_90"],
        }
        by_team.setdefault(team, []).append(info)

    return by_team


async def generate_synthetic_odds_for_season(league: str, season: str) -> int:
    """Generate synthetic bookmaker odds for all finished fixtures in a season.

    For each finished fixture that doesn't already have ``bookmaker="synthetic"`` odds:
    1. Find players from both teams with stats (minutes >= 90, non-GK)
    2. For each player × market (goalscorer, assist):
       - Compute fair odds from the Poisson model
       - Apply per-fixture overround: Uniform(0.05, 0.12)
       - Apply per-player noise: Gauss(0, 0.08)
       - Clamp to [1.10, 51.0]
    3. Store via ``store_odds_snapshot``

    Uses a fixed seed (42) for reproducibility.

    Returns:
        Number of odds records created.
    """
    rng = random.Random(42)  # noqa: S311 – deterministic, not for security
    total = 0

    async with async_session() as session:
        # Load player stats by team
        stats_by_team = await _load_player_stats_by_team(session, league, season)

        if not stats_by_team:
            print(f"[synthetic] No player stats found for {league} {season}")
            return 0

        print(f"[synthetic] {league} {season}: {sum(len(v) for v in stats_by_team.values())} players across {len(stats_by_team)} teams")

        # Find finished fixtures that don't already have synthetic odds
        has_synthetic = (
            select(OddsSnapshot.fixture_id)
            .where(OddsSnapshot.bookmaker == "synthetic")
            .distinct()
            .subquery()
        )

        stmt = (
            select(Fixture)
            .where(Fixture.league == league)
            .where(Fixture.season == season)
            .where(Fixture.status == "finished")
            .where(~Fixture.id.in_(select(has_synthetic.c.fixture_id)))
            .order_by(Fixture.kickoff_utc)
        )

        result = await session.execute(stmt)
        fixtures = list(result.scalars().all())

        if not fixtures:
            print(f"[synthetic] No fixtures need synthetic odds for {league} {season}")
            return 0

        print(f"[synthetic] Generating odds for {len(fixtures)} fixtures")

        for fixture in fixtures:
            # Per-fixture overround
            overround = rng.uniform(0.05, 0.12)

            # Collect players from both teams
            home_players = stats_by_team.get(fixture.home_team, [])
            away_players = stats_by_team.get(fixture.away_team, [])
            all_players = home_players + away_players

            if not all_players:
                continue

            for player_info in all_players:
                for market_type in ("goalscorer", "assist"):
                    fair_odds = _compute_fair_odds(player_info, market_type)
                    if fair_odds is None:
                        continue

                    # Per-player noise
                    noise = rng.gauss(0, 0.08)
                    synthetic_odds = _apply_margin_and_noise(fair_odds, overround, noise)

                    snapshot = OddsSnapshot(
                        fixture_id=fixture.id,
                        player_name=player_info["name"],
                        market_type=market_type,
                        bookmaker="synthetic",
                        odds=synthetic_odds,
                        implied_probability=1.0 / synthetic_odds if synthetic_odds > 0 else 0.0,
                        snapshot_utc=fixture.kickoff_utc,
                    )
                    session.add(snapshot)
                    total += 1

            # Commit per fixture to avoid huge transactions
            await session.commit()

    print(f"[synthetic] {league}: {total} synthetic odds created")
    return total
