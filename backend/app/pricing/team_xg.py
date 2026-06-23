"""Top-Down Match-Centric pricing engine — Bzzoiro model.

Three-stage pipeline:
  Stage 1 — Team Match xG : MarketXgService (market-implied or Dixon-Coles)
  Stage 2 — Player Shares : form-blended top-down shares (npxg_share / xa_share)
  Stage 3 — Player Pricing: finishing_multiplier + creation_multiplier_v2 per player

Source: Bzzoiro (bzz_player_season_stats + bzz_players)
"""

from __future__ import annotations

import logging
import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.pricing.assist import (
    ASSIST_GOAL_RATE,
    calculate_assist_lambda,
    calculate_creation_multiplier_v2,
    calculate_xa_conversion,
    calculate_supersub_prob_assist,
)
from app.pricing.goalscorer import (
    calculate_finishing_multiplier,
    calculate_supersub_prob,
)
from app.services.market_xg import MarketXgResult, MarketXgService

# ── Constants ─────────────────────────────────────────────────────

HOME_ADVANTAGE = 1.22
SHRINKAGE_N = 30.0
PENS_PER_MATCH = 0.10
PEN_CONVERSION = 0.78
CLAMP_MULTIPLIER_MIN = 0.5
CLAMP_MULTIPLIER_MAX = 2.0
# Deprecated — kept for backward compat (imports, tests). Logic moved to 11-bucket POSITION_NPXG_PRIORS.
DF_SETPIECE_DISCOUNT: float = 0.55

FORM_WEIGHTS_BY_POSITION: dict[str, float] = {
    # 11 fine-grained buckets
    "CF_lone": 0.30, "CF_pair": 0.28, "SS": 0.25, "winger": 0.22,
    "AM": 0.20, "CM": 0.18, "DM": 0.12,
    "WB": 0.10, "FB": 0.08, "CB": 0.06, "GK": 0.00,
    # Legacy 3-bucket fallback (kept for backward compat)
    "FW": 0.25, "MF": 0.20, "DF": 0.10,
}
_FORM_WEIGHT_DEFAULT = 0.18

# 11-bucket npxG/90 priors (calibrated on Big5 + UCL 2024-25, ≥450 min)
POSITION_NPXG_PRIORS: dict[str, float] = {
    "CF_lone": 0.42, "CF_pair": 0.35, "SS": 0.22,
    "winger": 0.14, "AM": 0.10, "CM": 0.05, "DM": 0.03,
    "WB": 0.02, "FB": 0.01, "CB": 0.01, "GK": 0.00,
    # Legacy fallback
    "FW": 0.25, "MF": 0.08, "DF": 0.02,
}
POSITION_XA_PRIORS: dict[str, float] = {
    "CF_lone": 0.08, "CF_pair": 0.09, "SS": 0.14,
    "winger": 0.18, "AM": 0.18, "CM": 0.10, "DM": 0.05,
    "WB": 0.08, "FB": 0.06, "CB": 0.02, "GK": 0.00,
    # Legacy fallback
    "FW": 0.10, "MF": 0.15, "DF": 0.03,
}

INTERNATIONAL_LEAGUES: frozenset[str] = frozenset({
    "world_cup_2026",
    "friendly_international",
    "nations_league_uefa",
    "nations_league_concacaf",
})

# ── Matchup factor ─────────────────────────────────────────────────
# ── CPA (Corners / Phases Arrêtées) additive module ───────────────
# Adds a λ bonus to CB/WB/FB players from set-piece delivery.
# Calibrated: top CPA teams (≥7 corners/match) vs bottom (≤4).
CPA_BONUS_BY_POSITION: dict[str, float] = {
    "CB": 0.025,   # primary aerial threat
    "WB": 0.012,   # occasional near-post run
    "FB": 0.008,   # rare but happens
}
CPA_CORNERS_THRESHOLD = 5.5   # avg corners/match above which CPA bonus applies
CPA_CORNERS_HIGH      = 8.0   # max scaling anchor
_CPA_BONUS_DEFAULT    = 0.0


# ── Position normalisation ────────────────────────────────────────

def _bzz_pos_to_raw(bzz_pos: str | None) -> str | None:
    """Map Bzzoiro single-char position codes to pricing engine format."""
    return {"G": "GK", "D": "DF", "M": "MF", "F": "FW"}.get(bzz_pos or "", bzz_pos)


def _name_key(name: str | None) -> str:
    """Accent-insensitive lowercase key for player deduplication.

    Strips diacritics so 'José' and 'Jose' collide to the same key.
    """
    normalized = unicodedata.normalize("NFD", (name or "").strip().lower())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


_NORM_POS_11_BUCKET: dict[str, str] = {
    "CF_LONE": "CF_lone", "CF_PAIR": "CF_pair", "SS": "SS", "WINGER": "winger",
    "AM": "AM", "CM": "CM", "DM": "DM", "WB": "WB", "FB": "FB", "CB": "CB", "GK": "GK",
}


def _norm_pos(raw: str | None) -> str | None:
    """Map Bzzoiro position codes to 11-bucket system.

    Buckets: CF_lone, CF_pair, SS, winger, AM, CM, DM, WB, FB, CB, GK
    Falls back to legacy FW/MF/DF/GK when sub-position info is absent.
    """
    if not raw:
        return None
    p = raw.strip().upper()
    # Explicit 11-bucket pass-through (case-insensitive → canonical casing)
    if p in _NORM_POS_11_BUCKET:
        return _NORM_POS_11_BUCKET[p]
    # GK
    if "GK" in p or p == "G":
        return "GK"
    # Defensive line
    if p in ("CB", "SW"):
        return "CB"
    if p in ("LB", "RB"):
        return "FB"
    if p in ("LWB", "RWB"):
        return "WB"
    # Defensive mid
    if p in ("DM", "CDM", "CM/DM"):
        return "DM"
    # Central mid
    if p in ("CM", "MF", "M"):
        return "CM"
    # Attacking mid / second striker
    if p in ("AM", "CAM"):
        return "AM"
    # Wingers
    if p in ("LW", "RW", "LM", "RM", "W"):
        return "winger"
    # Forwards — default to CF_lone when no pairing info
    if p in ("FW", "CF", "ST", "F"):
        return "CF_lone"
    # Broad legacy fallback (Bzzoiro single-char already mapped by _bzz_pos_to_raw)
    if p == "DF":
        return "CB"
    if p == "AM":
        return "AM"
    return None


