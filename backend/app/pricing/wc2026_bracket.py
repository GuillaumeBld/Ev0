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
    """Simulate a group: finished matches use real scores; unplayed are sampled."""
    standing: dict[str, dict] = {
        t: {"pts": 0, "gd": 0, "gf": 0, "elo": elo.get(t, _BASE_ELO)}
        for t in group_teams
    }

    for i, ta in enumerate(group_teams):
        for tb in group_teams[i + 1:]:
            match = _find_match(events, ta, tb)
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
