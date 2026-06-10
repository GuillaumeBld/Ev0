"""WC2026 tournament-level player pricing."""
from __future__ import annotations

import logging
import math
import unicodedata
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

N_MONTE_CARLO = 50_000
_MC_SEED = 42

# WC2026 has 8 group-stage + knockout matches for a champion; ~7 avg for a deep run.
# We estimate expected matches per team as a tournament-wide average.
# A standard model: group stage = 3 matches guaranteed per team.
# To produce lambda_goals over the whole tournament we scale per-match xG.
# The BM xG in TEAM_BM is total tournament expected goals (pre-tournament estimate).
# We convert to per-match: TEAM_BM[nation] / WC_AVG_MATCHES_PER_TEAM
WC_AVG_MATCHES_PER_TEAM = 4.0  # realistic expectation across all 48 teams


def _norm_name(name: str) -> str:
    n = unicodedata.normalize("NFKD", name.lower().strip())
    return "".join(c for c in n if not unicodedata.combining(c))


def poisson_ge(lam: float, k: int) -> float:
    """P(X >= k) where X ~ Poisson(lam). Pure math, no scipy."""
    if lam <= 0:
        return 0.0
    cdf = sum(math.exp(-lam) * (lam ** j) / math.factorial(j) for j in range(k))
    return max(0.0, min(1.0, 1.0 - cdf))


def _fair_odds(p: float) -> float | None:
    return round(1.0 / p, 2) if p > 0.001 else None


def run_monte_carlo(
    lambdas_goals: list[float],
    lambdas_assists: list[float],
    n_sim: int = N_MONTE_CARLO,
    seed: int = _MC_SEED,
) -> list[dict[str, float]]:
    """Simulate n_sim tournaments; return p_top_scorer + p_top_assister per player."""
    rng = np.random.default_rng(seed)
    n = len(lambdas_goals)
    lg = np.array(lambdas_goals, dtype=float)
    la = np.array(lambdas_assists, dtype=float)
    goals_sim   = rng.poisson(lg[:, None], size=(n, n_sim))
    assists_sim = rng.poisson(la[:, None], size=(n, n_sim))
    top_scorer_idx   = goals_sim.argmax(axis=0)
    top_assister_idx = assists_sim.argmax(axis=0)
    results = []
    for i in range(n):
        results.append({
            "p_top_scorer":   float((top_scorer_idx   == i).sum()) / n_sim,
            "p_top_assister": float((top_assister_idx == i).sum()) / n_sim,
        })
    return results