# ── Dataclasses ───────────────────────────────────────────────────

@dataclass
class TeamStats:
    team: str
    attack_xg_per_match: float
    defense_xga_per_match: float
    finishing: float


@dataclass
class PlayerShare:
    player_id: int
    player_name: str
    team: str
    position: str | None
    npxg_share: float
    xa_share: float
    expected_minutes: float
    matches_played: int
    is_pen_taker: bool = False
    npxg_per_90: float = 0.0
    xa_per_90: float = 0.0
    xgchain_per_90: float = 0.0
    conversion_rate: float = 1.0
    sot_per_90: float = 0.0
    tap_per_90: float = 0.0
    bcc_per_90: float = 0.0
    accurate_crosses_per_90: float = 0.0
    through_balls_per_90: float = 0.0
    shot_accuracy: float = 0.0
    xg_per_shot: float = 0.0
    avg_rating: float = 0.0          # brut (0-10)
    key_pass_per_90: float = 0.0
    accurate_cross_per_90: float = 0.0
    cross_accuracy: float = 0.0
    npxg_total: float = 0.0
    goals_total: int = 0
    xa_total: float = 0.0
    assists_total: int = 0
    form_xg_5: float | None = None
    form_assists_5: float | None = None
    finishing_delta: float = 0.0
    has_bzz_stats: bool = False  # True when loaded from bzz_player_season_stats


@dataclass
class PlayerAllocation:
    player_id: int
    player_name: str
    team: str
    position: str | None
    expected_minutes: float
    is_pen_taker: bool
    npxg_share: float
    xa_share: float
    quality_multiplier: float
    lambda_open_play: float
    lambda_penalty: float
    lambda_total: float
    prob_goal: float
    fair_odds_goal: float
    creation_multiplier: float
    lambda_assist: float
    prob_assist: float
    fair_odds_assist: float
    matches_played: int = 0
    has_form_goal: bool = False
    has_form_assist: bool = False
    # Supersub fields
    p_goal_supersub: float = 0.0
    fair_odds_goal_supersub: float = 99.0
    p_assist_supersub: float = 0.0
    fair_odds_assist_supersub: float = 99.0
    p_sub: float = 0.35
    avg_sub_time: float = 65.0
    sub_premium_goal: float = 0.0
    sub_premium_assist: float = 0.0


@dataclass
class MatchPricingResult:
    fixture_id: int
    home_team: str
    away_team: str
    home_match_xg: float
    away_match_xg: float
    xg_source: str = "market_implied"
    home_players: list[PlayerAllocation] = field(default_factory=list)
    away_players: list[PlayerAllocation] = field(default_factory=list)
    # Optional redistributed pricing when caller supplies a starter list
    home_lineup_players: list[PlayerAllocation] | None = None
    away_lineup_players: list[PlayerAllocation] | None = None
    last_scraped_at: datetime | None = None
    # Match-level derived probabilities
    p00: float = 0.0   # P(0-0) = e^(-(lambda_h + lambda_a)) under independent Poisson




def estimate_team_match_xg(
    attack_xg: float,
    opponent_xga: float,
    league_avg_xg: float,
    league_avg_xga: float,
    is_home: bool,
) -> float:
    """Dixon-Coles style match xG estimate (unchanged)."""
    attack_ratio = attack_xg / league_avg_xg if league_avg_xg > 0 else 1.0
    def_ratio = opponent_xga / league_avg_xga if (league_avg_xga > 0 and opponent_xga > 0) else 1.0
    home_factor = HOME_ADVANTAGE if is_home else 1.0
    return max(0.3, min(4.0, attack_ratio * def_ratio * league_avg_xg * home_factor))


# ── Guard-rail: assess_inputs ────────────────────────────────────

@dataclass
class InputWarning:
    code: str
    player_id: int | None
    player_name: str | None
    detail: str


def assess_inputs(players: list[dict[str, Any]], team: str) -> list[InputWarning]:
    """Validate player data before pricing. Returns warnings; never raises.

    Checks:
    - matches_played=1 with minutes_played>180 (data corruption)
    - npxg_per_90 > 1.5 (implausible; likely ingestion error)
    - no players found (empty squad)
    - avg_minutes_per_match > 90 (impossible per-match average)
    """
    warnings: list[InputWarning] = []
    if not players:
        warnings.append(InputWarning("NO_PLAYERS", None, None, f"team={team}: 0 players loaded"))
        return warnings
    for p in players:
        pid = p.get("player_id")
        pname = p.get("player_name", "?")
        matches = p.get("matches_played") or 0
        mins = p.get("minutes_played") or 0
        avg_mins = p.get("avg_minutes_per_match") or 0.0
        npxg = p.get("npxg_per_90") or p.get("xg_per_90") or 0.0

        if matches == 1 and mins > 180:
            warnings.append(InputWarning(
                "CORRUPT_MATCHES_PLAYED", pid, pname,
                f"matches_played=1 but minutes_played={mins} (probable aggregation error)"
            ))
        if npxg > 1.5:
            warnings.append(InputWarning(
                "IMPLAUSIBLE_NPXG", pid, pname,
                f"npxg_per_90={npxg:.3f} > 1.5 — likely ingestion error"
            ))
        if avg_mins > 90.0:
            warnings.append(InputWarning(
                "IMPOSSIBLE_AVG_MINS", pid, pname,
                f"avg_minutes_per_match={avg_mins:.1f} > 90"
            ))
    return warnings


# ── CPA additive module ───────────────────────────────────────────

def compute_cpa_bonus(
    position: str | None,
    team_corners_per_match: float | None,
) -> float:
    """Return additive λ bonus from set pieces for CB/WB/FB.

    Scales linearly between CPA_CORNERS_THRESHOLD and CPA_CORNERS_HIGH.
    Returns 0.0 for other positions or when corners data is unavailable.
    """
    base = CPA_BONUS_BY_POSITION.get(position or "", _CPA_BONUS_DEFAULT)
    if base == 0.0 or team_corners_per_match is None:
        return 0.0
    if team_corners_per_match <= CPA_CORNERS_THRESHOLD:
        return 0.0
    scale = min(
        (team_corners_per_match - CPA_CORNERS_THRESHOLD)
        / (CPA_CORNERS_HIGH - CPA_CORNERS_THRESHOLD),
        1.0,
    )
    return round(base * scale, 4)


# ── Stage 2: Player shares ────────────────────────────────────────

