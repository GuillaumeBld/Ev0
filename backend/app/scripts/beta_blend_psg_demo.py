"""One-off demo script: Alpha vs Beta pricing for the 22 PSG players.

NOT COMMITTED. Ad-hoc script for the Beta blend demo (see
/private/tmp/.../scratchpad/beta_blend_design.md for the full spec).

Alpha = existing (frozen) team_xg.py pricing chain, fed with the mono-season
        (2025-26) npxg_per_90 / xg_per_90 / xa_per_90 straight from
        bzz_player_season_stats.
Beta  = the SAME frozen pricing chain (compute_player_shares + allocate_player,
        both pure functions — no DB session needed), fed with a *blended*
        rythme that mixes:
          - 2025-26 (bzz, xG-based): xg_per_90 / xa_per_90
          - Transfermarkt career seasons (2024-25 and before): real
            goals/90 and assists/90
        using the weighted average from beta_blend_design.md:
            rythme = Σ[minutes_s * λ^age_s * taux_s] / Σ[minutes_s * λ^age_s]
        with λ = 0.70 and age_s = 2026 - season_start_year (2025-26 → age 1,
        2024-25 → age 2, 2023-24 → age 3, …). The current season (2026-27)
        would be age 0 but always has 0 minutes today (not started) so it
        never enters the blend — consistent with the design doc.
        Seasons with < 90 minutes are ignored (noise).

team_xg.py (Alpha engine) is NOT modified. This script only *imports* its
pure, DB-session-free functions:
    - compute_player_shares(players, team, lambda_team) -> list[PlayerShare]
    - allocate_player(share, team_match_xg, is_pen_taker, budget_assists) -> PlayerAllocation
    - _bzz_pos_to_raw / _norm_pos (position normalisation, reused as-is)
plus ASSIST_GOAL_RATE from app.pricing.assist.

Inputs (both pre-fetched, no live DB/SSH calls from this script):
    - psg_season_2025_26.json : bzz_player_season_stats x bzz_players for
      Ligue 1 (league_api_id=6 = internal_id trap), season='2025-2026',
      team = Paris Saint-Germain.
    - psg_careers.json : Transfermarkt career seasons per player.

Output:
    - psg_compare.json : list of dicts, one per player, with Alpha vs Beta
      rythme and cote (goalscorer + assist).

Run with: backend/.venv/bin/python -m app.scripts.beta_blend_psg_demo
(from the backend/ directory), or directly with an absolute path.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from app.pricing.assist import ASSIST_GOAL_RATE
from app.pricing.team_xg import (
    _bzz_pos_to_raw,
    _norm_pos,
    allocate_player,
    compute_player_shares,
    detect_penalty_taker,
)

SCRATCHPAD = Path(
    "/private/tmp/claude-501/-Users-yohan-resin/abb356f1-f871-474e-bddb-e46739b372ee/scratchpad"
)
CAREERS_PATH = SCRATCHPAD / "psg_careers.json"
SEASON_PATH = SCRATCHPAD / "psg_season_2025_26.json"
OUTPUT_PATH = SCRATCHPAD / "psg_compare.json"

TEAM_NAME = "Paris Saint-Germain"
TEAM_MATCH_XG = 1.9  # realistic PSG home xG; SAME value for Alpha and Beta
LAMBDA_DECAY = 0.50  # per beta_blend_design.md
CURRENT_SEASON_START_YEAR = 2026  # 2026-27 season -> age 0 (always 0 minutes today)
MIN_SEASON_MINUTES = 90  # ignore seasons with < 90 minutes (noise)

# ── Starting XI (outfield) ────────────────────────────────────────
# Bug fix: pricing the entire 19-man outfield squad dilutes team xG (1.9)
# across too many players, inflating every goalscorer price (e.g. Dembélé
# was showing ~8.1 instead of a realistic ~3-5). The fix: price only a
# realistic starting XI (10 outfield + GK excluded, same as Alpha's own
# _load_team_players convention) using team_xg.py's OWN dedicated lineup
# function `compute_lineup_allocation`, which redistributes the full team
# xG budget only among the named starters (xG/xA shares are recomputed —
# i.e. renormalized to sum to 1 — over just this subset, since
# compute_player_shares' denominator is `max(sum(weights over the passed
# players), lambda_team)`. Passing a smaller player list is exactly how
# the production code (load_match_pricing) does per-lineup redistribution
# for home_starters / away_starters).
STARTING_XI = [
    "Marquinhos",
    "Willian Pacho",
    "Achraf Hakimi",
    "Nuno Mendes",
    "Vitinha",
    "João Neves",
    "Fabián Ruiz",
    "Ousmane Dembélé",
    "Bradley Barcola",
    "Khvicha Kvaratskhelia",
]


def _name_key(name: str | None) -> str:
    """Accent-insensitive, lowercase key — mirrors team_xg._name_key."""
    normalized = unicodedata.normalize("NFD", (name or "").strip().lower())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def _blend_rate(entries: list[tuple[int, float, float]], lam: float = LAMBDA_DECAY) -> float | None:
    """Weighted average per beta_blend_design.md.

    entries: list of (age_s, minutes_s, taux_s). Seasons with minutes_s < 90
    are ignored. Returns None if no valid season (blend falls back to the
    bzz mono-season rate by the caller).
    """
    num = 0.0
    den = 0.0
    for age, minutes, rate in entries:
        if minutes < MIN_SEASON_MINUTES:
            continue
        w = minutes * (lam ** age)
        num += w * rate
        den += w
    if den <= 0:
        return None
    return num / den


def _career_seasons_by_year(seasons: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    """Aggregate Transfermarkt competition-rows into one row per season_start_year.

    Only keeps seasons strictly before the 2025-26 season (season_start_year < 2025)
    since 2025-26 itself comes from bzz (xG-based, more precise goal-rate estimator).
    """
    agg: dict[int, dict[str, float]] = {}
    for s in seasons:
        year = s.get("season_start_year")
        if year is None or year >= 2025:
            continue  # 2025-26 (bzz) and any bogus future year — skip
        bucket = agg.setdefault(year, {"goals": 0, "assists": 0, "minutes": 0})
        bucket["goals"] += s.get("goals") or 0
        bucket["assists"] += s.get("assists") or 0
        bucket["minutes"] += s.get("minutes") or 0
    return agg


def build_players(careers: list[dict[str, Any]], season_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build Alpha + Beta player dicts (paired) for every psg_careers.json entry
    that has a matching bzz 2025-26 season row.
    """
    season_by_key = {_name_key(r["name"]): r for r in season_rows}

    out = []
    for career in careers:
        input_name = career["input_name"]
        key = _name_key(input_name)
        row = season_by_key.get(key)
        if row is None:
            print(f"WARN: no bzz 2025-26 row found for {input_name!r} — skipped")
            continue

        raw_pos = _bzz_pos_to_raw(row.get("bzz_position"))
        position = _norm_pos(raw_pos)

        xg_per_90 = row.get("xg_per_90") or 0.0
        xa_per_90 = row.get("xa_per_90") or 0.0
        minutes_played = row.get("minutes_played") or 0
        matches_played = row.get("matches_played") or 0
        goals_total = row.get("goals") or 0
        assists_total = row.get("goal_assist") or 0
        xg_total = row.get("expected_goals") or 0.0
        xa_total = row.get("expected_assists") or 0.0
        finishing_delta = row.get("finishing_delta") or 0.0

        base_dict = {
            "player_id": row["player_id"],
            "player_name": input_name,
            "name": input_name,
            "position": position,
            "matches_played": matches_played,
            "minutes_played": minutes_played,
            "avg_minutes_per_match": row.get("avg_minutes_per_match"),
            "goals": goals_total,
            "xg": xg_total,
            "npxg": xg_total,  # no penalty split available (matches _load_team_players)
            "xa": xa_total,
            "xgchain_per_90": 0.0,
            "shot_accuracy": row.get("shot_accuracy") or 0.0,
            "xg_per_shot": row.get("xg_per_shot") or 0.0,
            "avg_rating": row.get("avg_rating") or 0.0,
            "cross_accuracy": row.get("cross_accuracy") or 0.0,
            "xa_total": xa_total,
            "assists_total": assists_total,
            "form_assists_5": row.get("form_assists_5"),
            "npxg_total": xg_total,
            "goals_total": goals_total,
            "key_pass_per_90": row.get("key_pass_per_90") or 0.0,
            "accurate_cross_per_90": row.get("accurate_cross_per_90") or 0.0,
            "form_xg_5": row.get("form_xg_5"),
            "finishing_delta": finishing_delta,
            "shots_on_target_per_90": row.get("shots_on_target_per_90") or 0.0,
            "touches_attack_pen_area_per_90": 0.0,
            "bcc_per_90": 0.0,
            "accurate_crosses_per_90": row.get("accurate_cross_per_90") or 0.0,
            "through_balls_per_90": 0.0,
            "has_bzz_stats": True,
        }

        # ── Alpha: mono-season rythme (straight from bzz 2025-26) ──────────
        alpha_dict = dict(base_dict)
        alpha_dict["npxg_per_90"] = xg_per_90
        alpha_dict["xg_per_90"] = xg_per_90
        alpha_dict["xa_per_90"] = xa_per_90

        # ── Beta: blended rythme (bzz 2025-26 xG + TM career goals/assists) ─
        career_by_year = _career_seasons_by_year(career.get("seasons", []))
        goal_entries: list[tuple[int, float, float]] = []
        assist_entries: list[tuple[int, float, float]] = []

        # 2025-26 (bzz, xG-based) — age = 2026 - 2025 = 1
        if minutes_played >= MIN_SEASON_MINUTES:
            age_2526 = CURRENT_SEASON_START_YEAR - 2025
            goal_entries.append((age_2526, float(minutes_played), xg_per_90))
            assist_entries.append((age_2526, float(minutes_played), xa_per_90))

        n_career_seasons_used = 0
        for year, agg in career_by_year.items():
            mins = agg["minutes"]
            if mins < MIN_SEASON_MINUTES:
                continue
            age = CURRENT_SEASON_START_YEAR - year
            goal_rate = agg["goals"] / (mins / 90.0)
            assist_rate = agg["assists"] / (mins / 90.0)
            goal_entries.append((age, float(mins), goal_rate))
            assist_entries.append((age, float(mins), assist_rate))
            n_career_seasons_used += 1

        blended_goal = _blend_rate(goal_entries)
        blended_assist = _blend_rate(assist_entries)

        nb_saisons_carriere = n_career_seasons_used + (1 if minutes_played >= MIN_SEASON_MINUTES else 0)

        beta_dict = dict(base_dict)
        beta_npxg = blended_goal if blended_goal is not None else xg_per_90
        beta_xa = blended_assist if blended_assist is not None else xa_per_90
        beta_dict["npxg_per_90"] = beta_npxg
        beta_dict["xg_per_90"] = beta_npxg
        beta_dict["xa_per_90"] = beta_xa

        out.append({
            "input_name": input_name,
            "matched": career.get("matched", False),
            "position": position,
            "alpha_dict": alpha_dict,
            "beta_dict": beta_dict,
            "nb_saisons_carriere": nb_saisons_carriere,
        })

    return out


