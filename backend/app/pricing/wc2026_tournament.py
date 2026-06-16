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

# Bayesian shrinkage: how many prior minutes of trust in scouting xG/90
# With 450 min WC data (5 matches), weight is 50/50 between prior and observed.
_W_PRIOR_MINUTES = 450.0

WC_LEAGUE_API_ID = 27

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
    # Fetch all individual odds records, then compute median after name normalization.
    # This avoids double-counting when bookmakers use different name variants for the same nation
    # (e.g. PMU uses "Spain" while Betclic/Unibet use "Espagne" → both map to canonical "Spain").
    rows = (await db.execute(text("""
        SELECT nation, market_type, bookmaker, odds
        FROM wc2026_outright_odds
        WHERE is_active = true
          AND nation IS NOT NULL
          AND nation NOT LIKE '%/%'
          AND market_type IN ('group_stage', 'top8', 'top4', 'winner')
    """))).mappings().all()

    # Collect one odds value per (canonical_nation, market, bookmaker) — take max if multiple
    raw: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        canon = _ODDS_TO_BM.get(row["nation"])
        if canon is None:
            continue
        key = (canon, row["market_type"])
        per_book = raw.setdefault(key, {})
        book = row["bookmaker"]
        if book not in per_book or row["odds"] > per_book[book]:
            per_book[book] = float(row["odds"])

    # Compute median per (canonical, market) across bookmakers
    best: dict[str, dict[str, float]] = {}
    for (canon, mkt), per_book in raw.items():
        vals = sorted(per_book.values())
        n = len(vals)
        median = (vals[n // 2] + vals[(n - 1) // 2]) / 2.0
        cur = best.setdefault(canon, {})
        cur[mkt] = median

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


async def load_wc_match_stats(db: AsyncSession) -> dict[str, dict]:
    """Aggregate actual WC tournament stats from bzz_player_match_stats.

    Returns {norm_player_name: {goals, assists, minutes, xg_per_90, xa_per_90}}.
    Uses DISTINCT ON to guard against duplicate rows from different sync runs.
    """
    rows = (await db.execute(text("""
        WITH deduped AS (
            SELECT DISTINCT ON (p.name, ps.event_api_id)
                p.name                              AS player_name,
                COALESCE(ps.minutes_played, 0)      AS minutes_played,
                COALESCE(ps.goals, 0)               AS goals,
                COALESCE(ps.goal_assist, 0)         AS assists,
                COALESCE(ps.expected_goals, 0.0)    AS xg,
                COALESCE(ps.expected_assists, 0.0)  AS xa
            FROM bzz_player_match_stats ps
            JOIN bzz_players p ON p.api_id = ps.player_api_id
            JOIN bzz_events  e ON e.api_id = ps.event_api_id
            WHERE e.league_api_id = :lid
              AND COALESCE(ps.minutes_played, 1) > 0
            ORDER BY p.name, ps.event_api_id, ps.player_api_id DESC
        )
        SELECT
            player_name,
            SUM(minutes_played) AS minutes,
            SUM(goals)          AS goals,
            SUM(assists)        AS assists,
            SUM(xg)             AS xg,
            SUM(xa)             AS xa
        FROM deduped
        GROUP BY player_name
    """), {"lid": WC_LEAGUE_API_ID})).mappings().all()

    result: dict[str, dict] = {}
    for row in rows:
        minutes = float(row["minutes"] or 0)
        xg = float(row["xg"] or 0)
        xa = float(row["xa"] or 0)
        result[_norm_name(row["player_name"])] = {
            "goals":     int(row["goals"] or 0),
            "assists":   int(row["assists"] or 0),
            "minutes":   int(minutes),
            "xg_per_90": round(xg / minutes * 90, 4) if minutes >= 45 else 0.0,
            "xa_per_90": round(xa / minutes * 90, 4) if minutes >= 45 else 0.0,
        }
    return result


def run_monte_carlo(
    lambdas_remaining_goals: list[float],
    lambdas_remaining_assists: list[float],
    wc_goals: list[int],
    wc_assists: list[int],
    n_sim: int = N_MONTE_CARLO,
    seed: int = _MC_SEED,
) -> list[dict[str, float]]:
    """Simulate total = already_scored + Poisson(lambda_remaining) for each player."""
    rng = np.random.default_rng(seed)
    n = len(lambdas_remaining_goals)
    lg = np.maximum(np.array(lambdas_remaining_goals, dtype=float), 0)
    la = np.maximum(np.array(lambdas_remaining_assists, dtype=float), 0)
    wg = np.array(wc_goals, dtype=int)
    wa = np.array(wc_assists, dtype=int)
    goals_sim   = wg[:, None] + rng.poisson(lg[:, None], size=(n, n_sim))
    assists_sim = wa[:, None] + rng.poisson(la[:, None], size=(n, n_sim))
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
        blended_xg_p90 = (W_PRIOR × prior_xg_p90 + wc_min × wc_xg_p90) / (W_PRIOR + wc_min)
        weight_g_i     = blended_xg_p90_i × (expected_minutes_i / 90)
        share_g_i      = weight_g_i / sum(weight_g)
        lambda_total   = share_g_i × adjusted_bm
        fraction_rem   = (expected_minutes - wc_minutes_played) / expected_minutes
        lambda_rem     = lambda_total × fraction_rem
        projected_total = wc_goals + Poisson(lambda_rem)  [used in MC]

    Stats source: wc2026_scouting_stats (prior) blended with bzz_player_match_stats (WC actual).
    Lineup source: wc2026_expected_lineups / wc2026_expected_lineup_players.
    Odds source: wc2026_outright_odds (group_stage, top8, top4, winner markets).
    """
    from app.ingestion.wc2026.team_bm import (
        TEAM_BM,
        WC2026_LINEUP_NATION_MAP,
        WC2026_SCOUTING_NATION_MAP,
    )
    from app.models.wc2026_lineups import WC2026ExpectedLineup, WC2026ExpectedLineupPlayer

    # 0. Load odds-based expected games + actual WC tournament stats
    e_games_map = await compute_expected_games(db)
    wc_stats = await load_wc_match_stats(db)
    logger.info("wc2026_pricing: loaded WC tournament stats for %d players", len(wc_stats))

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
            prior_xg_p90 = float(stats.get("xg_p90") or 0.0)
            prior_xa_p90 = float(stats.get("assists_p90") or 0.0)

            # Bayesian blend with actual WC tournament stats
            wc = wc_stats.get(norm) or {}
            wc_min = float(wc.get("minutes", 0))
            wc_xg_p90 = float(wc.get("xg_per_90", 0.0))
            wc_xa_p90 = float(wc.get("xa_per_90", 0.0))
            blend_denom = _W_PRIOR_MINUTES + wc_min
            blended_xg_p90 = (_W_PRIOR_MINUTES * prior_xg_p90 + wc_min * wc_xg_p90) / blend_denom
            blended_xa_p90 = (_W_PRIOR_MINUTES * prior_xa_p90 + wc_min * wc_xa_p90) / blend_denom

            candidate = {
                "player_name":     row["player_name"],
                "position":        stats.get("position"),
                "npxg_per_90":     blended_xg_p90,
                "xa_per_90":       blended_xa_p90,
                "prior_xg_p90":    prior_xg_p90,
                "prior_xa_p90":    prior_xa_p90,
                "wc_xg_per_90":    wc_xg_p90,
                "wc_xa_p90":       wc_xa_p90,
                "wc_minutes":      int(wc_min),
                "wc_goals":        int(wc.get("goals", 0)),
                "wc_assists":      int(wc.get("assists", 0)),
                "minutes":         mins,
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

        # 5. Compute weights (blended xg_p90 × expected_minutes) and normalise to shares
        for p in matched:
            p["w_g"] = p["npxg_per_90"] * (p["minutes"] / 90.0)
            p["w_a"] = p["xa_per_90"]   * (p["minutes"] / 90.0)

        total_g = sum(p["w_g"] for p in matched) or 1e-9
        total_a = sum(p["w_a"] for p in matched) or 1e-9
        budget_assists = adjusted_bm * _ASSIST_GOAL_RATE

        for p in matched:
            lambda_total_g = round((p["w_g"] / total_g) * adjusted_bm,   4)
            lambda_total_a = round((p["w_a"] / total_a) * budget_assists, 4)

            # Fraction of expected minutes still to be played
            total_exp_min = float(p["minutes"])
            played_min    = float(p["wc_minutes"])
            fraction_rem  = max(0.0, (total_exp_min - played_min) / total_exp_min) if total_exp_min > 0 else 1.0

            all_entries.append({
                "nation":                  nation,
                "player_name":             p["player_name"],
                "position":                p["position"],
                "expected_games":          e_games,
                "lambda_goals":            lambda_total_g,
                "lambda_assists":          lambda_total_a,
                "lambda_remaining_goals":  round(lambda_total_g * fraction_rem, 4),
                "lambda_remaining_assists":round(lambda_total_a * fraction_rem, 4),
                "prior_xg_p90":            round(p["prior_xg_p90"],  4),
                "blended_xg_p90":          round(p["npxg_per_90"],   4),
                "wc_xg_per_90":            round(p["wc_xg_per_90"],  4),
                "wc_minutes":              p["wc_minutes"],
                "wc_goals":                p["wc_goals"],
                "wc_assists":              p["wc_assists"],
            })

    if not all_entries:
        logger.warning("wc2026_pricing: no entries collected — returning empty list")
        return []

    # 6. Monte Carlo: total = wc_goals_already + Poisson(lambda_remaining)
    mc_results = run_monte_carlo(
        [e["lambda_remaining_goals"]   for e in all_entries],
        [e["lambda_remaining_assists"] for e in all_entries],
        [e["wc_goals"]                 for e in all_entries],
        [e["wc_assists"]               for e in all_entries],
        n_sim=N_MONTE_CARLO,
        seed=_MC_SEED,
    )

    # 7. Conditional Poisson cuts + assemble final rows
    # P(total ≥ k) = 1 if already scored ≥ k, else P(Poisson(λ_rem) ≥ k - already_scored)
    now = datetime.now(timezone.utc)
    output: list[dict[str, Any]] = []
    for entry, mc in zip(all_entries, mc_results):
        lg  = entry["lambda_remaining_goals"]
        la  = entry["lambda_remaining_assists"]
        g   = entry["wc_goals"]
        a   = entry["wc_assists"]
        p_ts = mc["p_top_scorer"]
        p_ta = mc["p_top_assister"]

        def _p_ge_g(k: int) -> float:
            return 1.0 if g >= k else poisson_ge(lg, k - g)

        def _p_ge_a(k: int) -> float:
            return 1.0 if a >= k else poisson_ge(la, k - a)

        output.append({
            "nation":                   entry["nation"],
            "player_name":              entry["player_name"],
            "position":                 entry["position"],
            "expected_games":           entry["expected_games"],
            "lambda_goals":             entry["lambda_goals"],
            "lambda_assists":           entry["lambda_assists"],
            "lambda_remaining_goals":   lg,
            "lambda_remaining_assists": la,
            "prior_xg_p90":             entry["prior_xg_p90"],
            "blended_xg_p90":           entry["blended_xg_p90"],
            "wc_xg_per_90":             entry["wc_xg_per_90"],
            "wc_minutes":               entry["wc_minutes"],
            "wc_goals":                 entry["wc_goals"],
            "wc_assists":               entry["wc_assists"],
            "p_1g":                     round(_p_ge_g(1), 6),
            "p_2g":                     round(_p_ge_g(2), 6),
            "p_3g":                     round(_p_ge_g(3), 6),
            "p_4g":                     round(_p_ge_g(4), 6),
            "fair_1g":                  _fair_odds(_p_ge_g(1)),
            "fair_2g":                  _fair_odds(_p_ge_g(2)),
            "fair_3g":                  _fair_odds(_p_ge_g(3)),
            "fair_4g":                  _fair_odds(_p_ge_g(4)),
            "p_1a":                     round(_p_ge_a(1), 6),
            "p_2a":                     round(_p_ge_a(2), 6),
            "p_3a":                     round(_p_ge_a(3), 6),
            "fair_1a":                  _fair_odds(_p_ge_a(1)),
            "fair_2a":                  _fair_odds(_p_ge_a(2)),
            "fair_3a":                  _fair_odds(_p_ge_a(3)),
            "p_top_scorer":             round(p_ts, 6),
            "p_top_assister":           round(p_ta, 6),
            "fair_top_scorer":          _fair_odds(p_ts),
            "fair_top_assister":        _fair_odds(p_ta),
            "computed_at":              now,
        })

    logger.info(
        "wc2026_pricing: computed %d players across %d nations",
        len(output), len({e["nation"] for e in output}),
    )
    return output