def compute_player_shares(
    players: list[dict[str, Any]],
    team: str,
    lambda_team: float,
) -> list[PlayerShare]:
    """Compute top-down shares via form-blended rates. denominator = max(sum weights, λ_team)."""
    # Pass 1 — compute blended weights per player
    entries = []
    for p in players:
        matches = p.get("matches_played", 0) or 0
        mins = p.get("minutes_played", 0) or 0
        avg_mins = p.get("avg_minutes_per_match") or ((mins / matches) if matches > 0 else 75.0)
        exp_mins = min(90.0, max(1.0, avg_mins))
        mins_ratio = exp_mins / 90.0

        # Goal weight — blend season xg_per_90 + form_xg_5 (position-specific form weight)
        pos = p.get("position")
        form_w = FORM_WEIGHTS_BY_POSITION.get(pos or "", _FORM_WEIGHT_DEFAULT)
        xg_per_90 = p.get("npxg_per_90") or p.get("xg_per_90") or 0.0
        form_xg = p.get("form_xg_5")
        if form_xg is not None and exp_mins > 0:
            form_rate = form_xg / (5.0 * exp_mins / 90.0)
            blended_xg = (1 - form_w) * xg_per_90 + form_w * form_rate
        else:
            blended_xg = xg_per_90
        goal_weight = blended_xg * mins_ratio

        # Assist weight — blend season xa_per_90 + form_assists_5
        xa_per_90 = p.get("xa_per_90") or 0.0
        form_xa = p.get("form_assists_5")
        if form_xa is not None and exp_mins > 0:
            form_xa_rate = form_xa / (5.0 * exp_mins / 90.0)
            blended_xa = (1 - form_w) * xa_per_90 + form_w * form_xa_rate
        else:
            blended_xa = xa_per_90
        assist_weight = blended_xa * mins_ratio

        entries.append((p, exp_mins, goal_weight, assist_weight))

    # Pass 2 — denominators
    total_goal = sum(e[2] for e in entries)
    total_assist = sum(e[3] for e in entries)
    budget_assists = lambda_team * ASSIST_GOAL_RATE
    goal_denom = max(total_goal, lambda_team) or 1e-9
    assist_denom = max(total_assist, budget_assists) or 1e-9

    # Pass 3 — build PlayerShare objects
    shares = []
    for p, exp_mins, goal_weight, assist_weight in entries:
        pos = p.get("position")
        matches = p.get("matches_played", 0) or 0
        npxg_total = p.get("npxg_total") or p.get("npxg") or 0.0
        goals_total = p.get("goals_total") or p.get("goals") or 0

        shares.append(PlayerShare(
            player_id=p["player_id"],
            player_name=p["player_name"],
            team=team,
            position=pos,
            npxg_share=goal_weight / goal_denom,
            xa_share=assist_weight / assist_denom,
            expected_minutes=exp_mins,
            matches_played=matches,
            npxg_per_90=p.get("npxg_per_90") or p.get("xg_per_90") or 0.0,
            xa_per_90=p.get("xa_per_90") or 0.0,
            xgchain_per_90=p.get("xgchain_per_90") or 0.0,
            conversion_rate=1.0,  # computed in allocate_player
            sot_per_90=p.get("shots_on_target_per_90") or 0.0,
            tap_per_90=p.get("touches_attack_pen_area_per_90") or 0.0,
            bcc_per_90=p.get("bcc_per_90") or 0.0,
            accurate_crosses_per_90=p.get("accurate_crosses_per_90") or 0.0,
            through_balls_per_90=p.get("through_balls_per_90") or 0.0,
            shot_accuracy=p.get("shot_accuracy") or 0.0,
            xg_per_shot=p.get("xg_per_shot") or 0.0,
            avg_rating=p.get("avg_rating") or 0.0,      # brut (0-10)
            cross_accuracy=p.get("cross_accuracy") or 0.0,
            npxg_total=npxg_total,
            goals_total=goals_total,
            xa_total=p.get("xa_total") or 0.0,
            assists_total=p.get("assists_total") or 0,
            key_pass_per_90=p.get("key_pass_per_90") or 0.0,
            accurate_cross_per_90=p.get("accurate_cross_per_90") or 0.0,
            form_xg_5=p.get("form_xg_5"),
            form_assists_5=p.get("form_assists_5"),
            finishing_delta=p.get("finishing_delta") or 0.0,
            has_bzz_stats=p.get("has_bzz_stats", False),
        ))
    return shares


def detect_penalty_taker(players: list[dict[str, Any]]) -> int | None:
    """Auto-detect penalty taker: highest xG − npxG (= penalty xG).

    Returns None when no player has npxG data (e.g. international matches where
    Bzzoiro doesn't provide xG/npxG split) to avoid spurious detection.
    Requires pen_xg > 0.05 to filter noise from data rounding errors.
    """
    has_any_npxg = any((p.get("npxg") or 0.0) > 0.0 for p in players)
    if not has_any_npxg:
        return None

    best_id: int | None = None
    best_pen_xg = 0.05  # minimum threshold — avoids noise from rounding
    for p in players:
        pen_xg = (p.get("xg", 0.0) or 0.0) - (p.get("npxg", 0.0) or 0.0)
        if pen_xg > best_pen_xg:
            best_pen_xg = pen_xg
            best_id = p["player_id"]
    return best_id


# ── Stage 3: Player allocation with Model C ───────────────────────

