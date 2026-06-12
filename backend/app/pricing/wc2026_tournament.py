"""WC2026 tournament-level player pricing."""
from __future__ import annotations

import logging
import math
import unicodedata
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

N_MONTE_CARLO = 50_000
_MC_SEED = 42
_ASSIST_GOAL_RATE = 0.65  # assists budget = BM × rate

# WC2026: 48 teams, 32 advance to R32, then R16→QF→SF→Final+3rd
# Uniform average = (48×3 + 32 + 16 + 8 + 4×2) / 48 ≈ 4.33 games
# We use 4.5 as the calibration baseline assumed in TEAM_BM
_E_GAMES_BASELINE = 4.5

# Maps every odds DB nation string → canonical TEAM_BM English key.
# Both French (Betclic/Unibet) and English (other books) variants included.
_ODDS_TO_BM: dict[str, str] = {
    # --- A ---
    "Afrique du Sud":      "South Africa",
    "South Africa":        "South Africa",
    "Algeria":             "Algeria",
    "Algérie":             "Algeria",
    "Allemagne":           "Germany",
    "Germany":             "Germany",
    "Angleterre":          "England",
    "England":             "England",
    "Arabie Saoudite":     "Saudi Arabia",
    "ArabieSaoudite":      "Saudi Arabia",
    "Saudi Arabia":        "Saudi Arabia",
    "Argentina":           "Argentina",
    "Argentine":           "Argentina",
    "Australia":           "Australia",
    "Australie":           "Australia",
    "Austria":             "Austria",
    "Autriche":            "Austria",
    # --- B ---
    "Belgique":            "Belgium",
    "Belgium":             "Belgium",
    "Bosnia & Herzegovina": "Bosnia-Herzegovina",
    "Bosnie Herzég.":      "Bosnia-Herzegovina",
    "Bosnie-Herzégovine":  "Bosnia-Herzegovina",
    "Brazil":              "Brazil",
    "Brésil":              "Brazil",
    # --- C ---
    "Canada":              "Canada",
    "Cap Vert":            "Cape Verde Islands",
    "Cap-Vert":            "Cape Verde Islands",
    "Cape Verde":          "Cape Verde Islands",
    "Colombia":            "Colombia",
    "Colombie":            "Colombia",
    "Corée du Sud":        "South Korea",
    "South Korea":         "South Korea",
    "Croatia":             "Croatia",
    "Croatie":             "Croatia",
    "Curacao":             "Curaçao",
    "Curaçao":             "Curaçao",
    "Czech Republic":      "Czechia",
    "Rép.Tchèque":         "Czechia",
    "Tchéquie":            "Czechia",
    "Côte d'Ivoire":       "Ivory Coast",
    "Ivory Coast":         "Ivory Coast",
    # --- D ---
    "DR Congo":            "Congo DR",
    "RD Congo":            "Congo DR",
    # --- E ---
    "Ecosse":              "Scotland",
    "Écosse":              "Scotland",
    "Scotland":            "Scotland",
    "Ecuador":             "Ecuador",
    "Equateur":            "Ecuador",
    "Équateur":            "Ecuador",
    "Egypt":               "Egypt",
    "Egypte":              "Egypt",
    "Égypte":              "Egypt",
    "Espagne":             "Spain",
    "Spain":               "Spain",
    "Etats-Unis":          "United States",
    "États-Unis":          "United States",
    "USA":                 "United States",
    # --- F ---
    "France":              "France",
    # --- G ---
    "Ghana":               "Ghana",
    # --- H ---
    "Haiti":               "Haiti",
    "Haïti":               "Haiti",
    # --- I ---
    "Irak":                "Iraq",
    "Iraq":                "Iraq",
    "Iran":                "Iran",
    # --- J ---
    "Japan":               "Japan",
    "Japon":               "Japan",
    "Jordan":              "Jordan",
    "Jordanie":            "Jordan",
    # --- M ---
    "Maroc":               "Morocco",
    "Morocco":             "Morocco",
    "Mexico":              "Mexico",
    "Mexique":             "Mexico",
    # --- N ---
    "Netherlands":         "Netherlands",
    "Pays-Bas":            "Netherlands",
    "New Zealand":         "New Zealand",
    "Nlle Zélande":        "New Zealand",
    "Nouvelle-Zélande":    "New Zealand",
    "Norway":              "Norway",
    "Norvège":             "Norway",
    # --- P ---
    "Panama":              "Panama",
    "Paraguay":            "Paraguay",
    "Portugal":            "Portugal",
    # --- Q ---
    "Qatar":               "Qatar",
    # --- S ---
    "Senegal":             "Senegal",
    "Sénégal":             "Senegal",
    "Sweden":              "Sweden",
    "Suède":               "Sweden",
    "Switzerland":         "Switzerland",
    "Suisse":              "Switzerland",
    # --- T ---
    "Tunisia":             "Tunisia",
    "Tunisie":             "Tunisia",
    "Turkey":              "Turkey",
    "Turquie":             "Turkey",
    # --- U ---
    "Uruguay":             "Uruguay",
    "Uzbekistan":          "Uzbekistan",
    "Ouzbékistan":         "Uzbekistan",
}


