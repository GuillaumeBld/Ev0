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
    # Priority 1: dynamic bracket simulation results
    adv_rows = (await db.execute(text(
        "SELECT nation, e_games FROM wc2026_team_advancement"
    ))).mappings().all()
    if adv_rows:
        logger.info(
            "compute_expected_games: using bracket simulation (%d nations)", len(adv_rows)
        )
        return {r["nation"]: float(r["e_games"]) for r in adv_rows}

    # Fallback: bookmaker outright odds (existing implementation below)
    logger.info("compute_expected_games: advancement table empty — falling back to bookmaker odds")
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


def _name_tokens(name: str) -> set[str]:
    return {
        t for t in _norm_name(name).replace("-", " ").replace("'", " ").split()
        if len(t) >= 3
    }


def _names_similar(a: str, b: str) -> bool:
    """True si deux noms désignent plausiblement le même joueur.

    Les deux univers d'IDs Bzzoiro stockent des variantes (Alex/Alejandro
    Baena, Møller/Möller Wolfe) mais certains internal_id pointent vers un
    autre joueur (ex. Rashford↔Ronaldo) : sans ce garde-fou, la
    réconciliation fusionnerait deux joueurs distincts.
    """
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return False
    if ta & tb:
        return True
    return any(x.startswith(y) or y.startswith(x) for x in ta for y in tb)


def poisson_ge(lam: float, k: int) -> float:
    """P(X >= k) where X ~ Poisson(lam). Pure math, no scipy."""
    if lam <= 0:
        return 0.0
    cdf = sum(math.exp(-lam) * (lam ** j) / math.factorial(j) for j in range(k))
    return max(0.0, min(1.0, 1.0 - cdf))


def _fair_odds(p: float) -> float | None:
    return round(1.0 / p, 2) if p > 0.001 else None


def _remaining_fraction(e_games: float, played: int, alive: bool) -> float:
    """Fraction du budget tournoi encore à jouer, au niveau ÉQUIPE.

    Une équipe éliminée ne projette plus rien (0.0), quels que soient les
    minutes ou le matching des stats de ses joueurs. Une équipe en lice a au
    moins 1 match à venir même si l'advancement (e_games) est périmé.
    """
    if not alive:
        return 0.0
    if e_games <= 0:
        return 0.0
    remaining = max(1.0, e_games - played)
    return min(1.0, remaining / e_games)


async def load_team_tournament_state(db: AsyncSession) -> dict[str, dict]:
    """Par nation canonique: matchs WC joués et statut en lice.

    alive = l'équipe apparaît dans au moins un match WC à venir (les
    placeholders W97/W98 n'ont pas de team_api_id, donc seules les équipes
    réellement qualifiées comptent).
    """
    rows = (await db.execute(text("""
        SELECT t.name AS team, e.status, count(*) AS n
        FROM bzz_events e
        JOIN bzz_teams t ON t.api_id IN (e.home_team_api_id, e.away_team_api_id)
        WHERE e.league_api_id = :lid AND e.status IN ('finished', 'notstarted')
        GROUP BY t.name, e.status
    """), {"lid": WC_LEAGUE_API_ID})).mappings().all()

    state: dict[str, dict] = {}
    for row in rows:
        canon = _ODDS_TO_BM.get(row["team"], row["team"])
        s = state.setdefault(canon, {"played": 0, "alive": False})
        if row["status"] == "finished":
            s["played"] += int(row["n"])
        else:
            s["alive"] = True
    return state


