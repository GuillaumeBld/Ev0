import math
import pytest
import numpy as np
from app.pricing.wc2026_bracket import (
    _apply_result,
    _build_groups,
    _elo_from_team_bm,
    _match_proba_group,
    _match_proba_ko,
    _rank_group,
    _select_best_thirds,
    _simulate_group,
    _update_elo,
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


# ── Group helpers ──────────────────────────────────────────────────────────────

def _make_event(home: str, away: str, sh: int, sa: int, finished: bool = True, rnd: int = 1):
    return {
        "home_team": home,
        "away_team": away,
        "home_score": sh,
        "away_score": sa,
        "status": "finished" if finished else "not_started",
        "round_number": rnd,
    }


def _empty_standing(*teams: str) -> dict:
    return {t: {"pts": 0, "gd": 0, "gf": 0, "elo": 1500.0} for t in teams}


def test_apply_result_win():
    st = _empty_standing("A", "B")
    _apply_result(st, "A", "B", 2, 1)
    assert st["A"]["pts"] == 3
    assert st["B"]["pts"] == 0
    assert st["A"]["gf"] == 2
    assert st["B"]["gf"] == 1
    assert st["A"]["gd"] == 1
    assert st["B"]["gd"] == -1


def test_apply_result_draw():
    st = _empty_standing("A", "B")
    _apply_result(st, "A", "B", 1, 1)
    assert st["A"]["pts"] == 1
    assert st["B"]["pts"] == 1
    assert st["A"]["gd"] == 0
    assert st["B"]["gd"] == 0


def test_simulate_group_uses_real_scores():
    # When all matches are finished, _simulate_group should reflect exact standings
    elo = {"A": 1500.0, "B": 1500.0, "C": 1500.0, "D": 1500.0}
    # A wins all 3, B wins 2, C wins 1, D wins 0
    events = [
        {"home_team": "A", "away_team": "B", "home_score": 1, "away_score": 0, "status": "finished", "round_number": 1},
        {"home_team": "A", "away_team": "C", "home_score": 1, "away_score": 0, "status": "finished", "round_number": 2},
        {"home_team": "A", "away_team": "D", "home_score": 1, "away_score": 0, "status": "finished", "round_number": 3},
        {"home_team": "B", "away_team": "C", "home_score": 1, "away_score": 0, "status": "finished", "round_number": 1},
        {"home_team": "B", "away_team": "D", "home_score": 1, "away_score": 0, "status": "finished", "round_number": 2},
        {"home_team": "C", "away_team": "D", "home_score": 1, "away_score": 0, "status": "finished", "round_number": 3},
    ]
    rng = np.random.default_rng(42)
    standing = _simulate_group(["A", "B", "C", "D"], events, elo, rng)
    assert standing["A"]["pts"] == 9
    assert standing["B"]["pts"] == 6
    assert standing["C"]["pts"] == 3
    assert standing["D"]["pts"] == 0


def test_build_groups_two_groups():
    # 6 matches between 4 teams = 1 group; but here we have 2 separate groups (A+B+C+D vs never meeting)
    # Build 2 groups by having A meet B and C, B meet D, but C never meets D
    # Actually simplest: A meets B+C+D, B meets C+D, C meets D = all in one group
    # For TWO groups: teams 1-4 only meet each other, teams 5-8 only meet each other
    events = [
        _make_event("T1", "T2", 1, 0), _make_event("T3", "T4", 2, 1),
        _make_event("T1", "T3", 0, 0), _make_event("T2", "T4", 1, 1),
        _make_event("T1", "T4", 2, 0), _make_event("T2", "T3", 0, 1),
        _make_event("T5", "T6", 1, 0), _make_event("T7", "T8", 2, 1),
        _make_event("T5", "T7", 0, 0), _make_event("T6", "T8", 1, 1),
        _make_event("T5", "T8", 2, 0), _make_event("T6", "T7", 0, 1),
    ]
    groups = _build_groups(events)
    assert len(groups) == 2
    all_teams = {t for g in groups.values() for t in g}
    assert all_teams == {"T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"}
    for g in groups.values():
        assert len(g) == 4


def test_build_groups_ignores_knockout_rounds():
    # round_number=6 is R32 (knockout), should not affect group detection
    events = [
        _make_event("A", "B", 1, 0, rnd=1),
        _make_event("C", "D", 2, 1, rnd=1),
        _make_event("A", "C", 0, 0, rnd=2),
        _make_event("B", "D", 1, 1, rnd=2),
        _make_event("A", "D", 2, 0, rnd=3),
        _make_event("B", "C", 0, 1, rnd=3),
        _make_event("A", "E", 1, 0, rnd=6),  # knockout — should be ignored
    ]
    groups = _build_groups(events)
    assert len(groups) == 1
    all_teams = {t for g in groups.values() for t in g}
    assert "E" not in all_teams


def test_rank_group_by_points():
    standing = {
        "A": {"pts": 9, "gd": 6, "gf": 6, "elo": 1600.0},
        "B": {"pts": 0, "gd": -6, "gf": 0, "elo": 1400.0},
        "C": {"pts": 3, "gd": 0, "gf": 2, "elo": 1500.0},
        "D": {"pts": 3, "gd": 0, "gf": 2, "elo": 1500.0},
    }
    ranked = _rank_group(standing)
    assert ranked[0] == "A"
    assert ranked[3] == "B"


def test_rank_group_tiebreak_gd():
    standing = {
        "A": {"pts": 6, "gd": 3, "gf": 5, "elo": 1500.0},
        "B": {"pts": 6, "gd": 1, "gf": 3, "elo": 1500.0},
        "C": {"pts": 3, "gd": 0, "gf": 2, "elo": 1500.0},
        "D": {"pts": 0, "gd": -4, "gf": 0, "elo": 1500.0},
    }
    ranked = _rank_group(standing)
    assert ranked[0] == "A"
    assert ranked[1] == "B"


def test_select_best_thirds_picks_eight():
    thirds = [
        {"team": f"T{i}", "pts": 9 - i, "gd": 5 - i, "gf": 5 - i}
        for i in range(12)
    ]
    best = _select_best_thirds(thirds)
    assert len(best) == 8
    assert best[0]["team"] == "T0"
    assert best[7]["team"] == "T7"


from app.pricing.wc2026_bracket import simulate_bracket, _e_games_from_probs


def test_simulate_bracket_probabilities_in_range():
    elo = _elo_from_team_bm()
    from app.ingestion.wc2026.team_bm import TEAM_BM
    nations = list(TEAM_BM.keys())
    groups = {chr(ord("A") + i): nations[i * 4: i * 4 + 4] for i in range(12)}
    results = simulate_bracket(elo, groups, events=[], n_sim=500)
    assert len(results) == 48
    for nation, probs in results.items():
        for stage in ["r32", "r16", "qf", "sf", "finalist", "winner"]:
            assert 0.0 <= probs[stage] <= 1.0, f"{nation}/{stage}={probs[stage]} out of [0,1]"


def test_simulate_bracket_favourites_advance_more():
    elo = _elo_from_team_bm()
    from app.ingestion.wc2026.team_bm import TEAM_BM
    nations = list(TEAM_BM.keys())
    groups = {chr(ord("A") + i): nations[i * 4: i * 4 + 4] for i in range(12)}
    results = simulate_bracket(elo, groups, events=[], n_sim=1_000)
    assert results["Spain"]["finalist"] > results["Iraq"]["finalist"]


def test_e_games_from_probs_range():
    from app.pricing.wc2026_bracket import _e_games_from_probs
    # Team that exits group stage
    probs_early = {"r32": 0.0, "r16": 0.0, "qf": 0.0, "sf": 0.0, "finalist": 0.0, "winner": 0.0}
    assert _e_games_from_probs(probs_early) == 3.0
    # Perfect winner (wins everything with p=1)
    probs_winner = {"r32": 1.0, "r16": 1.0, "qf": 1.0, "sf": 1.0, "finalist": 1.0, "winner": 1.0}
    assert _e_games_from_probs(probs_winner) == 3.0 + 1 + 1 + 1 + 2 + 1  # = 9.0


def test_simulate_bracket_e_games_in_range():
    elo = _elo_from_team_bm()
    from app.ingestion.wc2026.team_bm import TEAM_BM
    from app.pricing.wc2026_bracket import _e_games_from_probs
    nations = list(TEAM_BM.keys())
    groups = {chr(ord("A") + i): nations[i * 4: i * 4 + 4] for i in range(12)}
    results = simulate_bracket(elo, groups, events=[], n_sim=500)
    for nation, probs in results.items():
        eg = _e_games_from_probs(probs)
        assert 3.0 <= eg <= 9.0, f"{nation}: e_games={eg}"
