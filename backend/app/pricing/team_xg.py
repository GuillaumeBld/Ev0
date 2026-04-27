"""Top-Down Match-Centric pricing engine — Model C.

Three-stage pipeline (unchanged structure, updated player allocation):
  Stage 1 — Team Match xG : attack_strength × defense_weakness × home_factor × league_avg
  Stage 2 — Player Shares : form-blended top-down shares (npxg_share / xa_share)
  Stage 3 — Player Pricing: finishing_multiplier + creation_multiplier_v2 applied per player

Changes from original (FBref → Understat + Sofascore):
  - Goalscorer λ now applies calculate_finishing_multiplier (position-normalized)
  - Assist λ now applies calculate_creation_multiplier_v2 (profile+position)
  - compute_player_shares uses form-blend (60% season + 40% form) top-down approach
  - _load_team_players now reads from bzz_player_season_stats (joined to bzz_players)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.pricing.assist import (
    ASSIST_GOAL_RATE,
    calculate_assist_lambda,
    calculate_creation_multiplier_v2,
)
from app.pricing.goalscorer import (
    calculate_finishing_multiplier,
)
from app.services.market_xg import MarketXgResult, MarketXgService

# ── Constants ─────────────────────────────────────────────────────

HOME_ADVANTAGE = 1.22
SHRINKAGE_N = 30.0
PENS_PER_MATCH = 0.10
PEN_CONVERSION = 0.78
CLAMP_MULTIPLIER_MIN = 0.5
CLAMP_MULTIPLIER_MAX = 2.0

FORM_WEIGHTS_BY_POSITION: dict[str, float] = {
    "FW": 0.25, "MF": 0.20, "DF": 0.10,
}
_FORM_WEIGHT_DEFAULT = 0.20

POSITION_NPXG_PRIORS: dict[str, float] = {
    "FW": 0.25, "MF": 0.08, "DF": 0.02, "GK": 0.00,
}
POSITION_XA_PRIORS: dict[str, float] = {
    "FW": 0.10, "MF": 0.15, "DF": 0.03, "GK": 0.00,
}



# ── Position normalisation ────────────────────────────────────────

def _bzz_pos_to_raw(bzz_pos: str | None) -> str | None:
    """Map Bzzoiro single-char position codes to pricing engine format."""
    return {"G": "GK", "D": "DF", "M": "MF", "F": "FW"}.get(bzz_pos or "", bzz_pos)


def _norm_pos(raw: str | None) -> str | None:
    """Canonical position: FW / MF / DF / GK (W and FB kept for assist weighting)."""
    if not raw:
        return None
    p = raw.strip().upper()
    if p in ("FW", "MF", "DF", "GK", "W", "FB", "AM"):
        return p
    if "GK" in p:
        return "GK"
    if p.startswith("F") or "FW" in p:
        return "FW"
    if p in ("LB", "RB", "LWB", "RWB"):
        return "FB"
    if p in ("LW", "RW", "LM", "RM"):
        return "W"
    if p.startswith("D") or "DF" in p or "CB" in p:
        return "DF"
    if p.startswith("M") or "MF" in p or "AM" in p:
        return "MF"
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
    # Model C per-90 stats (Understat)
    npxg_per_90: float = 0.0
    xa_per_90: float = 0.0
    xgchain_per_90: float = 0.0
    conversion_rate: float = 1.0
    # Model C per-90 stats (Sofascore)
    sot_per_90: float = 0.0
    tap_per_90: float = 0.0
    bcc_per_90: float = 0.0
    accurate_crosses_per_90: float = 0.0
    through_balls_per_90: float = 0.0
    # Bzzoiro-specific stats (from bzz_player_season_stats)
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
    # Goalscorer (Model C)
    quality_multiplier: float
    lambda_open_play: float
    lambda_penalty: float
    lambda_total: float
    prob_goal: float
    fair_odds_goal: float
    # Assist (Model C)
    creation_multiplier: float
    lambda_assist: float
    prob_assist: float
    fair_odds_assist: float
    matches_played: int = 0
    has_form_goal: bool = False
    has_form_assist: bool = False


@dataclass
class MatchPricingResult:
    fixture_id: int
    home_team: str
    away_team: str
    home_match_xg: float
    away_match_xg: float
    xg_source: str = "dixon_coles"
    home_players: list[PlayerAllocation] = field(default_factory=list)
    away_players: list[PlayerAllocation] = field(default_factory=list)
    # Optional redistributed pricing when caller supplies a starter list
    home_lineup_players: list[PlayerAllocation] | None = None
    away_lineup_players: list[PlayerAllocation] | None = None


# ── Stage 1: Team stats ───────────────────────────────────────────

async def compute_team_stats(db: AsyncSession) -> dict[str, TeamStats]:
    """Compute attack xG/match, defense xGA/match, and finishing per team."""
    from app.models.fixtures import Fixture
    from app.models.players import Player, PlayerStats

    attack_res = await db.execute(
        select(
            Player.team,
            func.sum(PlayerStats.npxg).label("total_npxg"),
            func.max(PlayerStats.matches_played).label("team_matches"),
            func.sum(PlayerStats.goals).label("total_goals"),
            func.sum(PlayerStats.xg).label("total_xg"),
        )
        .join(Player, Player.id == PlayerStats.player_id)
        .where(Player.team.isnot(None))
        .where(PlayerStats.source == "average")
        .group_by(Player.team)
    )

    teams: dict[str, dict[str, Any]] = {}
    for row in attack_res.all():
        team, total_npxg, team_matches, total_goals, total_xg = (
            row[0], row[1] or 0.0, row[2] or 1, row[3] or 0, row[4] or 0.0
        )
        if team and team_matches > 0:
            teams[team] = {
                "attack_xg_per_match": total_npxg / team_matches,
                "total_goals": total_goals,
                "total_xg": total_xg,
                "def_conceded": 0,
                "def_matches": 0,
            }

    for _side, score_col, concede_col in [
        ("home", "home_team", "away_score"),
        ("away", "away_team", "home_score"),
    ]:
        res = await db.execute(
            select(
                getattr(Fixture, score_col.replace("_score", "_team")
                        if "_score" in score_col else score_col),
                func.sum(getattr(Fixture, concede_col)).label("conceded"),
                func.count(Fixture.id).label("matches"),
            )
            .where(Fixture.status == "finished")
            .where(Fixture.home_score.isnot(None))
            .group_by(getattr(Fixture, score_col.replace("_score", "_team")
                               if "_score" in score_col else score_col))
        )
        for row in res.all():
            team, conceded, matches = row[0], (row[1] or 0), (row[2] or 1)
            if team in teams:
                teams[team]["def_conceded"] += conceded
                teams[team]["def_matches"] += matches

    result: dict[str, TeamStats] = {}
    for team, d in teams.items():
        xga = d["def_conceded"] / d["def_matches"] if d["def_matches"] > 0 else 0.0
        finishing = 1.0
        if d["total_xg"] > 0:
            finishing = max(0.7, min(1.3, d["total_goals"] / d["total_xg"]))
        result[team] = TeamStats(
            team=team,
            attack_xg_per_match=d["attack_xg_per_match"],
            defense_xga_per_match=xga,
            finishing=finishing,
        )
    return result


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
        if form_xg is not None and avg_mins > 0:
            form_rate = form_xg / (5.0 * avg_mins / 90.0)
            blended_xg = (1 - form_w) * xg_per_90 + form_w * form_rate
        else:
            blended_xg = xg_per_90
        goal_weight = blended_xg * mins_ratio

        # Assist weight — blend season xa_per_90 + form_assists_5
        xa_per_90 = p.get("xa_per_90") or 0.0
        form_xa = p.get("form_assists_5")
        if form_xa is not None and avg_mins > 0:
            form_xa_rate = form_xa / (5.0 * avg_mins / 90.0)
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
    """Auto-detect penalty taker: highest xG − npxG (= penalty xG)."""
    best_id: int | None = None
    best_pen_xg = 0.0
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
) -> PlayerAllocation:
    """Compute Poisson lambdas using new top-down formulas."""
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
    lambda_total = max(0.001, min(lambda_open_play + lambda_penalty, 3.0))
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
    lambda_assist = calculate_assist_lambda(share.xa_share, budget_assists, creation_mult, 1.0)
    prob_assist = 1 - math.exp(-lambda_assist)
    fair_odds_assist = round(1 / prob_assist, 2) if prob_assist > 0 else 9999.0

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
) -> list[dict[str, Any]]:
    """Load player season stats from bzz_player_season_stats for a team.

    Lookup order (stops at first hit):
    1. Alias map (_TEAM_NAME_ALIASES) — for accent/abbreviation mismatches.
    2. Exact case-insensitive match.
    3. bzz name CONTAINS fixture name  (e.g. "FC Augsburg" ⊇ "Augsburg").
    4. Fixture name CONTAINS bzz name  (e.g. "Wolverhampton Wanderers" ⊇ "Wolverhampton").

    Falls back to [] if no BzzTeam found for the team name.
    """
    from app.models.bzzoiro import BzzPlayer, BzzPlayerSeasonStat, BzzTeam

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

    # Filter by team name (case-insensitive) — avoids the mismatch between
    # events API team IDs (bzz_teams.api_id) and player profile API team IDs
    # (BzzPlayer.current_team_api_id), which use incompatible ID systems.
    team_name_filter = func.lower(BzzPlayer.current_team_name) == func.lower(bzz_team.name)

    # Query season stats joined to player — pick the most recent season per player
    latest_subq = (
        select(
            BzzPlayerSeasonStat.player_api_id,
            func.max(BzzPlayerSeasonStat.season).label("max_season"),
        )
        .join(BzzPlayer, BzzPlayer.api_id == BzzPlayerSeasonStat.player_api_id)
        .where(team_name_filter)
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
        .where(team_name_filter)
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
    seen_names: set[str] = set()
    for row in res.all():
        stat, player = row[0], row[1]
        name = player.name
        name_key = (name or "").strip().lower()
        if player.api_id in seen_ids or name_key in seen_names:
            continue
        seen_ids.add(player.api_id)
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
        .where(team_name_filter)
    )
    for player in all_roster_result.scalars().all():
        if player.api_id in seen_ids:
            continue
        name = player.name
        name_key = (name or "").strip().lower()
        if name_key in seen_names:
            continue

        raw_pos = _bzz_pos_to_raw(player.position)
        position = _norm_pos(raw_pos)
        if position == "GK":
            continue

        seen_ids.add(player.api_id)
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
) -> MatchPricingResult:
    """Full Top-Down pricing pipeline for one fixture (Model C).

    home_starters / away_starters: optional list of player names. When
    provided, the response also contains home_lineup_players /
    away_lineup_players with xG redistributed among those starters only.
    The main home_players / away_players always contains the full squad.
    """
    home_team = fixture.home_team
    away_team = fixture.away_team

    if home_xg_override is not None and away_xg_override is not None:
        home_match_xg = home_xg_override
        away_match_xg = away_xg_override
        xg_source = "override"
    else:
        market_result: MarketXgResult | None = await MarketXgService().compute(fixture.id, db)
        if market_result is None:
            return None
        xg_source = market_result.xg_source

        home_match_xg = home_xg_override if home_xg_override is not None else market_result.xg_home
        away_match_xg = away_xg_override if away_xg_override is not None else market_result.xg_away

        if home_xg_override is not None or away_xg_override is not None:
            xg_source = "override"

    home_players_db = await _load_team_players(db, home_team)
    away_players_db = await _load_team_players(db, away_team)

    home_shares = compute_player_shares(home_players_db, home_team, lambda_team=home_match_xg)
    away_shares = compute_player_shares(away_players_db, away_team, lambda_team=away_match_xg)

    home_pen_id = home_pen_taker_override or detect_penalty_taker(home_players_db)
    away_pen_id = away_pen_taker_override or detect_penalty_taker(away_players_db)
    for s in home_shares:
        s.is_pen_taker = s.player_id == home_pen_id
    for s in away_shares:
        s.is_pen_taker = s.player_id == away_pen_id

    budget_assists_home = home_match_xg * ASSIST_GOAL_RATE
    budget_assists_away = away_match_xg * ASSIST_GOAL_RATE

    home_allocs = sorted(
        [allocate_player(s, home_match_xg, s.is_pen_taker, budget_assists_home) for s in home_shares],
        key=lambda a: a.prob_goal, reverse=True,
    )
    away_allocs = sorted(
        [allocate_player(s, away_match_xg, s.is_pen_taker, budget_assists_away) for s in away_shares],
        key=lambda a: a.prob_goal, reverse=True,
    )

    home_lineup = (
        compute_lineup_allocation(home_players_db, home_starters, home_team, home_match_xg)
        if home_starters else None
    )
    away_lineup = (
        compute_lineup_allocation(away_players_db, away_starters, away_team, away_match_xg)
        if away_starters else None
    )

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
    )
