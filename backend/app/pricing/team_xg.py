"""Team xG estimation — hybrid implied-odds / Dixon-Coles model.

For each player with bookmaker goalscorer odds we reverse-engineer what
the market believes the team's match xG will be:

    λ_i      = -ln(1 - 1/best_odds)          # Poisson λ for player i
    team_xg_i = λ_i / (npxg_share_i × mins_frac_i)

A weighted average (weighted by npxg_share) across qualifying players
gives the implied team xG.  Falls back to the sum-of-player Dixon-Coles
style estimate when fewer than 3 players have valid odds.
"""

import math
from dataclasses import dataclass

from app.ingestion.odds import normalize_selection_name


@dataclass
class PlayerShare:
    """A player's fractional share of team xG and expected minutes."""

    player_name: str
    npxg_share: float    # fraction of team xG this player contributes (0–1)
    expected_minutes: float  # expected minutes in the match


def _implied_team_xg_from_odds(
    player_shares: list[PlayerShare],
    player_best_odds: dict[str, float],  # normalized_name → best odds
    min_players: int = 3,
) -> float | None:
    """Reverse-engineer team match xG from bookmaker goalscorer odds.

    Returns None if fewer than *min_players* players have valid odds.
    Result is clamped to [0.40, 4.00].
    """
    estimates: list[float] = []
    weights: list[float] = []

    for share in player_shares:
        norm = normalize_selection_name(share.player_name)
        odds = player_best_odds.get(norm)
        if not odds or odds <= 1.05:
            continue

        p_fair = 1.0 / odds
        if not (0.001 < p_fair < 0.99):
            continue

        lam_market = -math.log(1.0 - p_fair)
        mins_frac = max(0.1, share.expected_minutes / 90.0)

        if share.npxg_share < 0.005:  # skip fringe players (<0.5 % of team xG)
            continue

        team_xg_i = lam_market / (share.npxg_share * mins_frac)
        estimates.append(team_xg_i)
        weights.append(share.npxg_share)

    if len(estimates) < min_players:
        return None

    total_w = sum(weights)
    implied = sum(e * w for e, w in zip(estimates, weights)) / total_w
    return max(0.40, min(4.00, implied))


def build_player_shares(
    team_players: list[tuple[str, dict]],
) -> list[PlayerShare]:
    """Build a PlayerShare list from (name, stats_dict) pairs.

    Each player's xG contribution = xg_per_90 × expected_minutes / 90.
    npxg_share = contribution / team_total_contribution.
    """
    contributions = [
        (name, stats.get("xg_per_90", 0.0) * stats.get("expected_minutes", 75.0) / 90.0)
        for name, stats in team_players
    ]
    total = sum(c for _, c in contributions)
    if total <= 0:
        return []

    return [
        PlayerShare(
            player_name=name,
            npxg_share=contribution / total,
            expected_minutes=stats.get("expected_minutes", 75.0),
        )
        for (name, stats), (_, contribution) in zip(team_players, contributions)
    ]


def compute_team_xg_scale(
    team_players: list[tuple[str, dict]],
    best_odds_by_norm: dict[str, float],
) -> tuple[float, str]:
    """Compute a team xG scale factor using implied odds when possible.

    Returns ``(scale_factor, source)`` where *source* is ``"implied"`` or
    ``"dixon"``.  A scale of 1.0 with source ``"dixon"`` means no market
    adjustment was applied.

    The scale is clamped to [0.40, 2.50] to avoid extreme adjustments.
    """
    if not team_players:
        return 1.0, "dixon"

    dixon_xg = sum(
        s.get("xg_per_90", 0.0) * s.get("expected_minutes", 75.0) / 90.0
        for _, s in team_players
    )
    if dixon_xg <= 0:
        return 1.0, "dixon"

    shares = build_player_shares(team_players)
    if not shares:
        return 1.0, "dixon"

    # Restrict odds lookup to players on this team
    team_norms = {normalize_selection_name(name) for name, _ in team_players}
    team_odds = {n: v for n, v in best_odds_by_norm.items() if n in team_norms}

    implied_xg = _implied_team_xg_from_odds(shares, team_odds)
    if implied_xg is None:
        return 1.0, "dixon"

    scale = max(0.40, min(2.50, implied_xg / dixon_xg))
    return scale, "implied"
