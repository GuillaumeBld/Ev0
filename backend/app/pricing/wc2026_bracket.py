"""WC2026 bracket simulation: ELO engine + group standings + Monte Carlo."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

WC_LEAGUE_API_ID = 27
N_SIM = 50_000
_K = 30.0
_BASE_ELO = 1500.0

# WC2026 round_number scheme (from bzz_events)
_GROUP_ROUNDS = frozenset({1, 2, 3})
_ROUND_R32 = 6
_ROUND_R16 = 5
_ROUND_QF  = 27
_ROUND_SF  = 28
_ROUND_FINAL = 29

STAGES = ["r32", "r16", "qf", "sf", "finalist", "winner"]


# ── ELO Engine ────────────────────────────────────────────────────────────────

def _elo_from_team_bm() -> dict[str, float]:
    """Initialise ELO ratings from TEAM_BM using a log10 scale anchored to the geometric mean."""
    from app.ingestion.wc2026.team_bm import TEAM_BM
    geo_mean = math.exp(
        sum(math.log(bm) for bm in TEAM_BM.values()) / len(TEAM_BM)
    )
    return {
        nation: round(_BASE_ELO + 400.0 * math.log10(bm / geo_mean), 1)
        for nation, bm in TEAM_BM.items()
    }


def _update_elo(
    elo: dict[str, float],
    team_a: str,
    team_b: str,
    score_a: int,
    score_b: int,
) -> None:
    """Update ELO ratings in-place after a match (K=30, conservative)."""
    ea = 1.0 / (1.0 + 10.0 ** ((elo[team_b] - elo[team_a]) / 400.0))
    result_a = 1.0 if score_a > score_b else (0.5 if score_a == score_b else 0.0)
    delta = _K * (result_a - ea)
    elo[team_a] += delta
    elo[team_b] -= delta


def _match_proba_group(elo_a: float, elo_b: float) -> tuple[float, float, float]:
    """Return (p_win_a, p_draw, p_win_b) for a group-stage match."""
    ea = 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))
    p_draw = 0.28 * (1.0 - abs(2.0 * ea - 1.0))
    p_win_a = ea * (1.0 - p_draw)
    p_win_b = (1.0 - ea) * (1.0 - p_draw)
    return p_win_a, p_draw, p_win_b


def _match_proba_ko(elo_a: float, elo_b: float) -> float:
    """Return P(team_a advances) in a knockout match (no draw)."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


# ── Group Stage ────────────────────────────────────────────────────────────────