async def load_wc_match_stats(db: AsyncSession) -> dict[str, dict]:
    """Aggregate actual WC tournament stats from bzz_player_match_stats.

    Returns {norm_player_name: {goals, assists, minutes, xg_per_90, xa_per_90}}.

    La base contient chaque joueur en double (deux univers d'api_id liés par
    internal_id). On réconcilie par internal_id + similarité de noms, on
    dédoublonne par (identité, match), puis on émet l'agrégat sous TOUTES les
    variantes de nom de l'identité pour que le matching lineup par nom
    fonctionne quelle que soit la variante utilisée.
    """
    rows = (await db.execute(text("""
        SELECT
            ps.player_api_id,
            p.internal_id,
            p.name                              AS player_name,
            ps.event_api_id,
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
    """), {"lid": WC_LEAGUE_API_ID})).mappings().all()

    name_by_api: dict[int, str] = {}
    for r in rows:
        name_by_api[int(r["player_api_id"])] = r["player_name"]

    def _resolve_pid(r) -> int:
        api = int(r["player_api_id"])
        internal = r["internal_id"]
        if internal is not None and int(internal) != api:
            target = name_by_api.get(int(internal))
            if target is not None and _names_similar(target, r["player_name"]):
                return int(internal)
        return api

    deduped: dict[tuple[int, int], Any] = {}
    names_by_pid: dict[int, set[str]] = {}
    for r in rows:
        pid = _resolve_pid(r)
        names_by_pid.setdefault(pid, set()).add(r["player_name"])
        deduped.setdefault((pid, int(r["event_api_id"])), r)

    agg: dict[int, dict] = {}
    for (pid, _event), r in deduped.items():
        d = agg.setdefault(pid, {"goals": 0, "assists": 0, "minutes": 0.0, "xg": 0.0, "xa": 0.0})
        d["goals"] += int(r["goals"])
        d["assists"] += int(r["assists"])
        d["minutes"] += float(r["minutes_played"])
        d["xg"] += float(r["xg"])
        d["xa"] += float(r["xa"])

    # Validation : les buts attribués aux joueurs ne peuvent pas dépasser les
    # buts réellement marqués (l'écart normal = csc non attribués).
    real_goals = (await db.execute(text("""
        SELECT COALESCE(SUM(home_score + away_score), 0)
        FROM bzz_events
        WHERE league_api_id = :lid AND status = 'finished'
    """), {"lid": WC_LEAGUE_API_ID})).scalar_one()
    player_goals = sum(d["goals"] for d in agg.values())
    if real_goals and player_goals > int(real_goals):
        logger.warning(
            "wc2026_pricing: %d buts joueurs > %d buts réels — doublons résiduels probables",
            player_goals, int(real_goals),
        )

    result: dict[str, dict] = {}
    for pid, d in agg.items():
        minutes = d["minutes"]
        stats = {
            "goals":     d["goals"],
            "assists":   d["assists"],
            "minutes":   int(minutes),
            "xg_per_90": round(d["xg"] / minutes * 90, 4) if minutes >= 45 else 0.0,
            "xa_per_90": round(d["xa"] / minutes * 90, 4) if minutes >= 45 else 0.0,
        }
        for name in names_by_pid[pid]:
            result[_norm_name(name)] = stats
    return result


def _winner_share(sim: np.ndarray) -> np.ndarray:
    """P(termine 1er) par joueur, avec partage dead-heat en cas d'égalité."""
    top = sim.max(axis=0)
    tied = sim == top
    shares = tied / tied.sum(axis=0, dtype=np.float32)
    return shares.mean(axis=1)


def _top3_share(sim: np.ndarray) -> np.ndarray:
    """P(termine top 3) par joueur — 3 places, dead-heat sur la place restante.

    Pour un joueur à la valeur v : h = nb strictement au-dessus, k = nb à
    égalité. Si h >= 3 → 0 ; sinon part = (3-h)/k (toujours <= 1 car au moins
    3 valeurs sont >= la 3e plus grande, donc h+k >= 3).
    """
    n = sim.shape[0]
    if n <= 3:
        return np.ones(n, dtype=np.float32)
    v3 = np.partition(sim, -3, axis=0)[-3]  # 3e plus grande valeur par simulation
    above = sim > v3
    tied = sim == v3
    h = above.sum(axis=0, dtype=np.int32)
    k = tied.sum(axis=0, dtype=np.int32)
    seat = ((3 - h) / k).astype(np.float32)
    shares = above + tied * seat
    return shares.mean(axis=1)


