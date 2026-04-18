"""Player stats ingestion — Model C (Understat + Sofascore).

Replaces FBref-based ingestion with:
  - Understat : xG, npxG, xA, xGChain, xGBuildup, goals, assists, minutes
  - Sofascore : SOT, TAP, BCC, accurate crosses, through balls, key passes

Utility functions (normalize_player_name, calculate_per_90, calculate_form_factor)
are unchanged and shared with the rest of the codebase.
"""

import math
import re
import unicodedata
from typing import Any


# ── Name normalisation (shared utility) ───────────────────────────

def normalize_player_name(name: str) -> str:
    """
    Normalize player name for consistent cross-source matching.
    - Remove accents
    - Lowercase
    - Replace spaces with hyphens
    """
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.lower().strip()
    normalized = re.sub(r"\s+", "-", normalized)
    return normalized


# ── Per-90 / form helpers (shared utility) ────────────────────────

def calculate_per_90(stat: float, minutes: int) -> float:
    """Calculate per-90-minute rate, rounded to 4 decimals."""
    if minutes <= 0:
        return 0.0
    return round((stat / minutes) * 90, 4)


def calculate_form_factor(
    recent_values: list[float],
    decay_lambda: float = 0.025,
    baseline: float | None = None,
) -> float:
    """
    Exponential decay form factor.

    Args:
        recent_values: Values ordered most-recent first
        decay_lambda:  Decay rate (0.025 ≈ 40-match half-life)
        baseline:      Expected average (default: mean of recent_values)

    Returns:
        1.0 = average form, >1.0 = above average, <1.0 = below average
    """
    if not recent_values:
        return 1.0

    if baseline is None:
        baseline = sum(recent_values) / len(recent_values)
    if baseline <= 0:
        return 1.0

    total_weight = 0.0
    weighted_sum = 0.0
    for i, value in enumerate(recent_values):
        weight = math.exp(-decay_lambda * i)
        weighted_sum += value * weight
        total_weight += weight

    if total_weight <= 0:
        return 1.0

    return (weighted_sum / total_weight) / baseline


# ── Merge logic ───────────────────────────────────────────────────

def merge_player_stats(
    understat_rows: list[dict[str, Any]],
    sofascore_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Merge Understat and Sofascore stats for each player.

    Matching is done by normalized player name.
    Understat provides the xG/xA anchor; Sofascore provides the multiplier inputs.

    Returns a list of merged dicts ready for PlayerStats upsert.
    """
    # Index both sources by normalized name
    understat_idx: dict[str, dict[str, Any]] = {}
    for row in understat_rows:
        key = normalize_player_name(row.get("player_name") or row.get("name", ""))
        understat_idx[key] = row

    sofascore_idx: dict[str, dict[str, Any]] = {}
    for row in sofascore_rows:
        key = normalize_player_name(row.get("player_name") or row.get("name", ""))
        sofascore_idx[key] = row

    all_keys = set(understat_idx) | set(sofascore_idx)
    merged = []

    for key in all_keys:
        us = understat_idx.get(key, {})
        ss = sofascore_idx.get(key, {})

        # Prefer Understat for identity (it has the cleaner name from scraper)
        base = us if us else ss

        minutes = int(us.get("time", 0) or ss.get("minutes_played", 0) or 0)
        matches = int(us.get("games", 0) or ss.get("appearances", 0) or 0)

        # Season totals
        goals = int(us.get("goals", 0) or ss.get("goals", 0) or 0)
        assists = int(us.get("assists", 0) or ss.get("assists", 0) or 0)
        xg = float(us.get("xG", 0.0) or 0.0)
        npxg = float(us.get("npxG", 0.0) or 0.0)
        xa = float(us.get("xA", 0.0) or 0.0)
        xgchain = float(us.get("xGChain", 0.0) or 0.0)
        xgbuildup = float(us.get("xGBuildup", 0.0) or 0.0)

        shots_on_target = int(ss.get("shots_on_target", 0) or 0)
        touches_attack_pen_area = int(ss.get("touches_attack_pen_area", 0) or 0)
        big_chances_created = int(ss.get("big_chances_created", 0) or 0)
        accurate_crosses = int(ss.get("accurate_crosses", 0) or 0)
        total_crosses = int(ss.get("total_crosses", 0) or 0)
        through_balls = int(ss.get("through_balls", 0) or 0)
        key_passes = int(ss.get("key_passes", us.get("key_passes", 0)) or 0)

        # Per-90 rates
        row_out: dict[str, Any] = {
            "player_name": base.get("player_name") or base.get("name", key),
            "normalized_name": key,
            "team": us.get("team_title") or ss.get("team", ""),
            "position": us.get("position") or ss.get("position"),
            "matches_played": matches,
            "minutes_played": minutes,
            # Understat
            "goals": goals,
            "assists": assists,
            "xg": xg,
            "npxg": npxg,
            "xa": xa,
            "xgchain": xgchain,
            "xgbuildup": xgbuildup,
            # Sofascore
            "shots_on_target": shots_on_target,
            "touches_attack_pen_area": touches_attack_pen_area,
            "big_chances_created": big_chances_created,
            "accurate_crosses": accurate_crosses,
            "total_crosses": total_crosses,
            "through_balls": through_balls,
            "key_passes": key_passes,
            "sofascore_rating": float(ss.get("rating", 0.0) or 0.0) or None,
            # Per-90 (stored for pricing engine)
            "xg_per_90": calculate_per_90(xg, minutes),
            "npxg_per_90": calculate_per_90(npxg, minutes),
            "xa_per_90": calculate_per_90(xa, minutes),
            "xgchain_per_90": calculate_per_90(xgchain, minutes),
            "shots_on_target_per_90": calculate_per_90(shots_on_target, minutes),
            "touches_attack_pen_area_per_90": calculate_per_90(touches_attack_pen_area, minutes),
            "bcc_per_90": calculate_per_90(big_chances_created, minutes),
            "accurate_crosses_per_90": calculate_per_90(accurate_crosses, minutes),
            "through_balls_per_90": calculate_per_90(through_balls, minutes),
        }
        merged.append(row_out)

    return merged


# ── League averages ───────────────────────────────────────────────

def compute_league_averages_from_merged(
    merged_rows: list[dict[str, Any]],
    min_minutes: int = 450,
) -> dict[str, float]:
    """
    Compute league-average per-90 values from a list of merged player rows.

    Used by goalscorer and assist pricing modules for quality/creation multiplier normalization.

    Only players with >= min_minutes are included.
    """
    eligible = [r for r in merged_rows if (r.get("minutes_played") or 0) >= min_minutes]
    if not eligible:
        return {}

    n = len(eligible)

    def avg(field: str) -> float:
        return round(sum(r.get(field, 0.0) or 0.0 for r in eligible) / n, 4)

    return {
        # Goalscorer multiplier inputs
        "sot":     avg("shots_on_target_per_90"),
        "tap":     avg("touches_attack_pen_area_per_90"),
        "xgchain": avg("xgchain_per_90"),
        # Assist multiplier inputs
        "bcc":     avg("bcc_per_90"),
        "crosses": avg("accurate_crosses_per_90"),
        "tb":      avg("through_balls_per_90"),
        # Anchor baselines (informational)
        "npxg":    avg("npxg_per_90"),
        "xa":      avg("xa_per_90"),
    }