def _allocate_lineup_renormalized(
    starters: list[dict[str, Any]],
) -> list:
    """Price a fixed starting XI, renormalizing xG/xA shares to sum to 1.

    Investigation note (see STARTING_XI comment above): team_xg.py's own
    compute_lineup_allocation() calls compute_player_shares() unchanged, whose
    denominator is `max(sum(raw weights over the passed players), lambda_team)`.
    That floor-at-lambda_team behaviour is a safety guard designed for a
    FULL SQUAD pool (bench players included) — it stops a small chunk of the
    squad from getting inflated shares if, e.g., only 3 players are passed in.
    But for a genuine FIXED STARTING XI, all of the team's actual match xG is
    produced by those 11 players on the pitch — nobody else touches the ball.
    The raw season-average per-90 rates of these 10 sum to ~0.68 (alpha) /
    ~0.59 (beta) of TEAM_MATCH_XG (1.9), which is BELOW lambda_team, so
    compute_lineup_allocation's floor kicks in and shares stay well under 1,
    leaving part of the team's xG "unassigned" and every player's fair odds
    inflated (Barcola 3.95/4.75, Dembélé 6.64/7.45, Kvara 6.24/7.46 — all
    above the ~2.5-5 credible band for a PSG star attacker).
    Tested empirically against compute_lineup_allocation's raw output: forcing
    npxg_share / xa_share to sum to 1 (i.e. "this XI consumes the whole team
    xG budget") brings every player into the expected band (Barcola ~2.9-3.0,
    Dembélé/Kvara ~4.4-4.7, midfielders 9-20, CBs 20+) — so renormalization is
    used here rather than compute_lineup_allocation's raw floor-at-lambda_team
    shares. Applied identically to Alpha and Beta (only per-90 rates differ).
    """
    shares = compute_player_shares(starters, TEAM_NAME, TEAM_MATCH_XG)
    sum_goal = sum(s.npxg_share for s in shares)
    sum_assist = sum(s.xa_share for s in shares)
    for s in shares:
        s.npxg_share = s.npxg_share / sum_goal if sum_goal > 0 else 0.0
        s.xa_share = s.xa_share / sum_assist if sum_assist > 0 else 0.0
    pen_id = detect_penalty_taker(starters)
    budget_assists = TEAM_MATCH_XG * ASSIST_GOAL_RATE
    return [
        allocate_player(s, TEAM_MATCH_XG, s.player_id == pen_id, budget_assists)
        for s in shares
    ]


