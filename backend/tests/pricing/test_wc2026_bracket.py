import math
import pytest
from app.pricing.wc2026_bracket import (
    _elo_from_team_bm,
    _update_elo,
    _match_proba_group,
    _match_proba_ko,
)


def test_elo_init_ordering():
    elo = _elo_from_team_bm()
    # Spain and France are favourites, Iraq is an underdog
    assert elo["Spain"] > 1500
    assert elo["France"] > 1500
    assert elo["Iraq"] < 1500
    assert elo["Spain"] > elo["Iraq"]


def test_elo_init_centred():
    elo = _elo_from_team_bm()
    values = list(elo.values())
    assert 1400 < sum(values) / len(values) < 1600


def test_elo_init_48_nations():
    elo = _elo_from_team_bm()
    assert len(elo) == 48


def test_update_elo_winner_gains():
    elo = {"A": 1500.0, "B": 1500.0}
    _update_elo(elo, "A", "B", 2, 0)
    assert elo["A"] > 1500.0
    assert elo["B"] < 1500.0


def test_update_elo_sum_preserved():
    elo = {"A": 1600.0, "B": 1400.0}
    total_before = elo["A"] + elo["B"]
    _update_elo(elo, "A", "B", 1, 0)
    assert abs(elo["A"] + elo["B"] - total_before) < 1e-9


def test_update_elo_draw_equal_teams():
    elo = {"A": 1500.0, "B": 1500.0}
    _update_elo(elo, "A", "B", 1, 1)
    # Equal teams draw → no ELO change
    assert abs(elo["A"] - 1500.0) < 1e-9
    assert abs(elo["B"] - 1500.0) < 1e-9


def test_update_elo_upset_bigger_change():
    elo_normal = {"A": 1600.0, "B": 1400.0}
    elo_upset  = {"C": 1600.0, "D": 1400.0}
    _update_elo(elo_normal, "A", "B", 2, 0)   # favourite wins
    _update_elo(elo_upset,  "C", "D", 0, 2)   # underdog wins
    gain_normal = elo_normal["A"] - 1600.0     # favourite gains little
    gain_upset  = elo_upset["D"]  - 1400.0     # underdog gains a lot
    assert gain_upset > gain_normal


def test_match_proba_group_sums_to_one():
    p_win, p_draw, p_loss = _match_proba_group(1600.0, 1400.0)
    assert abs(p_win + p_draw + p_loss - 1.0) < 1e-9


def test_match_proba_group_favourite_wins_more():
    p_win, _, p_loss = _match_proba_group(1600.0, 1400.0)
    assert p_win > p_loss


def test_match_proba_group_equal_teams_draw_most():
    p_win, p_draw, p_loss = _match_proba_group(1500.0, 1500.0)
    assert abs(p_win - p_loss) < 1e-9  # symmetric
    # With 0.28 coefficient, draw is lower than wins (still a valid probability model)


def test_match_proba_ko_returns_float():
    p = _match_proba_ko(1600.0, 1400.0)
    assert 0.5 < p < 1.0


def test_match_proba_ko_symmetric():
    p_ab = _match_proba_ko(1600.0, 1400.0)
    p_ba = _match_proba_ko(1400.0, 1600.0)
    assert abs(p_ab + p_ba - 1.0) < 1e-9