def allocate_player(
    share: PlayerShare,
    team_match_xg: float,
    is_pen_taker: bool,
    budget_assists: float,
    league_avg_goalscorer: dict[str, float] | None = None,
    league_avg_assist: dict[str, float] | None = None,
    p_sub: float = 0.35,
    avg_sub_time: float = 65.0,
    team_corners_per_match: float | None = None,
) -> PlayerAllocation:
    """Compute Poisson lambdas using new top-down formulas.

    team_corners_per_match: avg corners per match for the attacking team;
        used by compute_cpa_bonus to add set-piece λ for CB/WB/FB.
    """
    mins_ratio = share.expected_minutes / 90.0

    # ── Goalscorer ────────────────────────────────────────────────
    goal_stats = {
        "shot_accuracy": share.shot_accuracy,
        "xg_per_shot": share.xg_per_shot,
        "avg_rating": share.avg_rating,        # brut (0-10)
        "matches_played": share.matches_played,
        "npxg_total": share.npxg_total,
        "goals": share.goals_total,
    }
    finishing_mult = calculate_finishing_multiplier(goal_stats, share.position)
    lambda_open_play = share.npxg_share * team_match_xg * finishing_mult
    lambda_penalty = PEN_CONVERSION * PENS_PER_MATCH * mins_ratio if is_pen_taker else 0.0
    lambda_cpa = compute_cpa_bonus(share.position, team_corners_per_match) * mins_ratio
    lambda_total = max(0.001, min(lambda_open_play + lambda_penalty + lambda_cpa, 3.0))
    prob_goal = 1 - math.exp(-lambda_total)
    fair_odds_goal = round(1 / prob_goal, 2) if prob_goal > 0 else 9999.0

    # ── Assist ────────────────────────────────────────────────────
    assist_stats = {
        "xa_per_90": share.xa_per_90,
        "key_pass_per_90": share.key_pass_per_90,
        "accurate_cross_per_90": share.accurate_cross_per_90,
        "cross_accuracy": share.cross_accuracy,
        "matches_played": share.matches_played,
        "xa_total": share.xa_total,
        "assists": share.assists_total,
    }
    creation_mult = calculate_creation_multiplier_v2(assist_stats, share.position)
    xa_conversion = calculate_xa_conversion(assist_stats, share.position)
    lambda_assist = calculate_assist_lambda(share.xa_share, budget_assists, creation_mult, xa_conversion)
    prob_assist = 1 - math.exp(-lambda_assist)
    fair_odds_assist = round(1 / prob_assist, 2) if prob_assist > 0 else 9999.0

    # ── Supersub ──────────────────────────────────────────────────
    # calculate_supersub_prob expects the full 90-min lambda; lambda_total and
    # lambda_assist are already scaled by mins_ratio (via npxg_share / xa_share),
    # so we must un-scale them before passing to the formula.
    position_str = share.position or "MF"
    lambda_total_90 = lambda_total / mins_ratio if mins_ratio > 0 else lambda_total
    lambda_assist_90 = lambda_assist / mins_ratio if mins_ratio > 0 else lambda_assist

    p_goal_ss = calculate_supersub_prob(
        lambda_A=lambda_total_90,
        p_sub=p_sub,
        t_sub=avg_sub_time,
        position=position_str,
    )
    fair_odds_goal_ss = round(1.0 / p_goal_ss, 2) if p_goal_ss > 0 else 99.0

    p_assist_ss = calculate_supersub_prob_assist(
        lambda_A=lambda_assist_90,
        p_sub=p_sub,
        t_sub=avg_sub_time,
        position=position_str,
    )
    fair_odds_assist_ss = round(1.0 / p_assist_ss, 2) if p_assist_ss > 0 else 99.0

    sub_premium_goal   = round(p_goal_ss   - prob_goal,   4)
    sub_premium_assist = round(p_assist_ss - prob_assist, 4)

    return PlayerAllocation(
        player_id=share.player_id,
        player_name=share.player_name,
        team=share.team,
        position=share.position,
        expected_minutes=round(share.expected_minutes, 1),
        is_pen_taker=is_pen_taker,
        npxg_share=round(share.npxg_share, 4),
        xa_share=round(share.xa_share, 4),
        quality_multiplier=round(finishing_mult, 4),
        lambda_open_play=round(lambda_open_play, 4),
        lambda_penalty=round(lambda_penalty, 4),
        lambda_total=round(lambda_total, 4),
        prob_goal=round(prob_goal, 4),
        fair_odds_goal=fair_odds_goal,
        creation_multiplier=round(creation_mult, 4),
        lambda_assist=round(lambda_assist, 4),
        prob_assist=round(prob_assist, 4),
        fair_odds_assist=fair_odds_assist,
        matches_played=share.matches_played,
        has_form_goal=share.form_xg_5 is not None,
        has_form_assist=share.form_assists_5 is not None,
        p_goal_supersub=round(p_goal_ss, 4),
        fair_odds_goal_supersub=fair_odds_goal_ss,
        p_assist_supersub=round(p_assist_ss, 4),
        fair_odds_assist_supersub=fair_odds_assist_ss,
        p_sub=round(p_sub, 4),
        avg_sub_time=round(avg_sub_time, 1),
        sub_premium_goal=sub_premium_goal,
        sub_premium_assist=sub_premium_assist,
    )


# ── DB helpers ────────────────────────────────────────────────────

