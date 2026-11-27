"""xG/xA par 90 sur les seuls matchs mesurés (27/11/2026)."""
from types import SimpleNamespace

from app.pricing.team_xg import _player_dict_from_merged
from app.services.atlas_source import merge_season_stats


def _ligne(minutes, minutes_xg, xg, xa, buts, passes, league=1):
    champs = {c: None for c in (
        "starts", "total_shots", "shots_on_target", "key_pass", "total_cross", "accurate_cross",
        "total_pass", "accurate_pass", "total_long_balls", "accurate_long_balls", "duel_won",
        "duel_lost", "aerial_won", "aerial_lost", "total_tackle", "won_tackle", "interception",
        "ball_recovery", "yellow_card", "red_card", "saves", "avg_rating", "form_xg_5",
        "form_rating_5", "form_goals_5", "form_assists_5", "rating_trend", "shot_accuracy",
        "xg_per_shot", "cross_accuracy")}
    champs.update(dict(matches_played=minutes // 90, minutes_played=minutes, minutes_xg=minutes_xg,
                       expected_goals=xg, expected_assists=xa, goals=buts, goal_assist=passes,
                       league_api_id=league, season="2026-2027"))
    return SimpleNamespace(**champs)


def test_svanback_le_xg_est_divise_par_les_matchs_mesures():
    m = merge_season_stats([_ligne(1269, 0, None, None, 4, 4), _ligne(329, 329, 1.59, 0.72, 4, 2, 2)], "2026-2027")
    assert abs(m["xg_per_90"] - 1.59 / 329 * 90) < 1e-9
    assert abs(m["xa_per_90"] - 0.72 / 329 * 90) < 1e-9
    assert m["minutes_played"] == 1598 and m["goals"] == 8


def test_match_mesure_sans_tir_compte_zero():
    m = merge_season_stats([_ligne(900, 900, None, None, 0, 0)], "2026-2027")
    assert m["xg_per_90"] == 0.0


def test_aucun_match_mesure_pas_de_faux_zero_et_vrais_buts_au_calcul():
    m = merge_season_stats([_ligne(900, 0, None, None, 3, 2)], "2026-2027")
    assert m["xg_per_90"] is None
    joueur = SimpleNamespace(api_id=7, name="X", position="F")
    d = _player_dict_from_merged(joueur, m, "2026-2027", [_ligne(900, 0, None, None, 3, 2)])
    assert abs(d["xg_per_90"] - 0.30) < 1e-9 and abs(d["xa_per_90"] - 0.20) < 1e-9


def test_colonne_pas_encore_calculee_ancien_comportement():
    m = merge_season_stats([_ligne(900, None, 0.9, 0.45, 1, 1)], "2026-2027")
    assert abs(m["xg_per_90"] - 0.09) < 1e-9


# ── Équipe entière sans xG mesuré (Parva Liga, Liga 1 roumaine…) ──

from app.pricing.team_xg import RAPPEL_SANS_XG_MATCHS, allocate_player, compute_player_shares


def _sans_xg(pid, pos, buts, passes, minutes=1200):
    return {"player_id": pid, "player_name": f"J{pid}", "position": pos, "minutes_played": minutes,
            "matches_played": minutes // 90, "starts": minutes // 90, "xg_per_90": 90 * buts / minutes,
            "npxg_per_90": 90 * buts / minutes, "xa_per_90": 90 * passes / minutes,
            "goals_total": buts, "assists_total": passes, "rythme_sans_xg": True, "minutes_xg": 0}


def _equipe():
    return ([_sans_xg(1, "GK", 0, 0)] + [_sans_xg(i, "CB", 0, 0) for i in range(2, 6)]
            + [_sans_xg(i, "CM", 1, 2) for i in range(6, 10)] + [_sans_xg(10, "CF_lone", 6, 1), _sans_xg(11, "CF_lone", 3, 1)])