async def compute_expected_games(db: AsyncSession) -> dict[str, float]:
    """Compute expected number of matches per nation from bookmaker odds.

    WC2026 format: 48 teams, 32 advance to knockout (R32→R16→QF→SF→Final+3rd).
    Max games = 8 (3 group + 5 knockout). All top-4 teams play game 8.

    E[games] = 3
        + p(pass group/reach R32)
        + p(reach R16) [interpolated]
        + p(top8 = reach QF)
        + 2 × p(top4 = reach SF)   # SF game + 3rd-or-Final game (guaranteed for top4)
        + p(finalist)               # Final game (only the 2 finalists)
    """
    # Use median to exclude bookmaker-specific outliers (e.g. Unibet winner, PMU group_stage).
    rows = (await db.execute(text("""
        SELECT nation, market_type,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY odds) AS median_odds
        FROM wc2026_outright_odds
        WHERE is_active = true
          AND nation IS NOT NULL
          AND nation NOT LIKE '%/%'
          AND market_type IN ('group_stage', 'top8', 'top4', 'winner')
        GROUP BY nation, market_type
        HAVING COUNT(*) >= 1
    """))).mappings().all()

    # Aggregate per canonical TEAM_BM name — keep best (lowest implied prob) median per market
    best: dict[str, dict[str, float]] = {}
    for row in rows:
        canon = _ODDS_TO_BM.get(row["nation"])
        if canon is None:
            continue
        mkt = row["market_type"]
        cur = best.setdefault(canon, {})
        # Keep highest median odds (= lowest implied probability = most conservative estimate)
        if mkt not in cur or row["median_odds"] > cur[mkt]:
            cur[mkt] = float(row["median_odds"])

    result: dict[str, float] = {}
    for nation, mkt in best.items():
        p_group   = (1.0 / mkt["group_stage"]) if "group_stage" in mkt else 0.65
        p_top8    = (1.0 / mkt["top8"])         if "top8"        in mkt else p_group * 0.25
        p_top4    = (1.0 / mkt["top4"])         if "top4"        in mkt else p_top8  * 0.5
        p_win     = (1.0 / mkt["winner"])        if "winner"      in mkt else p_top4  * 0.25

        # Interpolate P(reach R16) — geometric mean between passing group and top8
        p_r16 = math.sqrt(p_group * p_top8)
        # P(finalist) ≈ 2 × P(win), capped at P(top4) since finalists ⊆ top4
        p_finalist = min(2.0 * p_win, p_top4)

        e_games = 3.0 + p_group + p_r16 + p_top8 + 2.0 * p_top4 + p_finalist
        result[nation] = round(e_games, 4)

    if result:
        logger.info(
            "wc2026 E[games] sample — Spain: %.2f, France: %.2f, Iran: %.2f",
            result.get("Spain", 0), result.get("France", 0), result.get("Iran", 0),
        )
    return result


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

    Formula:
        weight_g_i    = xg_p90_i × (expected_minutes_i / 90)
        share_g_i     = weight_g_i / sum(weight_g)
        adjusted_bm   = BM × (E_games_from_odds / _E_GAMES_BASELINE)
        lambda_goals  = share_g_i × adjusted_bm

    Stats source: wc2026_scouting_stats (all 48 nations).
    Lineup source: wc2026_expected_lineups / wc2026_expected_lineup_players.
    Odds source: wc2026_outright_odds (group_stage, top8, top4, winner markets).
    """
    from app.ingestion.wc2026.team_bm import (
        TEAM_BM,
        WC2026_LINEUP_NATION_MAP,
        WC2026_SCOUTING_NATION_MAP,
    )
    from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer

    # 0. Load odds-based expected games per nation
    e_games_map = await compute_expected_games(db)

    all_entries: list[dict[str, Any]] = []

    for nation, bm in TEAM_BM.items():
        # Adjust BM by odds-derived expected games vs calibration baseline.
        # BM was calibrated assuming _E_GAMES_BASELINE games on average.
        # Dividing by baseline gives per-match quality; multiplying by
        # odds-derived E[games] gives the true tournament lambda budget.
        e_games = e_games_map.get(nation, _E_GAMES_BASELINE)
        adjusted_bm = bm * (e_games / _E_GAMES_BASELINE)

        # 1. Load default lineup (DB stores nation names in French)
        lineup_nation = WC2026_LINEUP_NATION_MAP.get(nation, nation)
        lineup_result = await db.execute(
            select(WC2026ExpectedLineup).where(
                WC2026ExpectedLineup.nation == lineup_nation,
                WC2026ExpectedLineup.context == "default",
            )
        )
        lineup = lineup_result.scalar_one_or_none()
        if lineup is None:
            logger.warning("wc2026_pricing: no default lineup for %s — skipped", nation)
            continue

        # 2. Load expected minutes from lineup — single source of truth
        lp_result = await db.execute(
            select(WC2026ExpectedLineupPlayer).where(
                WC2026ExpectedLineupPlayer.lineup_id == lineup.id
            )
        )
        lineup_minutes: dict[str, float] = {
            _norm_name(lp.player_name): float(lp.expected_minutes)
            for lp in lp_result.scalars().all()
        }

        # 3. Load scouting stats for this nation
        scouting_nation = WC2026_SCOUTING_NATION_MAP.get(nation, nation)
        rows = (await db.execute(
            text(
                "SELECT player_name, normalized_name, stats "
                "FROM wc2026_scouting_stats WHERE nation = :nation"
            ),
            {"nation": scouting_nation},
        )).mappings().all()

        if not rows:
            logger.warning(
                "wc2026_pricing: no scouting stats for %s (searched '%s') — skipped",
                nation, scouting_nation,
            )
            continue

        # 4. Match scouting rows to lineup by normalised name, deduplicate (keep highest xg_p90).
        # Fallback: reversed 2-token name for Korean-style naming conventions.
        best_per_name: dict[str, dict[str, Any]] = {}
        for row in rows:
            norm = _norm_name(row["player_name"])
            mins = lineup_minutes.get(norm)
            if mins is None:
                parts = norm.split()
                if len(parts) == 2:
                    mins = lineup_minutes.get(f"{parts[1]} {parts[0]}")
            if mins is None:
                continue
            stats = row["stats"] or {}
            candidate = {
                "player_name": row["player_name"],
                "position":    stats.get("position"),
                "npxg_per_90": float(stats.get("xg_p90") or 0.0),
                "xa_per_90":   float(stats.get("assists_p90") or 0.0),
                "minutes":     mins,
            }
            existing = best_per_name.get(norm)
            if existing is None or candidate["npxg_per_90"] > existing["npxg_per_90"]:
                best_per_name[norm] = candidate
        matched = list(best_per_name.values())

        if len(matched) < 3:
            logger.warning(
                "wc2026_pricing: only %d matched players for %s — skipped",
                len(matched), nation,
            )
            continue

        # 5. Compute weights and normalise to relative shares
        for p in matched:
            p["w_g"] = p["npxg_per_90"] * (p["minutes"] / 90.0)
            p["w_a"] = p["xa_per_90"]   * (p["minutes"] / 90.0)

        total_g = sum(p["w_g"] for p in matched) or 1e-9
        total_a = sum(p["w_a"] for p in matched) or 1e-9
        budget_assists = adjusted_bm * _ASSIST_GOAL_RATE

        for p in matched:
            all_entries.append({
                "nation":          nation,
                "player_name":     p["player_name"],
                "position":        p["position"],
                "expected_games":  e_games,
                "lambda_goals":    round((p["w_g"] / total_g) * adjusted_bm,            4),
                "lambda_assists":  round((p["w_a"] / total_a) * budget_assists, 4),
            })

    if not all_entries:
        logger.warning("wc2026_pricing: no entries collected — returning empty list")
        return []

    # 6. Monte Carlo across ALL players simultaneously
    mc_results = run_monte_carlo(
        [e["lambda_goals"]   for e in all_entries],
        [e["lambda_assists"] for e in all_entries],
        n_sim=N_MONTE_CARLO,
        seed=_MC_SEED,
    )

    # 7. Poisson cuts + assemble final rows
    now = datetime.now(timezone.utc)
    output: list[dict[str, Any]] = []
    for entry, mc in zip(all_entries, mc_results):
        lg = entry["lambda_goals"]
        la = entry["lambda_assists"]
        p_ts = mc["p_top_scorer"]
        p_ta = mc["p_top_assister"]
        output.append({
            "nation":           entry["nation"],
            "player_name":      entry["player_name"],
            "position":         entry["position"],
            "expected_games":   entry["expected_games"],
            "lambda_goals":     lg,
            "lambda_assists":   la,
            "p_1g":             round(poisson_ge(lg, 1), 6),
            "p_2g":             round(poisson_ge(lg, 2), 6),
            "p_3g":             round(poisson_ge(lg, 3), 6),
            "p_4g":             round(poisson_ge(lg, 4), 6),
            "fair_1g":          _fair_odds(poisson_ge(lg, 1)),
            "fair_2g":          _fair_odds(poisson_ge(lg, 2)),
            "fair_3g":          _fair_odds(poisson_ge(lg, 3)),
            "fair_4g":          _fair_odds(poisson_ge(lg, 4)),
            "p_1a":             round(poisson_ge(la, 1), 6),
            "p_2a":             round(poisson_ge(la, 2), 6),
            "p_3a":             round(poisson_ge(la, 3), 6),
            "fair_1a":          _fair_odds(poisson_ge(la, 1)),
            "fair_2a":          _fair_odds(poisson_ge(la, 2)),
            "fair_3a":          _fair_odds(poisson_ge(la, 3)),
            "p_top_scorer":     round(p_ts, 6),
            "p_top_assister":   round(p_ta, 6),
            "fair_top_scorer":  _fair_odds(p_ts),
            "fair_top_assister": _fair_odds(p_ta),
            "computed_at":      now,
        })

    logger.info(
        "wc2026_pricing: computed %d players across %d nations",
        len(output), len({e["nation"] for e in output}),
    )
    return output