# Fixture team name → exact bzz_teams.name mapping.
# Values are verified against the production bzz_teams table.
# Only map names that DIFFER from the bzz_teams canonical name —
# if fixture name == bzz name, no entry needed (exact match handles it).
_TEAM_NAME_ALIASES: dict[str, str] = {
    # ── Accents / ponctuation ──────────────────────────────────────
    "Atletico Madrid":             "Atlético Madrid",
    "Atletico de Madrid":          "Atlético Madrid",
    "Bayer Leverkusen":            "Bayer 04 Leverkusen",
    "Borussia Mönchengladbach":    "Borussia M'gladbach",
    "Deportivo Alaves":            "Deportivo Alavés",
    "Rennes":                      "Stade Rennais",
    # ── Premier League ────────────────────────────────────────────
    "Man United":                  "Manchester United",
    "Man Utd":                     "Manchester United",
    "Manchester United FC":        "Manchester United",
    "Man City":                    "Manchester City",
    "Manchester City FC":          "Manchester City",
    "Spurs":                       "Tottenham Hotspur",
    "Tottenham Hotspur FC":        "Tottenham Hotspur",
    "Arsenal FC":                  "Arsenal",
    "Chelsea FC":                  "Chelsea",
    "Liverpool FC":                "Liverpool",
    "Wolverhampton Wanderers":     "Wolverhampton",
    "Wolves":                      "Wolverhampton",
    "Brighton and Hove Albion":    "Brighton & Hove Albion",
    "Nottm Forest":                "Nottingham Forest",
    "Nott'm Forest":               "Nottingham Forest",
    # ── Ligue 1 ───────────────────────────────────────────────────
    "PSG":                         "Paris Saint-Germain",
    "Paris Saint Germain":         "Paris Saint-Germain",
    "Marseille":                   "Olympique de Marseille",
    "Lyon":                        "Olympique Lyonnais",
    "Brest":                       "Stade Brestois",
    "Reims":                       "Stade de Reims",
    "Strasbourg":                  "RC Strasbourg",
    "Lens":                        "RC Lens",
    "Le Havre AC":                 "Le Havre",
    "Havre AC":                    "Le Havre",
    "Montpellier HSC":             "Montpellier",
    # ── Bundesliga ────────────────────────────────────────────────
    "Bayern Munich":               "Bayern München",
    "FC Bayern Munich":            "FC Bayern München",
    "Dortmund":                    "Borussia Dortmund",
    "BVB":                         "Borussia Dortmund",
    "Frankfurt":                   "Eintracht Frankfurt",
    "Freiburg":                    "SC Freiburg",
    "Hoffenheim":                  "TSG Hoffenheim",
    "Augsburg":                    "FC Augsburg",
    "Wolfsburg":                   "VfL Wolfsburg",
    "VfL Bochum":                  "VfL Bochum 1848",
    "Bochum":                      "VfL Bochum 1848",
    "Stuttgart":                   "VfB Stuttgart",
    "Werder Bremen":               "SV Werder Bremen",
    "Bremen":                      "SV Werder Bremen",
    "Leipzig":                     "RB Leipzig",
    "Kiel":                        "Holstein Kiel",
    "Heidenheim":                  "1. FC Heidenheim",
    "FC Heidenheim":               "1. FC Heidenheim",
    "St. Pauli":                   "FC St. Pauli",
    # ── Serie A ───────────────────────────────────────────────────
    "Internazionale":              "Inter",
    "Inter Milan":                 "Inter",
    "AC Milan":                    "Milan",
    "SS Lazio":                    "Lazio",
    "AS Roma":                     "Roma",
    "SSC Napoli":                  "Napoli",
    "Juventus FC":                 "Juventus",
    "FC Bologna":                  "Bologna",
    "ACF Fiorentina":              "Fiorentina",
    "Atalanta BC":                 "Atalanta",
    "Torino FC":                   "Torino",
    "Genoa CFC":                   "Genoa",
    "Empoli FC":                   "Empoli",
    "Cagliari Calcio":             "Cagliari",
    "Udinese Calcio":              "Udinese",
    "Venezia FC":                  "Venezia",
    "US Lecce":                    "Lecce",
    "US Sassuolo":                 "Sassuolo",
    "Como 1907":                   "Como",
    # ── La Liga ───────────────────────────────────────────────────
    "Real Madrid CF":              "Real Madrid",
    "Athletic Bilbao":             "Athletic Club",
    "Betis":                       "Real Betis",
    "Villarreal CF":               "Villarreal",
    "Sevilla FC":                  "Sevilla",
    "Valencia CF":                 "Valencia",
    "Celta de Vigo":               "Celta Vigo",
    "Celta":                       "Celta Vigo",
    "Rayo":                        "Rayo Vallecano",
    "UD Las Palmas":               "Las Palmas",
    "RCD Espanyol":                "Espanyol",
    "RCD Mallorca":                "Mallorca",
    "CD Leganes":                  "Leganés",
    "Girona":                      "Girona FC",
    "Getafe CF":                   "Getafe",
    "CA Osasuna":                  "Osasuna",
    "Alaves":                      "Deportivo Alavés",
}