async def compute_tournament_pricing(db: AsyncSession) -> list[dict[str, Any]]:
    """Compute per-player tournament pricing for WC2026.

    For each nation with a default lineup:
      1. Resolve national_team_api_id from bzz_teams via nation name.
      2. Load Bzzoiro player stats (_load_national_team_players).
      3. Match lineup players to Bzzoiro players by NFKD-normalised name.
      4. Set avg_minutes_per_match from WC2026ExpectedLineupPlayer.expected_minutes.
      5. compute_player_shares + allocate_player to get per-match lambdas.
      6. Scale lambdas by expected matches to get tournament totals.

    Monte Carlo runs ONCE across ALL players after all nations are processed.
    Returns list[dict] ready for bulk insert into wc2026_player_pricing.
    """
    from app.ingestion.wc2026.team_bm import TEAM_BM, WC2026_NATION_NAME_ALIASES
    from app.models.bzzoiro import BzzPlayer, BzzTeam
    from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer
    from app.pricing.assist import ASSIST_GOAL_RATE
    from app.pricing.team_xg import (
        _load_national_team_players,
        allocate_player,
        compute_player_shares,
        detect_penalty_taker,
    )

    # ── Phase 1: Collect per-player tournament lambdas ────────────────
    all_entries: list[dict[str, Any]] = []

    for nation, team_bm_xg in TEAM_BM.items():
        # Step 1: Load default lineup
        lineup_result = await db.execute(
            select(WC2026ExpectedLineup)
            .where(
                WC2026ExpectedLineup.nation == nation,
                WC2026ExpectedLineup.context == "default",
            )
        )
        lineup = lineup_result.scalar_one_or_none()
        if lineup is None:
            logger.warning("wc2026_pricing: no default lineup for %s — skipped", nation)
            continue

        # Step 2: Resolve national_team_api_id via bzz_teams
        bzz_team_name = WC2026_NATION_NAME_ALIASES.get(nation, nation)
        team_result = await db.execute(
            select(BzzTeam).where(BzzTeam.name == bzz_team_name)
        )
        bzz_team = team_result.scalar_one_or_none()
        if bzz_team is None:
            logger.warning(
                "wc2026_pricing: bzz_team not found for %s (searched '%s') — skipped",
                nation, bzz_team_name,
            )
            continue

        # Step 3: Load Bzzoiro player stats for this national team
        bzz_players = await _load_national_team_players(db, bzz_team.api_id)
        if not bzz_players:
            logger.warning(
                "wc2026_pricing: no bzz players found for %s (api_id=%s) — skipped",
                nation, bzz_team.api_id,
            )
            continue

        # Step 4: Load lineup players and match to Bzzoiro players by normalised name
        lineup_players_result = await db.execute(
            select(WC2026ExpectedLineupPlayer).where(
                WC2026ExpectedLineupPlayer.lineup_id == lineup.id
            )
        )
        lineup_players = lineup_players_result.scalars().all()

        # Build name-to-expected_minutes map from lineup
        lineup_by_norm_name: dict[str, int] = {
            _norm_name(lp.player_name): lp.expected_minutes
            for lp in lineup_players
        }

        # Match bzz players to lineup by normalised name; override avg_minutes_per_match
        matched_players: list[dict[str, Any]] = []
        for p in bzz_players:
            norm = _norm_name(p["player_name"])
            if norm in lineup_by_norm_name:
                p = dict(p)  # copy to avoid mutating cached dict
                p["avg_minutes_per_match"] = float(lineup_by_norm_name[norm])
                matched_players.append(p)

        if len(matched_players) < 5:
            logger.warning(
                "wc2026_pricing: only %d matched players for %s (need >=5) — skipped",
                len(matched_players), nation,
            )
            continue

        # Step 5: Per-match xG for this nation
        per_match_xg = team_bm_xg / WC_AVG_MATCHES_PER_TEAM

        # Step 6: compute_player_shares
        shares = compute_player_shares(matched_players, nation, per_match_xg)

        # Step 7: detect penalty taker
        pen_taker_id = detect_penalty_taker(matched_players)

        # Step 8: allocate_player for each share
        budget_assists = per_match_xg * ASSIST_GOAL_RATE
        for share in shares:
            is_pen_taker = (share.player_id == pen_taker_id)
            allocation = allocate_player(
                share,
                per_match_xg,
                is_pen_taker,
                budget_assists,
            )
            # Scale per-match lambdas to tournament totals
            lambda_goals_tournament   = allocation.lambda_total  * WC_AVG_MATCHES_PER_TEAM
            lambda_assists_tournament = allocation.lambda_assist * WC_AVG_MATCHES_PER_TEAM

            all_entries.append({
                "nation":           nation,
                "player_name":      share.player_name,
                "position":         share.position,
                "lambda_goals":     lambda_goals_tournament,
                "lambda_assists":   lambda_assists_tournament,
            })

    if not all_entries:
        logger.warning("wc2026_pricing: no entries collected — returning empty list")
        return []

    # ── Phase 2: Monte Carlo across ALL players ───────────────────────
    lambdas_g = [e["lambda_goals"]   for e in all_entries]
    lambdas_a = [e["lambda_assists"] for e in all_entries]
    mc_results = run_monte_carlo(lambdas_g, lambdas_a, n_sim=N_MONTE_CARLO, seed=_MC_SEED)

    # ── Phase 3: Compute Poisson cuts + assemble output rows ──────────
    now = datetime.now(timezone.utc)
    output: list[dict[str, Any]] = []
    for entry, mc in zip(all_entries, mc_results):
        lg = entry["lambda_goals"]
        la = entry["lambda_assists"]

        p_ts = mc["p_top_scorer"]
        p_ta = mc["p_top_assister"]

        output.append({
            "nation":            entry["nation"],
            "player_name":       entry["player_name"],
            "position":          entry["position"],
            "lambda_goals":      round(lg, 4),
            "lambda_assists":    round(la, 4),
            # Goal cuts
            "p_1g":              round(poisson_ge(lg, 1), 6),
            "p_2g":              round(poisson_ge(lg, 2), 6),
            "p_3g":              round(poisson_ge(lg, 3), 6),
            "p_4g":              round(poisson_ge(lg, 4), 6),
            "fair_1g":           _fair_odds(poisson_ge(lg, 1)),
            "fair_2g":           _fair_odds(poisson_ge(lg, 2)),
            "fair_3g":           _fair_odds(poisson_ge(lg, 3)),
            "fair_4g":           _fair_odds(poisson_ge(lg, 4)),
            # Assist cuts
            "p_1a":              round(poisson_ge(la, 1), 6),
            "p_2a":              round(poisson_ge(la, 2), 6),
            "p_3a":              round(poisson_ge(la, 3), 6),
            "fair_1a":           _fair_odds(poisson_ge(la, 1)),
            "fair_2a":           _fair_odds(poisson_ge(la, 2)),
            "fair_3a":           _fair_odds(poisson_ge(la, 3)),
            # Outrights
            "p_top_scorer":      round(p_ts, 6),
            "p_top_assister":    round(p_ta, 6),
            "fair_top_scorer":   _fair_odds(p_ts),
            "fair_top_assister": _fair_odds(p_ta),
            "computed_at":       now,
        })

    logger.info(
        "wc2026_pricing: computed pricing for %d players across %d nations",
        len(output),
        len({e["nation"] for e in output}),
    )
    return output