def _build_groups(events: list[dict]) -> dict[str, list[str]]:
    """Build {group_id: [team1..4]} using Union-Find on round 1-3 matchups."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            if ra > rb:
                ra, rb = rb, ra
            parent[rb] = ra

    for ev in events:
        if ev.get("round_number") in _GROUP_ROUNDS:
            home, away = ev["home_team"], ev["away_team"]
            if home not in parent:
                parent[home] = home
            if away not in parent:
                parent[away] = away
            union(home, away)

    buckets: dict[str, list[str]] = {}
    for team in list(parent):
        root = find(team)
        buckets.setdefault(root, []).append(team)
    return buckets


def _rank_group(standing: dict[str, dict]) -> list[str]:
    """Sort teams in a group: pts → gd → gf → elo (all descending)."""
    return sorted(
        standing,
        key=lambda t: (
            standing[t]["pts"],
            standing[t]["gd"],
            standing[t]["gf"],
            standing[t]["elo"],
        ),
        reverse=True,
    )


def _select_best_thirds(thirds: list[dict]) -> list[dict]:
    """Pick best 8 third-placed teams by pts → gd → gf."""
    return sorted(
        thirds,
        key=lambda t: (t["pts"], t["gd"], t["gf"]),
        reverse=True,
    )[:8]


def _find_match(events: list[dict], ta: str, tb: str) -> dict | None:
    """Find a played match between ta and tb (either order)."""
    for ev in events:
        if (ev["home_team"] == ta and ev["away_team"] == tb) or \
           (ev["home_team"] == tb and ev["away_team"] == ta):
            return ev
    return None


def _apply_result(
    standing: dict[str, dict], ta: str, tb: str, ga: int, gb: int
) -> None:
    """Apply a match result to the group standings in-place."""
    standing[ta]["gf"] += ga
    standing[ta]["gd"] += ga - gb
    standing[tb]["gf"] += gb
    standing[tb]["gd"] += gb - ga
    if ga > gb:
        standing[ta]["pts"] += 3
    elif ga == gb:
        standing[ta]["pts"] += 1
        standing[tb]["pts"] += 1
    else:
        standing[tb]["pts"] += 3


def _simulate_group(
    group_teams: list[str],
    events: list[dict],
    elo: dict[str, float],
    rng: np.random.Generator,
) -> dict[str, dict]:
    """Simulate a group: finished matches use real scores; unplayed are sampled.

    ELO is frozen at entry — it reflects all finished matches before this
    simulation call, updated once in compute_wc_advancement, not per-match here.
    """
    standing: dict[str, dict] = {
        t: {"pts": 0, "gd": 0, "gf": 0, "elo": elo.get(t, _BASE_ELO)}
        for t in group_teams
    }
    # Build O(1) lookup for this group's real results
    event_map: dict[frozenset, dict] = {
        frozenset({ev["home_team"], ev["away_team"]}): ev
        for ev in events
        if ev.get("round_number") in _GROUP_ROUNDS
    }

    for i, ta in enumerate(group_teams):
        for tb in group_teams[i + 1:]:
            match = event_map.get(frozenset({ta, tb}))
            if match and match["status"] == "finished":
                sh, sa = match["home_score"], match["away_score"]
                if match["home_team"] == ta:
                    ga, gb = sh, sa
                else:
                    ga, gb = sa, sh
            else:
                pw, pd, pl = _match_proba_group(
                    elo.get(ta, _BASE_ELO), elo.get(tb, _BASE_ELO)
                )
                roll = rng.random()
                if roll < pw:
                    ga, gb = 1, 0
                elif roll < pw + pd:
                    ga, gb = 0, 0
                else:
                    ga, gb = 0, 1

            _apply_result(standing, ta, tb, ga, gb)

    return standing


# ── Bracket Placement ──────────────────────────────────────────────────────────

# WC2026: 12 groups → 4 regions of 3 groups each.
# Each region: 3 winners + 3 runners-up + 2 best thirds = 8 teams → 4 R32 matches.
# 4 regions × 4 matches = 16 R32 matches. ✓
_REGIONS: list[tuple[str, str, str]] = [
    ("A", "B", "C"),   # Region I
    ("D", "E", "F"),   # Region II
    ("G", "H", "I"),   # Region III
    ("J", "K", "L"),   # Region IV
]


def _build_bracket(
    groups: dict[str, list[str]],
    standings: dict[str, dict[str, dict]],
    thirds_teams: list[str],
) -> list[tuple[str, str]]:
    """Build 16 R32 matchup pairs (4 regions × 4 matches each).

    Within each region (groups X, Y, Z) with 2 thirds T1, T2:
      Match 1: W_X vs R_Y   Match 2: W_Y vs T1
      Match 3: W_Z vs R_X   Match 4: R_Z vs T2
    This ensures no same-group teams meet before R16.
    """
    # Resolve group positions to team names
    pos: dict[str, str] = {}
    for gid, teams in groups.items():
        ranked = _rank_group(standings[gid])
        pos[f"W_{gid}"] = ranked[0]
        pos[f"R_{gid}"] = ranked[1]

    # Assign 2 thirds per region in alphabetical order
    pairs: list[tuple[str, str]] = []
    third_idx = 0

    for x, y, z in _REGIONS:
        if f"W_{x}" not in pos or f"W_{y}" not in pos or f"W_{z}" not in pos:
            continue  # skip regions not yet determined

        t1 = thirds_teams[third_idx]     if third_idx < len(thirds_teams) else pos.get(f"R_{y}", "?")
        t2 = thirds_teams[third_idx + 1] if third_idx + 1 < len(thirds_teams) else pos.get(f"R_{z}", "?")
        third_idx += 2

        pairs.append((pos[f"W_{x}"], pos[f"R_{y}"]))
        pairs.append((pos[f"W_{y}"], t1))
        pairs.append((pos[f"W_{z}"], pos[f"R_{x}"]))
        pairs.append((pos[f"R_{z}"], t2))

    return pairs  # 16 pairs


def _simulate_ko_round(
    pairs: list[tuple[str, str]],
    elo: dict[str, float],
    rng: np.random.Generator,
) -> tuple[list[str], list[str]]:
    """Simulate one knockout round. Returns (winners, losers)."""
    winners, losers = [], []
    for ta, tb in pairs:
        p_a = _match_proba_ko(elo.get(ta, _BASE_ELO), elo.get(tb, _BASE_ELO))
        if rng.random() < p_a:
            winners.append(ta)
            losers.append(tb)
        else:
            winners.append(tb)
            losers.append(ta)
    return winners, losers


# ── Monte Carlo ────────────────────────────────────────────────────────────────

def simulate_bracket(
    elo: dict[str, float],
    groups: dict[str, list[str]],
    events: list[dict],
    n_sim: int = N_SIM,
) -> dict[str, dict[str, float]]:
    """Run Monte Carlo simulation of the WC2026 bracket.

    Returns {nation: {stage: probability}} for all nations in elo.
    Stages: r32, r16, qf, sf, finalist, winner.
    """
    rng = np.random.default_rng(42)
    counters: dict[str, dict[str, int]] = {
        n: {s: 0 for s in STAGES} for n in elo
    }

    for _ in range(n_sim):
        # 1. Simulate remaining group matches → standings
        standings: dict[str, dict[str, dict]] = {}
        for gid, teams in groups.items():
            standings[gid] = _simulate_group(teams, events, elo, rng)

        # 2. Collect thirds and pick best 8
        all_thirds: list[dict] = []
        for gid, teams in groups.items():
            ranked = _rank_group(standings[gid])
            if len(ranked) >= 3:
                third = ranked[2]
                st = standings[gid][third]
                all_thirds.append({
                    "team": third,
                    "pts": st["pts"],
                    "gd": st["gd"],
                    "gf": st["gf"],
                })
        best8 = _select_best_thirds(all_thirds)
        thirds_teams = sorted(t["team"] for t in best8)

        # 3. Mark r32 qualifiers
        for gid in groups:
            ranked = _rank_group(standings[gid])
            for t in ranked[:2]:
                if t in counters:
                    counters[t]["r32"] += 1
        for t in thirds_teams:
            if t in counters:
                counters[t]["r32"] += 1

        # 4. Build bracket and simulate knockouts
        pairs_r32 = _build_bracket(groups, standings, thirds_teams)
        winners_r32, _ = _simulate_ko_round(pairs_r32, elo, rng)
        for t in winners_r32:
            if t in counters:
                counters[t]["r16"] += 1

        pairs_r16 = list(zip(winners_r32[::2], winners_r32[1::2]))
        winners_r16, _ = _simulate_ko_round(pairs_r16, elo, rng)
        for t in winners_r16:
            if t in counters:
                counters[t]["qf"] += 1

        pairs_qf = list(zip(winners_r16[::2], winners_r16[1::2]))
        winners_qf, _ = _simulate_ko_round(pairs_qf, elo, rng)
        for t in winners_qf:
            if t in counters:
                counters[t]["sf"] += 1

        pairs_sf = list(zip(winners_qf[::2], winners_qf[1::2]))
        winners_sf, _ = _simulate_ko_round(pairs_sf, elo, rng)
        for t in winners_sf:
            if t in counters:
                counters[t]["finalist"] += 1

        if len(winners_sf) >= 2:
            pairs_final = [(winners_sf[0], winners_sf[1])]
            winners_final, _ = _simulate_ko_round(pairs_final, elo, rng)
            for t in winners_final:
                if t in counters:
                    counters[t]["winner"] += 1

    return {
        nation: {stage: count / n_sim for stage, count in stages.items()}
        for nation, stages in counters.items()
    }


def _e_games_from_probs(probs: dict[str, float]) -> float:
    """E[games] = 3 + p_r32 + p_r16 + p_qf + 2*p_sf + p_finalist — mirrors wc2026_tournament.py formula for calibration consistency."""
    return (
        3.0
        + probs["r32"]
        + probs["r16"]
        + probs["qf"]
        + 2.0 * probs["sf"]
        + probs["finalist"]
    )


# ── Public Entry Point ─────────────────────────────────────────────────────────

async def compute_wc_advancement(session: Any) -> list[dict]:
    """Compute WC2026 team advancement probabilities via ELO + Monte Carlo.

    Reads all WC events from bzz_events, applies ELO updates for finished
    matches, then runs simulate_bracket(). Returns a list of dicts ready for
    DB INSERT into wc2026_team_advancement.
    """
    from sqlalchemy import text

    rows = (await session.execute(text("""
        SELECT
            e.round_number,
            e.status,
            e.home_score,
            e.away_score,
            ht.name AS home_team,
            at.name AS away_team
        FROM bzz_events e
        JOIN bzz_teams ht ON ht.api_id = e.home_team_api_id
        JOIN bzz_teams at ON at.api_id = e.away_team_api_id
        WHERE e.league_api_id = :lid
        ORDER BY e.event_date ASC
    """), {"lid": WC_LEAGUE_API_ID})).mappings().all()

    events = [dict(r) for r in rows]

    # Build reverse map: bzz team name → TEAM_BM canonical key
    from app.ingestion.wc2026.team_bm import TEAM_BM, WC2026_NATION_NAME_ALIASES
    bzz_to_canon: dict[str, str] = {
        bzz_name: canon
        for canon, bzz_name in WC2026_NATION_NAME_ALIASES.items()
    }
    for canon in TEAM_BM:
        bzz_to_canon.setdefault(canon, canon)

    norm_events = []
    for ev in events:
        home = bzz_to_canon.get(ev["home_team"])
        away = bzz_to_canon.get(ev["away_team"])
        if home and away:
            norm_events.append({**ev, "home_team": home, "away_team": away})

    # Initialise ELO and apply all finished matches
    elo = _elo_from_team_bm()
    for ev in norm_events:
        if ev["status"] == "finished" and ev["home_score"] is not None:
            _update_elo(elo, ev["home_team"], ev["away_team"],
                        ev["home_score"], ev["away_score"])

    groups = _build_groups(norm_events)
    if not groups:
        logger.warning("compute_wc_advancement: no group events found — returning empty")
        return []

    logger.info(
        "compute_wc_advancement: %d groups, %d nations, running %d simulations",
        len(groups), len(elo), N_SIM,
    )

    probs = simulate_bracket(elo, groups, norm_events, n_sim=N_SIM)

    now = datetime.now(timezone.utc)
    return [
        {
            "nation":      nation,
            "elo":         round(elo.get(nation, _BASE_ELO), 1),
            "p_r32":       round(p["r32"],      6),
            "p_r16":       round(p["r16"],      6),
            "p_qf":        round(p["qf"],       6),
            "p_sf":        round(p["sf"],       6),
            "p_finalist":  round(p["finalist"], 6),
            "p_winner":    round(p["winner"],   6),
            "e_games":     round(_e_games_from_probs(p), 4),
            "n_sim":       N_SIM,
            "computed_at": now,
        }
        for nation, p in probs.items()
    ]