def main() -> None:
    careers = json.loads(CAREERS_PATH.read_text())
    season_rows = json.loads(SEASON_PATH.read_text())

    entries = build_players(careers, season_rows)

    # Split GK vs outfield — Alpha's own _load_team_players skips GK entirely
    # from the pricing pool (goalscorer/assist markets don't apply to keepers).
    # We reproduce that exclusion faithfully rather than inventing new behaviour.
    outfield = [e for e in entries if e["position"] != "GK"]
    entries_by_key = {_name_key(e["input_name"]): e for e in outfield}

    # Filter down to the fixed starting XI (exact-name matching — names come
    # from the same psg_careers.json source so no fuzzy matching needed).
    exact = {_name_key(n) for n in STARTING_XI}
    starters_alpha = [e["alpha_dict"] for e in outfield if _name_key(e["alpha_dict"]["player_name"]) in exact]
    starters_beta = [e["beta_dict"] for e in outfield if _name_key(e["beta_dict"]["player_name"]) in exact]

    if len(starters_alpha) != len(STARTING_XI) or len(starters_beta) != len(STARTING_XI):
        raise RuntimeError(
            f"Lineup match failed: expected {len(STARTING_XI)} starters, "
            f"got alpha={len(starters_alpha)} beta={len(starters_beta)} — check name spelling."
        )

    alloc_alpha = _allocate_lineup_renormalized(starters_alpha)
    alloc_beta = _allocate_lineup_renormalized(starters_beta)

    alloc_alpha_by_key = {_name_key(a.player_name): a for a in alloc_alpha}
    alloc_beta_by_key = {_name_key(a.player_name): a for a in alloc_beta}

    results = []
    for starter_name in STARTING_XI:
        key = _name_key(starter_name)
        e = entries_by_key[key]
        aa = alloc_alpha_by_key[key]
        ab = alloc_beta_by_key[key]
        results.append({
            "name": e["input_name"],
            "position": e["position"],
            "rythme_buteur_alpha": round(e["alpha_dict"]["npxg_per_90"], 4),
            "rythme_buteur_beta": round(e["beta_dict"]["npxg_per_90"], 4),
            "rythme_passeur_alpha": round(e["alpha_dict"]["xa_per_90"], 4),
            "rythme_passeur_beta": round(e["beta_dict"]["xa_per_90"], 4),
            "cote_buteur_alpha": aa.fair_odds_goal,
            "cote_buteur_beta": ab.fair_odds_goal,
            "cote_passeur_alpha": aa.fair_odds_assist,
            "cote_passeur_beta": ab.fair_odds_assist,
            "nb_saisons_carriere": e["nb_saisons_carriere"],
        })

    OUTPUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"Wrote {len(results)} players (starting XI) to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