async def _load_team_players(
    db: AsyncSession,
    team: str,
    league: str | None = None,
    bzz_team_id: int | None = None,
) -> list[dict[str, Any]]:
    """Load player season stats from bzz_player_season_stats for a team.

    When bzz_team_id is provided (preferred path), filters players by
    current_team_api_id OR loan_team_api_id — no string matching needed.

    Fallback (bzz_team_id=None): resolves team name via alias map + fuzzy
    match, then filters by COALESCE(loan_team_name, current_team_name).
    """
    from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat, BzzTeam

    # --- Build the player filter ---
    if bzz_team_id is not None:
        # COALESCE(loan_team_api_id, current_team_api_id): a player on loan belongs
        # to their loan club, not their parent club — using OR would double-count them.
        team_id_filter = func.coalesce(
            BzzPlayer.loan_team_api_id, BzzPlayer.current_team_api_id
        ) == bzz_team_id
    else:
        # Fallback: resolve team name → bzz_teams row → name filter
        resolved = _TEAM_NAME_ALIASES.get(team, team)

        # Step 1+2: alias-resolved exact match
        team_res = await db.execute(
            select(BzzTeam)
            .where(func.lower(BzzTeam.name) == func.lower(resolved))
            .limit(1)
        )
        bzz_team = team_res.scalar_one_or_none()

        # Step 3: bzz name contains fixture name (e.g. "FC Bayern München" ⊇ "Bayern München")
        if bzz_team is None:
            team_res = await db.execute(
                select(BzzTeam)
                .where(func.lower(BzzTeam.name).contains(func.lower(resolved)))
                .order_by(func.length(BzzTeam.name))
                .limit(1)
            )
            bzz_team = team_res.scalar_one_or_none()

        # Step 4: fixture name contains bzz name (e.g. "Wolverhampton Wanderers" ⊇ "Wolverhampton")
        if bzz_team is None:
            team_res = await db.execute(
                select(BzzTeam)
                .where(literal(resolved.lower()).contains(func.lower(BzzTeam.name)))
                .order_by(func.length(BzzTeam.name).desc())  # prefer longest bzz name (more specific)
                .limit(1)
            )
            bzz_team = team_res.scalar_one_or_none()

        if bzz_team is None:
            logger.warning("_load_team_players: no bzz team found for %r", team)
            return []

        # Filter by effective team name — COALESCE(loan_team_name, current_team_name) so that
        # players on loan appear under their loan club (not their parent club) in pricing,
        # consistent with how the players API and teams API work (migration v027+).
        _eff_team = func.coalesce(BzzPlayer.loan_team_name, BzzPlayer.current_team_name)
        team_id_filter = func.lower(_eff_team) == func.lower(bzz_team.name)

    # Query season stats joined to player — pick the most recent season per player
    latest_subq = (
        select(
            BzzPlayerSeasonStat.player_api_id,
            func.max(BzzPlayerSeasonStat.season).label("max_season"),
        )
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .where(team_id_filter)
        .group_by(BzzPlayerSeasonStat.player_api_id)
        .subquery()
    )

    stats_q = (
        select(BzzPlayerSeasonStat, BzzPlayer)
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .join(
            latest_subq,
            (BzzPlayerSeasonStat.player_api_id == latest_subq.c.player_api_id)
            & (BzzPlayerSeasonStat.season == latest_subq.c.max_season),
        )
        .where(team_id_filter)
        # Ensure the row with the most matches is seen first so seen_ids dedup
        # keeps the full-season row rather than a sparse migration artifact.
        .order_by(
            BzzPlayerSeasonStat.player_api_id,
            BzzPlayerSeasonStat.matches_played.desc().nullslast(),
        )
    )

    res = await db.execute(stats_q)

    players = []
    seen_ids: set[int] = set()
    seen_internal_ids: set[int] = set()
    seen_names: set[str] = set()
    for row in res.all():
        stat, player = row[0], row[1]
        name = player.name
        name_key = _name_key(name)
        internal_id = player.internal_id
        if player.api_id in seen_ids:
            continue
        if internal_id is not None and internal_id in seen_internal_ids:
            continue
        if name_key in seen_names:
            continue
        seen_ids.add(player.api_id)
        if internal_id is not None:
            seen_internal_ids.add(internal_id)
        seen_names.add(name_key)

        # Map bzz position (G/D/M/F) to pricing engine format (GK/DF/MF/FW)
        raw_pos = _bzz_pos_to_raw(player.position)
        position = _norm_pos(raw_pos)
        if position == "GK":
            continue

        xg_total = stat.expected_goals or 0.0
        goals_total = stat.goals or 0
        finishing_delta = goals_total - xg_total

        players.append({
            "player_id": player.api_id,
            "player_name": name,
            "name": name,
            "position": position,
            "matches_played": stat.matches_played or 0,
            "minutes_played": stat.minutes_played or 0,
            # Shared numeric fields (mapped to existing compute_player_shares keys)
            "goals": goals_total,
            "xg": xg_total,
            "npxg": xg_total,  # no penalty split in bzz — use total xG
            "xa": stat.expected_assists or 0.0,
            "npxg_per_90": stat.xg_per_90 or 0.0,
            "xa_per_90": stat.xa_per_90 or 0.0,
            "xgchain_per_90": 0.0,  # not available in bzz
            # Bzzoiro-specific quality/creation fields
            "xg_per_90": stat.xg_per_90 or 0.0,
            "shot_accuracy": stat.shot_accuracy or 0.0,
            "xg_per_shot": stat.xg_per_shot or 0.0,
            "avg_rating":      stat.avg_rating or 0.0,          # brut (0-10)
            "cross_accuracy":  stat.cross_accuracy or 0.0,
            "xa_total":        stat.expected_assists or 0.0,
            "assists_total":   stat.goal_assist or 0,
            "form_assists_5":  stat.form_assists_5,              # peut être None
            "npxg_total":      xg_total,
            "goals_total":     goals_total,
            "key_pass_per_90": stat.key_pass_per_90 or 0.0,
            "accurate_cross_per_90": stat.accurate_cross_per_90 or 0.0,
            "form_xg_5": stat.form_xg_5 if stat.form_xg_5 else None,
            "finishing_delta": finishing_delta,
            # Sofascore fields — not available from bzz; default to 0
            "shots_on_target_per_90": stat.shots_on_target_per_90 or 0.0,
            "touches_attack_pen_area_per_90": 0.0,
            "bcc_per_90": 0.0,
            "accurate_crosses_per_90": stat.accurate_cross_per_90 or 0.0,
            "through_balls_per_90": 0.0,
            # Flag to indicate bzz-sourced stats (triggers new multiplier formulas)
            "has_bzz_stats": True,
        })

    # ── Fallback: add roster players with no season stats ─────────
    # Players in bzz_players for this team but without BzzPlayerSeasonStat
    # rows are invisible to the pricing engine. Load all roster players
    # and append missing ones with zero stats — positional priors handle them.
    all_roster_result = await db.execute(
        select(BzzPlayer)
        .where(team_id_filter)
    )
    for player in all_roster_result.scalars().all():
        if player.api_id in seen_ids:
            continue
        internal_id = player.internal_id
        if internal_id is not None and internal_id in seen_internal_ids:
            continue
        name = player.name
        name_key = _name_key(name)
        if name_key in seen_names:
            continue

        raw_pos = _bzz_pos_to_raw(player.position)
        position = _norm_pos(raw_pos)
        if position == "GK":
            continue

        seen_ids.add(player.api_id)
        if internal_id is not None:
            seen_internal_ids.add(internal_id)
        seen_names.add(name_key)
        players.append({
            "player_id": player.api_id,
            "player_name": name,
            "name": name,
            "position": position,
            "matches_played": 0,
            "minutes_played": 0,
            "goals": 0,
            "xg": 0.0,
            "npxg": 0.0,
            "xa": 0.0,
            "npxg_per_90": 0.0,
            "xa_per_90": 0.0,
            "xgchain_per_90": 0.0,
            "xg_per_90": 0.0,
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 0.0,
            "cross_accuracy": 0.0,
            "xa_total": 0.0,
            "assists_total": 0,
            "npxg_total": 0.0,
            "goals_total": 0,
            "key_pass_per_90": 0.0,
            "accurate_cross_per_90": 0.0,
            "form_xg_5": None,
            "form_assists_5": None,
            "finishing_delta": 0.0,
            "shots_on_target_per_90": 0.0,
            "touches_attack_pen_area_per_90": 0.0,
            "bcc_per_90": 0.0,
            "accurate_crosses_per_90": 0.0,
            "through_balls_per_90": 0.0,
            "has_bzz_stats": False,
        })
    # ── End fallback ──────────────────────────────────────────────

    return players


