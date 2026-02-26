"""Top-Down Match-Centric pricing engine.

Three-stage pipeline:
  Stage 1 — Team Match xG: attack_strength × defense_weakness × home_factor × league_avg
  Stage 2 — Player Allocation: team_xG × npxg_share × Bayesian_shrinkage
  Stage 2b — Assist Lambda: team_xG × xa_share × (mins/90)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# ── Constants ─────────────────────────────────────────────────────

HOME_ADVANTAGE = 1.22          # Dixon-Coles home multiplier
SHRINKAGE_N = 30.0             # full weight at 30+ matches
PENS_PER_MATCH = 0.10          # expected penalties per match (league avg)
PEN_CONVERSION = 0.78          # penalty conversion rate

# Bayesian priors: fraction of team's total xG / xA per position
POSITION_NPXG_PRIORS: dict[str, float] = {
    "FW": 0.25, "MF": 0.08, "DF": 0.02, "GK": 0.00,
}
POSITION_XA_PRIORS: dict[str, float] = {
    "FW": 0.10, "MF": 0.15, "DF": 0.03, "GK": 0.00,
}


# ── Position normalisation (inline to avoid circular imports) ─────

def _norm_pos(raw: str | None) -> str | None:
    """Canonical position: FW / MF / DF / GK."""
    if not raw:
        return None
    p = raw.strip().upper()
    if p in ("FW", "MF", "DF", "GK"):
        return p
    if "GK" in p:
        return "GK"
    if p.startswith("F") or "FW" in p:
        return "FW"
    if p.startswith("D") or "DF" in p or "CB" in p:
        return "DF"
    if p.startswith("M") or "MF" in p or "AM" in p:
        return "MF"
    return None


# ── Dataclasses ───────────────────────────────────────────────────

@dataclass
class TeamStats:
    team: str
    attack_xg_per_match: float    # npxG / team_matches from PlayerStats
    defense_xga_per_match: float  # goals_conceded / matches from Fixture
    finishing: float              # goals / xG, clamped [0.7, 1.3]


@dataclass
class PlayerShare:
    player_id: int
    player_name: str
    team: str
    position: str | None
    npxg_share: float       # fraction of team's open-play xG
    xa_share: float         # fraction of team's xA
    expected_minutes: float
    matches_played: int
    is_pen_taker: bool = False


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
    # Goalscorer
    lambda_open_play: float
    lambda_penalty: float
    lambda_total: float
    prob_goal: float
    fair_odds_goal: float
    # Assist
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


# ── Stage 1: Team stats ───────────────────────────────────────────

async def compute_team_stats(db: AsyncSession) -> dict[str, TeamStats]:
    """Compute attack xG/match, defense xGA/match, and finishing per team."""
    from app.models.fixtures import Fixture
    from app.models.players import Player, PlayerStats

    # Attack xG: sum(npxg) / max(matches_played) per team
    # max(matches) ≈ team's total matches (avoids summing across squad)
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
        team = row[0]
        total_npxg = row[1] or 0.0
        team_matches = row[2] or 1
        total_goals = row[3] or 0
        total_xg = row[4] or 0.0
        if team and team_matches > 0:
            teams[team] = {
                "attack_xg_per_match": total_npxg / team_matches,
                "total_goals": total_goals,
                "total_xg": total_xg,
                "def_conceded": 0,
                "def_matches": 0,
            }

    # Defense xGA: goals conceded from finished Fixture rows
    home_def = await db.execute(
        select(
            Fixture.home_team,
            func.sum(Fixture.away_score).label("conceded"),
            func.count(Fixture.id).label("matches"),
        )
        .where(Fixture.status == "finished")
        .where(Fixture.home_score.isnot(None))
        .group_by(Fixture.home_team)
    )
    for row in home_def.all():
        team, conceded, matches = row[0], (row[1] or 0), (row[2] or 1)
        if team in teams:
            teams[team]["def_conceded"] += conceded
            teams[team]["def_matches"] += matches

    away_def = await db.execute(
        select(
            Fixture.away_team,
            func.sum(Fixture.home_score).label("conceded"),
            func.count(Fixture.id).label("matches"),
        )
        .where(Fixture.status == "finished")
        .where(Fixture.away_score.isnot(None))
        .group_by(Fixture.away_team)
    )
    for row in away_def.all():
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
    """Dixon-Coles style match xG estimate.

    team_match_xg = (attack / avg_xg) × (opponent_xga / avg_xga) × avg_xg × home_factor
    """
    attack_ratio = attack_xg / league_avg_xg if league_avg_xg > 0 else 1.0
    def_ratio = (
        opponent_xga / league_avg_xga
        if (league_avg_xga > 0 and opponent_xga > 0)
        else 1.0
    )
    home_factor = HOME_ADVANTAGE if is_home else 1.0
    return max(0.3, min(4.0, attack_ratio * def_ratio * league_avg_xg * home_factor))


# ── Stage 2: Player shares ────────────────────────────────────────

def compute_player_shares(
    players: list[dict[str, Any]],
    team: str,
) -> list[PlayerShare]:
    """Compute npxG/xA shares with Bayesian shrinkage toward position priors."""
    team_npxg = sum(p.get("npxg", 0.0) or 0.0 for p in players) or 1e-9
    team_xa = sum(p.get("xa", 0.0) or 0.0 for p in players) or 1e-9

    shares = []
    for p in players:
        pos = p.get("position")
        matches = p.get("matches_played", 0) or 0
        shrink = min(matches / SHRINKAGE_N, 1.0)

        npxg_prior = POSITION_NPXG_PRIORS.get(pos or "MF", 0.08)
        xa_prior = POSITION_XA_PRIORS.get(pos or "MF", 0.10)

        npxg_actual = (p.get("npxg", 0.0) or 0.0) / team_npxg
        xa_actual = (p.get("xa", 0.0) or 0.0) / team_xa

        npxg_share = shrink * npxg_actual + (1 - shrink) * npxg_prior
        xa_share = shrink * xa_actual + (1 - shrink) * xa_prior

        mins = p.get("minutes_played", 0) or 0
        exp_mins = (mins / matches) if matches > 0 else 75.0
        exp_mins = max(0.0, min(90.0, exp_mins))

        shares.append(PlayerShare(
            player_id=p["player_id"],
            player_name=p["player_name"],
            team=team,
            position=pos,
            npxg_share=npxg_share,
            xa_share=xa_share,
            expected_minutes=exp_mins,
            matches_played=matches,
        ))
    return shares


def detect_penalty_taker(players: list[dict[str, Any]]) -> int | None:
    """Auto-detect penalty taker: player with highest xG − npxG (= penalty xG)."""
    best_id: int | None = None
    best_pen_xg = 0.0
    for p in players:
        pen_xg = (p.get("xg", 0.0) or 0.0) - (p.get("npxg", 0.0) or 0.0)
        if pen_xg > best_pen_xg:
            best_pen_xg = pen_xg
            best_id = p["player_id"]
    return best_id


def allocate_player(
    share: PlayerShare,
    team_match_xg: float,
    is_pen_taker: bool,
    team_pen_ratio: float = PENS_PER_MATCH,
) -> PlayerAllocation:
    """Compute Poisson lambdas and probabilities for one player.

    λ_open_play = team_xG × (1−pen_ratio) × npxg_share × (mins/90)
    λ_penalty   = PEN_CONVERSION × PENS_PER_MATCH × (mins/90)   [pen taker only]
    λ_assist    = team_xG × xa_share × (mins/90)
    """
    mins_ratio = share.expected_minutes / 90.0

    lambda_open_play = team_match_xg * (1 - team_pen_ratio) * share.npxg_share * mins_ratio
    lambda_penalty = PEN_CONVERSION * PENS_PER_MATCH * mins_ratio if is_pen_taker else 0.0
    lambda_total = max(0.001, lambda_open_play + lambda_penalty)
    prob_goal = 1 - math.exp(-lambda_total)
    fair_odds_goal = round(1 / prob_goal, 2) if prob_goal > 0 else 9999.0

    lambda_assist = max(0.001, team_match_xg * share.xa_share * mins_ratio)
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
        lambda_open_play=round(lambda_open_play, 4),
        lambda_penalty=round(lambda_penalty, 4),
        lambda_total=round(lambda_total, 4),
        prob_goal=round(prob_goal, 4),
        fair_odds_goal=fair_odds_goal,
        lambda_assist=round(lambda_assist, 4),
        prob_assist=round(prob_assist, 4),
        fair_odds_assist=fair_odds_assist,
    )


# ── DB helpers ────────────────────────────────────────────────────

async def _load_team_players(db: AsyncSession, team: str) -> list[dict[str, Any]]:
    """Load latest player stats (source=average) for a team."""
    from app.models.players import Player, PlayerStats

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
        .where(Player.team == team)
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
            "npxg": ps.npxg or 0.0,
            "xg": ps.xg or 0.0,
            "xa": ps.xa or 0.0,
            "matches_played": ps.matches_played or 0,
            "minutes_played": ps.minutes_played or 0,
        })
    return players


# ── Orchestration ─────────────────────────────────────────────────

async def load_match_pricing(
    db: AsyncSession,
    fixture: Any,
    home_xg_override: float | None = None,
    away_xg_override: float | None = None,
    home_pen_taker_override: int | None = None,
    away_pen_taker_override: int | None = None,
) -> MatchPricingResult:
    """Full Top-Down pricing pipeline for one fixture.

    Steps:
    1. Compute team stats (attack xG, defense xGA)
    2. Estimate match xG for both teams (Dixon-Coles)
    3. Load squad players for both teams
    4. Compute player shares (Bayesian shrinkage)
    5. Detect / apply penalty takers
    6. Allocate Poisson lambdas per player
    """
    team_stats = await compute_team_stats(db)
    home_team = fixture.home_team
    away_team = fixture.away_team
    home_ts = team_stats.get(home_team)
    away_ts = team_stats.get(away_team)

    # League averages
    all_ts = list(team_stats.values())
    league_avg_xg = (
        sum(ts.attack_xg_per_match for ts in all_ts) / len(all_ts) if all_ts else 1.2
    )
    xga_values = [ts.defense_xga_per_match for ts in all_ts if ts.defense_xga_per_match > 0]
    league_avg_xga = sum(xga_values) / len(xga_values) if xga_values else league_avg_xg

    # Match xG
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

    # Squad players
    home_players_db = await _load_team_players(db, home_team)
    away_players_db = await _load_team_players(db, away_team)

    # Shares
    home_shares = compute_player_shares(home_players_db, home_team)
    away_shares = compute_player_shares(away_players_db, away_team)

    # Penalty takers
    home_pen_id = home_pen_taker_override or detect_penalty_taker(home_players_db)
    away_pen_id = away_pen_taker_override or detect_penalty_taker(away_players_db)
    for s in home_shares:
        s.is_pen_taker = s.player_id == home_pen_id
    for s in away_shares:
        s.is_pen_taker = s.player_id == away_pen_id

    # Allocate, sort by prob_goal descending
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
    )
