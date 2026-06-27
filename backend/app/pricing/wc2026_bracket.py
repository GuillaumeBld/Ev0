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
    """Initialise ELO ratings from TEAM_BM bookmaker budgets.

    Uses a log10 scale anchored to the geometric mean of all BM values so that
    the average ELO across the 48 nations is approximately 1500.
    """
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