async def _load_national_team_players(
    db: AsyncSession,
    national_team_api_id: int,
) -> list[dict[str, Any]]:
    """Load player season stats for a national team.

    Filters bzz_players by national_team_api_id (direct integer match —
    no fuzzy name resolution needed). Return structure is identical to
    _load_team_players so all downstream callers work unchanged.
    """
    from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat

    nat_filter = BzzPlayer.national_team_api_id == national_team_api_id

    # Most-recent season per player (same subquery pattern as _load_team_players)
    latest_subq = (
        select(
            BzzPlayerSeasonStat.player_api_id,
            func.max(BzzPlayerSeasonStat.season).label("max_season"),
        )
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .where(nat_filter)
        .group_by(BzzPlayerSeasonStat.player_api_id)
        .subquery()
    )

    stats_q = (
        select(BzzPlayerSeasonStat, BzzPlayer)
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .join(
            latest_subq,
            (BzzPlayerSeasonStat.player_api_id == latest_subq.c.player_api_id)
            & (BzzPlayerSeasonStat.season == latest_subq.c.max_season),
        )
        .where(nat_filter)
        .order_by(
            BzzPlayerSeasonStat.player_api_id,
            BzzPlayerSeasonStat.matches_played.desc().nullslast(),
        )
    )

    res = await db.execute(stats_q)

    players: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    seen_internal_ids: set[int] = set()
    seen_names: set[str] = set()

    for row in res.all():
        stat, player = row[0], row[1]
        name = player.name
        name_key = _name_key(name)
        internal_id = player.internal_id
        if player.api_id in seen_ids:
            continue
        if internal_id is not None and internal_id in seen_internal_ids:
            continue
        if name_key in seen_names:
            continue
        seen_ids.add(player.api_id)
        if internal_id is not None:
            seen_internal_ids.add(internal_id)
        seen_names.add(name_key)

        raw_pos = _bzz_pos_to_raw(player.position)
        position = _norm_pos(raw_pos)
        if position == "GK":
            continue

        xg_total = stat.expected_goals or 0.0
        goals_total = stat.goals or 0
        finishing_delta = goals_total - xg_total

        players.append({
            "player_id": player.api_id,
            "player_name": name,
            "name": name,
            "position": position,
            "matches_played": stat.matches_played or 0,
            "minutes_played": stat.minutes_played or 0,
            # Shared numeric fields (mapped to existing compute_player_shares keys)
            "goals": goals_total,
            "xg": xg_total,
            "npxg": xg_total,  # no penalty split in bzz — use total xG
            "xa": stat.expected_assists or 0.0,
            "npxg_per_90": stat.xg_per_90 or 0.0,
            "xa_per_90": stat.xa_per_90 or 0.0,
            "xgchain_per_90": 0.0,  # not available in bzz
            # Bzzoiro-specific quality/creation fields
            "xg_per_90": stat.xg_per_90 or 0.0,
            "shot_accuracy": stat.shot_accuracy or 0.0,
            "xg_per_shot": stat.xg_per_shot or 0.0,
            "avg_rating":      stat.avg_rating or 0.0,          # brut (0-10)
            "cross_accuracy":  stat.cross_accuracy or 0.0,
            "xa_total":        stat.expected_assists or 0.0,
            "assists_total":   stat.goal_assist or 0,
            "form_assists_5":  stat.form_assists_5,              # peut être None
            "npxg_total":      xg_total,
            "goals_total":     goals_total,
            "key_pass_per_90": stat.key_pass_per_90 or 0.0,
            "accurate_cross_per_90": stat.accurate_cross_per_90 or 0.0,
            "form_xg_5": stat.form_xg_5 if stat.form_xg_5 else None,
            "finishing_delta": finishing_delta,
            # Sofascore fields — not available from bzz; default to 0
            "shots_on_target_per_90": stat.shots_on_target_per_90 or 0.0,
            "touches_attack_pen_area_per_90": 0.0,
            "bcc_per_90": 0.0,
            "accurate_crosses_per_90": stat.accurate_cross_per_90 or 0.0,
            "through_balls_per_90": 0.0,
            # Flag to indicate bzz-sourced stats (triggers new multiplier formulas)
            "has_bzz_stats": True,
        })

    # ── Fallback: add roster players with no season stats ─────────
    # Players in bzz_players for this national team but without BzzPlayerSeasonStat
    # rows are invisible to the pricing engine. Load all roster players
    # and append missing ones with zero stats — positional priors handle them.
    all_roster_result = await db.execute(
        select(BzzPlayer).where(nat_filter)
    )
    for player in all_roster_result.scalars().all():
        if player.api_id in seen_ids:
            continue
        internal_id = player.internal_id
        if internal_id is not None and internal_id in seen_internal_ids:
            continue
        name = player.name
        name_key = _name_key(name)
        if name_key in seen_names:
            continue

        raw_pos = _bzz_pos_to_raw(player.position)
        position = _norm_pos(raw_pos)
        if position == "GK":
            continue

        seen_ids.add(player.api_id)
        if internal_id is not None:
            seen_internal_ids.add(internal_id)
        seen_names.add(name_key)
        players.append({
            "player_id": player.api_id,
            "player_name": name,
            "name": name,
            "position": position,
            "matches_played": 0,
            "minutes_played": 0,
            "goals": 0,
            "xg": 0.0,
            "npxg": 0.0,
            "xa": 0.0,
            "npxg_per_90": 0.0,
            "xa_per_90": 0.0,
            "xgchain_per_90": 0.0,
            "xg_per_90": 0.0,
            "shot_accuracy": 0.0,
            "xg_per_shot": 0.0,
            "avg_rating": 0.0,
            "cross_accuracy": 0.0,
            "xa_total": 0.0,
            "assists_total": 0,
            "npxg_total": 0.0,
            "goals_total": 0,
            "key_pass_per_90": 0.0,
            "accurate_cross_per_90": 0.0,
            "form_xg_5": None,
            "form_assists_5": None,
            "finishing_delta": 0.0,
            "shots_on_target_per_90": 0.0,
            "touches_attack_pen_area_per_90": 0.0,
            "bcc_per_90": 0.0,
            "accurate_crosses_per_90": 0.0,
            "through_balls_per_90": 0.0,
            "has_bzz_stats": False,
        })
    # ── End fallback ──────────────────────────────────────────────

    return players


# ── Lineup pricing (optional redistribution) ─────────────────────

def compute_lineup_allocation(
    players: list[dict[str, Any]],
    starter_names: list[str],
    team: str,
    match_xg: float,
) -> list[PlayerAllocation]:
    """Compute redistributed allocations for a specific set of starters.

    The team xG is split only among the named starters → each player gets a
    larger share than in the full-squad calculation, giving a more accurate
    probability when the lineup is known.

    Returns [] if fewer than 5 starters matched in the DB (name mismatch /
    missing data safety fallback).
    """
    norm = {n.strip().lower() for n in starter_names}
    starters = [p for p in players if p["player_name"].strip().lower() in norm]
    if len(starters) < 5:
        return []

    shares = compute_player_shares(starters, team, lambda_team=match_xg)
    pen_id = detect_penalty_taker(starters)
    for s in shares:
        s.is_pen_taker = s.player_id == pen_id

    budget_assists = match_xg * ASSIST_GOAL_RATE
    return sorted(
        [allocate_player(s, match_xg, s.is_pen_taker, budget_assists) for s in shares],
        key=lambda a: a.prob_goal,
        reverse=True,
    )


# ── Orchestration ─────────────────────────────────────────────────