def run_monte_carlo(
    lambdas_remaining_goals: list[float],
    lambdas_remaining_assists: list[float],
    wc_goals: list[int],
    wc_assists: list[int],
    n_sim: int = N_MONTE_CARLO,
    seed: int = _MC_SEED,
) -> list[dict[str, float]]:
    """Simulate total = already_scored + Poisson(lambda_remaining) for each player.

    Six marchés outright, tous avec règle dead-heat :
    top buteur / top passeur / plus décisif (G+A), et leurs variantes top 3.
    """
    rng = np.random.default_rng(seed)
    n = len(lambdas_remaining_goals)
    lg = np.maximum(np.array(lambdas_remaining_goals, dtype=float), 0)
    la = np.maximum(np.array(lambdas_remaining_assists, dtype=float), 0)
    wg = np.array(wc_goals, dtype=np.int32)
    wa = np.array(wc_assists, dtype=np.int32)
    goals_sim = wg[:, None] + rng.poisson(lg[:, None], size=(n, n_sim)).astype(np.int32)
    assists_sim = wa[:, None] + rng.poisson(la[:, None], size=(n, n_sim)).astype(np.int32)
    decisive_sim = goals_sim + assists_sim

    p_top_scorer = _winner_share(goals_sim)
    p_top_assister = _winner_share(assists_sim)
    p_most_decisive = _winner_share(decisive_sim)
    p_top3_scorer = _top3_share(goals_sim)
    p_top3_assister = _top3_share(assists_sim)
    p_top3_decisive = _top3_share(decisive_sim)

    return [
        {
            "p_top_scorer":    float(p_top_scorer[i]),
            "p_top_assister":  float(p_top_assister[i]),
            "p_most_decisive": float(p_most_decisive[i]),
            "p_top3_scorer":   float(p_top3_scorer[i]),
            "p_top3_assister": float(p_top3_assister[i]),
            "p_top3_decisive": float(p_top3_decisive[i]),
        }
        for i in range(n)
    ]


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
    team_state = await load_team_tournament_state(db)
    logger.info(
        "wc2026_pricing: loaded WC tournament stats for %d players, %d teams (%d en lice)",
        len(wc_stats), len(team_state), sum(1 for s in team_state.values() if s["alive"]),
    )

    all_entries: list[dict[str, Any]] = []

    for nation, bm in TEAM_BM.items():
        # Adjust BM by odds-derived expected games vs calibration baseline.
        # BM was calibrated assuming _E_GAMES_BASELINE games on average.
        # Dividing by baseline gives per-match quality; multiplying by
        # odds-derived E[games] gives the true tournament lambda budget.
        e_games = e_games_map.get(nation, _E_GAMES_BASELINE)
        adjusted_bm = bm * (e_games / _E_GAMES_BASELINE)

        # Fraction du tournoi restante — au niveau ÉQUIPE. Basée sur les
        # matchs réellement joués et le statut en lice, pas sur les minutes
        # des joueurs : les éliminés sont figés à 0 même quand leurs stats
        # WC n'ont pas été matchées par nom, et les remplaçants ne sont plus
        # sur-projetés.
        st = team_state.get(nation)
        if st is None:
            logger.warning("wc2026_pricing: no WC events matched for %s — treated as eliminated", nation)
            fraction_rem = 0.0
        else:
            fraction_rem = _remaining_fraction(e_games, st["played"], st["alive"])

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
            "p_most_decisive":          round(mc["p_most_decisive"], 6),
            "fair_most_decisive":       _fair_odds(mc["p_most_decisive"]),
            "p_top3_decisive":          round(mc["p_top3_decisive"], 6),
            "fair_top3_decisive":       _fair_odds(mc["p_top3_decisive"]),
            "p_top3_scorer":            round(mc["p_top3_scorer"], 6),
            "fair_top3_scorer":         _fair_odds(mc["p_top3_scorer"]),
            "p_top3_assister":          round(mc["p_top3_assister"], 6),
            "fair_top3_assister":       _fair_odds(mc["p_top3_assister"]),
            "computed_at":              now,
        })

    logger.info(
        "wc2026_pricing: computed %d players across %d nations",
        len(output), len({e["nation"] for e in output}),
    )
    return output
