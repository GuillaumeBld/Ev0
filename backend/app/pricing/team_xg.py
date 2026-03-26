"""Top-Down Match-Centric pricing engine — Model C.

Three-stage pipeline (unchanged structure, updated player allocation):
  Stage 1 — Team Match xG : attack_strength × defense_weakness × home_factor × league_avg
  Stage 2 — Player Shares : npxg_share / xa_share with Bayesian shrinkage
  Stage 3 — Player Pricing: Model C quality/creation multipliers applied per player

Changes from original (FBref → Understat + Sofascore):
  - Goalscorer λ now applies quality_multiplier (SOT + TAP + xGChain) from Model C
  - Assist λ now applies creation_multiplier (BCC + xGChain + Crosses + TB) from Model C
  - _load_team_players fetches new Sofascore fields from PlayerStats
  - conversion_rate computed from Understat npxG (unchanged logic)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.pricing.assist import calculate_creation_multiplier
from app.pricing.goalscorer import calculate_quality_multiplier

# ── Constants ─────────────────────────────────────────────────────

HOME_ADVANTAGE = 1.22
SHRINKAGE_N = 30.0
PENS_PER_MATCH = 0.10
PEN_CONVERSION = 0.78

POSITION_NPXG_PRIORS: dict[str, float] = {
    "FW": 0.25, "MF": 0.08, "DF": 0.02, "GK": 0.00,
}
POSITION_XA_PRIORS: dict[str, float] = {
    "FW": 0.10, "MF": 0.15, "DF": 0.03, "GK": 0.00,
}

# League-average per-90 values used for quality/creation multiplier normalization.
# Updated from live Sofascore data each season via compute_league_averages().
LEAGUE_AVG_GOALSCORER: dict[str, float] = {
    "sot":     0.60,
    "tap":     2.50,
    "xgchain": 0.35,
}
LEAGUE_AVG_ASSIST: dict[str, float] = {
    "bcc":     0.18,
    "xgchain": 0.35,
    "crosses": 0.80,
    "tb":      0.25,
}


# ── Position normalisation ────────────────────────────────────────

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


@dataclass
class MatchPricingResult:
    fixture_id: int
    home_team: str
    away_team: str
    home_match_xg: float
    away_match_xg: float
    home_players: list[PlayerAllocation] = field(default_factory=list)
    away_players: list[PlayerAllocation] = field(default_factory=list)
    home_lineup_type: str | None = None
    away_lineup_type: str | None = None


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
) -> list[PlayerShare]:
    """Compute npxG/xA shares with Bayesian shrinkage + attach Model C per-90 stats."""
    team_npxg = sum(p.get("npxg", 0.0) or 0.0 for p in players) or 1e-9
    team_xa = sum(p.get("xa", 0.0) or 0.0 for p in players) or 1e-9

    shares = []
    for p in players:
        pos = p.get("position")
        matches = p.get("matches_played", 0) or 0
        shrink = min(matches / SHRINKAGE_N, 1.0)

        npxg_prior = POSITION_NPXG_PRIORS.get(pos or "MF", 0.08)
        xa_prior = POSITION_XA_PRIORS.get(pos or "MF", 0.10)

        npxg_share = (
            shrink * ((p.get("npxg", 0.0) or 0.0) / team_npxg)
            + (1 - shrink) * npxg_prior
        )
        xa_share = (
            shrink * ((p.get("xa", 0.0) or 0.0) / team_xa)
            + (1 - shrink) * xa_prior
        )

        mins = p.get("minutes_played", 0) or 0
        exp_mins = max(0.0, min(90.0, (mins / matches) if matches > 0 else 75.0))

        # Conversion rate: actual goals / npxG over season, clamped [0.5, 2.0]
        npxg = p.get("npxg", 0.0) or 0.0
        goals = p.get("goals", 0) or 0
        conversion_rate = max(0.5, min(2.0, goals / npxg)) if npxg > 0 else 1.0

        shares.append(PlayerShare(
            player_id=p["player_id"],
            player_name=p["player_name"],
            team=team,
            position=pos,
            npxg_share=npxg_share,
            xa_share=xa_share,
            expected_minutes=exp_mins,
            matches_played=matches,
            # Understat per-90
            npxg_per_90=p.get("npxg_per_90", 0.0) or 0.0,
            xa_per_90=p.get("xa_per_90", 0.0) or 0.0,
            xgchain_per_90=p.get("xgchain_per_90", 0.0) or 0.0,
            conversion_rate=conversion_rate,
            # Sofascore per-90
            sot_per_90=p.get("shots_on_target_per_90", 0.0) or 0.0,
            tap_per_90=p.get("touches_attack_pen_area_per_90", 0.0) or 0.0,
            bcc_per_90=p.get("bcc_per_90", 0.0) or 0.0,
            accurate_crosses_per_90=p.get("accurate_crosses_per_90", 0.0) or 0.0,
            through_balls_per_90=p.get("through_balls_per_90", 0.0) or 0.0,
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
    team_pen_ratio: float = PENS_PER_MATCH,
    league_avg_goalscorer: dict[str, float] | None = None,
    league_avg_assist: dict[str, float] | None = None,
) -> PlayerAllocation:
    """
    Compute Poisson lambdas for one player using Model C.

    Goalscorer:
        λ_open = team_xG × (1−pen_ratio) × npxg_share × (mins/90)
                × quality_multiplier(SOT, TAP, xGChain)
                × conversion_rate

    Assist:
        λ_assist = team_xG × xa_share × (mins/90)
                 × creation_multiplier(BCC, xGChain, Crosses, TB)
    """
    mins_ratio = share.expected_minutes / 90.0

    # ── Goalscorer ────────────────────────────────────────────────
    q_mult, _ = calculate_quality_multiplier(
        sot_per_90=share.sot_per_90,
        touches_attack_pen_per_90=share.tap_per_90,
        xgchain_per_90=share.xgchain_per_90,
        league_averages=league_avg_goalscorer or LEAGUE_AVG_GOALSCORER,
    )

    lambda_open_play = (
        team_match_xg
        * (1 - team_pen_ratio)
        * share.npxg_share
        * mins_ratio
        * q_mult
        * share.conversion_rate
    )
    lambda_penalty = PEN_CONVERSION * PENS_PER_MATCH * mins_ratio if is_pen_taker else 0.0
    lambda_total = max(0.001, lambda_open_play + lambda_penalty)
    prob_goal = 1 - math.exp(-lambda_total)
    fair_odds_goal = round(1 / prob_goal, 2) if prob_goal > 0 else 9999.0

    # ── Assist ────────────────────────────────────────────────────
    c_mult, _ = calculate_creation_multiplier(
        bcc_per_90=share.bcc_per_90,
        xgchain_per_90=share.xgchain_per_90,
        accurate_crosses_per_90=share.accurate_crosses_per_90,
        through_balls_per_90=share.through_balls_per_90,
        position=share.position,
        league_averages=league_avg_assist or LEAGUE_AVG_ASSIST,
    )

    lambda_assist = max(0.001, team_match_xg * share.xa_share * mins_ratio * c_mult)
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
        quality_multiplier=round(q_mult, 4),
        lambda_open_play=round(lambda_open_play, 4),
        lambda_penalty=round(lambda_penalty, 4),
        lambda_total=round(lambda_total, 4),
        prob_goal=round(prob_goal, 4),
        fair_odds_goal=fair_odds_goal,
        creation_multiplier=round(c_mult, 4),
        lambda_assist=round(lambda_assist, 4),
        prob_assist=round(prob_assist, 4),
        fair_odds_assist=fair_odds_assist,
    )


# ── DB helpers ────────────────────────────────────────────────────

async def _load_team_players(db: AsyncSession, team: str) -> list[dict[str, Any]]:
    """Load latest player stats (source=average) for a team — includes Model C fields."""
    from app.ingestion.storage import TEAM_NAME_MAP
    from app.models.players import Player, PlayerStats

    candidates = {team}
    for short, canonical in TEAM_NAME_MAP.items():
        if canonical == team:
            candidates.add(short)

    latest_subq = (
        select(PlayerStats.player_id, func.max(PlayerStats.as_of_utc).label("max_date"))
        .where(PlayerStats.source == "average")
        .group_by(PlayerStats.player_id)
        .subquery()
    )

    res = await db.execute(
        select(PlayerStats, Player.name, Player.position)
        .join(Player, Player.id == PlayerStats.player_id)
        .join(
            latest_subq,
            (PlayerStats.player_id == latest_subq.c.player_id)
            & (PlayerStats.as_of_utc == latest_subq.c.max_date),
        )
        .where(Player.team.in_(candidates))
    )

    players = []
    for row in res.all():
        ps, name, raw_pos = row[0], row[1], row[2]
        position = _norm_pos(raw_pos)
        if position == "GK":
            continue
        players.append({
            "player_id": ps.player_id,
            "player_name": name,
            "position": position,
            "matches_played": ps.matches_played or 0,
            "minutes_played": ps.minutes_played or 0,
            # Understat
            "goals": ps.goals or 0,
            "xg": ps.xg or 0.0,
            "npxg": ps.npxg or 0.0,
            "xa": ps.xa or 0.0,
            "npxg_per_90": ps.npxg_per_90 or 0.0,
            "xa_per_90": ps.xa_per_90 or 0.0,
            "xgchain_per_90": ps.xgchain_per_90 or 0.0,
            # Sofascore
            "shots_on_target_per_90": ps.shots_on_target_per_90 or 0.0,
            "touches_attack_pen_area_per_90": ps.touches_attack_pen_area_per_90 or 0.0,
            "bcc_per_90": ps.bcc_per_90 or 0.0,
            "accurate_crosses_per_90": ps.accurate_crosses_per_90 or 0.0,
            "through_balls_per_90": ps.through_balls_per_90 or 0.0,
        })
    return players


# ── Lineup integration ────────────────────────────────────────────

async def _apply_lineup_minutes(
    shares: list[PlayerShare],
    fixture_id: int,
    team: str,
    db: AsyncSession,
) -> str | None:
    """Override expected_minutes from the best available lineup.

    Returns the lineup_type used ("official", "probable_manual", "last_known")
    or None if no lineup is available for this team/fixture.

    Minutes assignment:
      - Starter → 90
      - Substitute → 30
      - Not in lineup → 0
    """
    from app.ingestion.lineup_resolver import resolve_lineup

    resolved = await resolve_lineup(fixture_id, team, db)
    if resolved is None:
        return None

    starters: set[str] = set()
    subs: set[str] = set()
    for p in resolved.players:
        key = p.player_name.strip().lower()
        if p.is_starter:
            starters.add(key)
        else:
            subs.add(key)

    for share in shares:
        key = share.player_name.strip().lower()
        if key in starters:
            share.expected_minutes = 90.0
        elif key in subs:
            share.expected_minutes = 30.0
        else:
            share.expected_minutes = 0.0

    return resolved.lineup_type


# ── Orchestration ─────────────────────────────────────────────────

async def load_match_pricing(
    db: AsyncSession,
    fixture: Any,
    home_xg_override: float | None = None,
    away_xg_override: float | None = None,
    home_pen_taker_override: int | None = None,
    away_pen_taker_override: int | None = None,
) -> MatchPricingResult:
    """Full Top-Down pricing pipeline for one fixture (Model C)."""
    team_stats = await compute_team_stats(db)
    home_team = fixture.home_team
    away_team = fixture.away_team
    home_ts = team_stats.get(home_team)
    away_ts = team_stats.get(away_team)

    all_ts = list(team_stats.values())
    league_avg_xg = (
        sum(ts.attack_xg_per_match for ts in all_ts) / len(all_ts) if all_ts else 1.2
    )
    xga_values = [ts.defense_xga_per_match for ts in all_ts if ts.defense_xga_per_match > 0]
    league_avg_xga = sum(xga_values) / len(xga_values) if xga_values else league_avg_xg

    if home_xg_override is not None:
        home_match_xg = home_xg_override
    elif home_ts:
        home_match_xg = estimate_team_match_xg(
            home_ts.attack_xg_per_match,
            away_ts.defense_xga_per_match if away_ts else league_avg_xga,
            league_avg_xg, league_avg_xga, is_home=True,
        )
    else:
        home_match_xg = league_avg_xg * HOME_ADVANTAGE

    if away_xg_override is not None:
        away_match_xg = away_xg_override
    elif away_ts:
        away_match_xg = estimate_team_match_xg(
            away_ts.attack_xg_per_match,
            home_ts.defense_xga_per_match if home_ts else league_avg_xga,
            league_avg_xg, league_avg_xga, is_home=False,
        )
    else:
        away_match_xg = league_avg_xg

    home_players_db = await _load_team_players(db, home_team)
    away_players_db = await _load_team_players(db, away_team)

    home_shares = compute_player_shares(home_players_db, home_team)
    away_shares = compute_player_shares(away_players_db, away_team)

    # Apply lineup overrides (starters=90min, subs=30min, absent=0min)
    home_lineup_type = await _apply_lineup_minutes(home_shares, fixture.id, home_team, db)
    away_lineup_type = await _apply_lineup_minutes(away_shares, fixture.id, away_team, db)

    home_pen_id = home_pen_taker_override or detect_penalty_taker(home_players_db)
    away_pen_id = away_pen_taker_override or detect_penalty_taker(away_players_db)
    for s in home_shares:
        s.is_pen_taker = s.player_id == home_pen_id
    for s in away_shares:
        s.is_pen_taker = s.player_id == away_pen_id

    home_allocs = sorted(
        [allocate_player(s, home_match_xg, s.is_pen_taker) for s in home_shares],
        key=lambda a: a.prob_goal, reverse=True,
    )
    away_allocs = sorted(
        [allocate_player(s, away_match_xg, s.is_pen_taker) for s in away_shares],
        key=lambda a: a.prob_goal, reverse=True,
    )

    return MatchPricingResult(
        fixture_id=fixture.id,
        home_team=home_team,
        away_team=away_team,
        home_match_xg=round(home_match_xg, 3),
        away_match_xg=round(away_match_xg, 3),
        home_players=home_allocs,
        away_players=away_allocs,
        home_lineup_type=home_lineup_type,
        away_lineup_type=away_lineup_type,
    )