async def load_match_pricing(
    db: AsyncSession,
    fixture: Any,
    home_xg_override: float | None = None,
    away_xg_override: float | None = None,
    home_pen_taker_override: int | None = None,
    away_pen_taker_override: int | None = None,
    home_starters: list[str] | None = None,
    away_starters: list[str] | None = None,
    home_corners_per_match: float | None = None,
    away_corners_per_match: float | None = None,
) -> MatchPricingResult | None:
    """Full Top-Down pricing pipeline for one fixture (Model C).

    home_starters / away_starters: optional list of player names. When
    provided, the response also contains home_lineup_players /
    away_lineup_players with xG redistributed among those starters only.
    The main home_players / away_players always contains the full squad.
    """
    home_team = fixture.home_team
    away_team = fixture.away_team
    market_result: MarketXgResult | None = None

    if home_xg_override is not None and away_xg_override is not None:
        home_match_xg = home_xg_override
        away_match_xg = away_xg_override
        xg_source = "override"
    else:
        _svc = MarketXgService()
        market_result = await _svc.compute(fixture.id, db)
        if market_result is None:
            return None
        xg_source = market_result.xg_source

        home_match_xg = home_xg_override if home_xg_override is not None else market_result.xg_home
        away_match_xg = away_xg_override if away_xg_override is not None else market_result.xg_away

        if home_xg_override is not None or away_xg_override is not None:
            xg_source = "override"

    home_bzz_team_id = getattr(fixture, "home_bzz_team_id", None)
    away_bzz_team_id = getattr(fixture, "away_bzz_team_id", None)

    if fixture.league in INTERNATIONAL_LEAGUES:
        from app.models.bzzoiro import BzzEvent
        bzz_api_id_str = (fixture.external_id or "").removeprefix("bzz_")
        bzz_event = None
        if bzz_api_id_str.isdigit():
            ev_res = await db.execute(
                select(BzzEvent).where(BzzEvent.api_id == int(bzz_api_id_str))
            )
            bzz_event = ev_res.scalar_one_or_none()

        if bzz_event is not None and bzz_event.home_team_api_id and bzz_event.away_team_api_id:
            home_players_db = await _load_national_team_players(
                db, national_team_api_id=bzz_event.home_team_api_id
            )
            away_players_db = await _load_national_team_players(
                db, national_team_api_id=bzz_event.away_team_api_id
            )
        else:
            logger.warning(
                "load_match_pricing: international fixture %s (league=%r, external_id=%r) "
                "— BzzEvent not found or missing team IDs, falling back to _load_team_players",
                getattr(fixture, "id", "?"), fixture.league, fixture.external_id,
            )
            home_players_db = await _load_team_players(db, home_team, bzz_team_id=home_bzz_team_id)
            away_players_db = await _load_team_players(db, away_team, bzz_team_id=away_bzz_team_id)
    else:
        home_players_db = await _load_team_players(db, home_team, bzz_team_id=home_bzz_team_id)
        away_players_db = await _load_team_players(db, away_team, bzz_team_id=away_bzz_team_id)

    if not home_players_db:
        logger.warning(
            "load_match_pricing: 0 players found for home team %r (fixture %s) — "
            "check bzz_players/bzz_player_season_stats and loan_team_name values",
            home_team, getattr(fixture, "id", "?"),
        )
    if not away_players_db:
        logger.warning(
            "load_match_pricing: 0 players found for away team %r (fixture %s) — "
            "check bzz_players/bzz_player_season_stats and loan_team_name values",
            away_team, getattr(fixture, "id", "?"),
        )

    home_shares = compute_player_shares(home_players_db, home_team, lambda_team=home_match_xg)
    away_shares = compute_player_shares(away_players_db, away_team, lambda_team=away_match_xg)

    for w in assess_inputs(home_players_db, home_team):
        logger.warning("assess_inputs [%s]: %s — %s", home_team, w.code, w.detail)
    for w in assess_inputs(away_players_db, away_team):
        logger.warning("assess_inputs [%s]: %s — %s", away_team, w.code, w.detail)

    home_pen_id = home_pen_taker_override or detect_penalty_taker(home_players_db)
    away_pen_id = away_pen_taker_override or detect_penalty_taker(away_players_db)
    for s in home_shares:
        s.is_pen_taker = s.player_id == home_pen_id
    for s in away_shares:
        s.is_pen_taker = s.player_id == away_pen_id

    budget_assists_home = home_match_xg * ASSIST_GOAL_RATE
    budget_assists_away = away_match_xg * ASSIST_GOAL_RATE

    from app.services.sub_stats import get_player_sub_stats

    _home_allocs: list[PlayerAllocation] = []
    for s in home_shares:
        sub = await get_player_sub_stats(
            player_name=s.player_name,
            position=s.position or "MF",
            db=db,
            n_matches=20,
        )
        _home_allocs.append(
            allocate_player(
                s, home_match_xg, s.is_pen_taker, budget_assists_home,
                p_sub=sub.p_sub, avg_sub_time=sub.avg_sub_time,
                team_corners_per_match=home_corners_per_match,
            )
        )
    home_allocs = sorted(_home_allocs, key=lambda a: a.prob_goal, reverse=True)

    _away_allocs: list[PlayerAllocation] = []
    for s in away_shares:
        sub = await get_player_sub_stats(
            player_name=s.player_name,
            position=s.position or "MF",
            db=db,
            n_matches=20,
        )
        _away_allocs.append(
            allocate_player(
                s, away_match_xg, s.is_pen_taker, budget_assists_away,
                p_sub=sub.p_sub, avg_sub_time=sub.avg_sub_time,
                team_corners_per_match=away_corners_per_match,
            )
        )
    away_allocs = sorted(_away_allocs, key=lambda a: a.prob_goal, reverse=True)

    home_lineup = (
        compute_lineup_allocation(home_players_db, home_starters, home_team, home_match_xg)
        if home_starters else None
    )
    away_lineup = (
        compute_lineup_allocation(away_players_db, away_starters, away_team, away_match_xg)
        if away_starters else None
    )

    p00 = round(math.exp(-(home_match_xg + away_match_xg)), 4)

    return MatchPricingResult(
        fixture_id=fixture.id,
        home_team=home_team,
        away_team=away_team,
        home_match_xg=round(home_match_xg, 3),
        away_match_xg=round(away_match_xg, 3),
        xg_source=xg_source,
        home_players=home_allocs,
        away_players=away_allocs,
        home_lineup_players=home_lineup or None,
        away_lineup_players=away_lineup or None,
        last_scraped_at=market_result.last_snapshot_at if market_result is not None else None,
        p00=p00,
    )