def test_aucun_defenseur_a_1_05_quand_personne_n_a_de_xg():
    """Bug du 24-26/11 : 1,05 buteur pour des défenseurs, ~230 recos à +200 % d'edge."""
    shares = compute_player_shares(_equipe(), "X", 1.3)
    allocs = {s.player_id: allocate_player(s, 1.3, False, 0.91) for s in shares}
    for pid in range(2, 6):
        assert allocs[pid].fair_odds_goal_supersub > 8.0
        assert allocs[pid].fair_odds_goal_supersub < 100.0, "0 but en 13 matchs n'est pas « jamais buteur »"
    assert allocs[10].fair_odds_goal_supersub < allocs[11].fair_odds_goal_supersub < allocs[2].fair_odds_goal_supersub
    total = sum(-__import__("math").log(1 - a.p_goal_supersub) for a in allocs.values())
    assert abs(total - 1.3) < 0.02


def test_rappel_de_15_matchs():
    assert RAPPEL_SANS_XG_MATCHS == 15.0


def _j(pid, pos, xg90, starts):
    return {"player_id": pid, "player_name": f"J{pid}", "position": pos, "minutes_played": 1800 if starts else 300,
            "matches_played": 20, "starts": starts, "xg_per_90": xg90, "npxg_per_90": xg90, "xa_per_90": 0.05}


def test_un_remplacant_efficace_ne_vaut_pas_plus_que_l_equipe():
    """Fluminense : un remplaçant à gros rythme était coté 1,10 (part > 1)."""
    joueurs = ([_j(1, "GK", 0.0, 20)] + [_j(i, "CB", 0.05, 20) for i in range(2, 6)]
               + [_j(i, "CM", 0.10, 20) for i in range(6, 10)] + [_j(10, "CF_lone", 0.30, 20), _j(11, "CF_lone", 0.30, 20)]
               + [_j(12, "CF_lone", 1.20, 2)])
    shares = {s.player_id: s for s in compute_player_shares(joueurs, "X", 1.5)}
    assert shares[12].npxg_share * 1.0 < 1.0
    a = allocate_player(shares[12], 1.5, False, 1.05)
    assert a.fair_odds_goal_supersub > 1.5
    # il est coté exactement comme s'il occupait le poste du titulaire qu'il remplace
    seul = {s.player_id: s for s in compute_player_shares(joueurs, "X", 1.5, xi_ids={1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12})}
    assert abs(shares[12].npxg_share - seul[12].npxg_share) < 1e-12


def test_lukic_ses_buts_des_matchs_non_mesures_comptent():
    """Lukić (Cluj) : 1 match mesuré (0,08 xG), 8 buts dans les autres -> coté 4,71.
    Sa production = xG mesuré + vrais buts ailleurs, rappelée selon la part non mesurée."""
    ligne_nm = _ligne(947, 0, None, None, 8, 1)
    ligne_m = _ligne(93, 93, 0.08, 0.02, 0, 0, 2)
    ligne_nm.goals_xg, ligne_nm.assists_xg = 0, 0
    ligne_m.goals_xg, ligne_m.assists_xg = 0, 0
    m = merge_season_stats([ligne_nm, ligne_m], "2026-2027")
    d = _player_dict_from_merged(SimpleNamespace(api_id=8, name="Lukic", position="F"), m, "2026-2027", [ligne_nm, ligne_m])
    assert abs(d["xg_per_90"] - 90 * (0.08 + 8) / 1040) < 1e-9      # 0,70 et non 0,08
    assert abs(d["part_non_mesuree"] - 947 / 1040) < 1e-9
    d["player_name"] = "Lukic"
    equipe = [_sans_xg(1, "GK", 0, 0)] + [_sans_xg(i, "CB", 0, 0) for i in range(2, 6)] + [_sans_xg(i, "CM", 1, 2) for i in range(6, 10)] + [_sans_xg(10, "CF_lone", 3, 1)] + [d]
    d["starts"] = 12
    shares = {s.player_id: s for s in compute_player_shares(equipe, "X", 1.3)}
    a = allocate_player(shares[8], 1.3, False, 0.91)
    assert a.fair_odds_goal_supersub < 4.0   # 4,71 avant ; cote réelle vérifiée sur Cluj
