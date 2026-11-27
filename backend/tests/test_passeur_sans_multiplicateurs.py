"""Règle passeur du 27/11/2026 : rythme carrière, sans multiplicateurs de
création ni de conversion, et jamais de cote « plancher » (100.50)."""
from app.pricing.team_xg import allocate_player, compute_player_shares


def _j(pid, pos, xa90, xg90=0.1, carriere_xa=None, **kw):
    d = {"player_id": pid, "player_name": f"J{pid}", "position": pos, "minutes_played": 1800,
         "matches_played": 20, "starts": 20, "xg_per_90": xg90, "npxg_per_90": xg90,
         "xa_per_90": xa90, "key_pass_per_90": kw.get("kp", 1.0),
         "accurate_cross_per_90": kw.get("ctr", 0.5), "cross_accuracy": 0.3,
         "xa_total": xa90 * 20, "assists_total": kw.get("ast", 0)}
    if carriere_xa is not None:
        d["carriere_xa_per_90"] = carriere_xa
    return d


def _onze(createur):
    return [_j(1, "GK", 0.01)] + [_j(i, "CM", 0.10) for i in range(2, 11)] + [createur]


def test_les_passes_des_11_postes_egalent_le_budget():
    joueurs = _onze(_j(11, "CF_lone", 0.03, kp=0.2, ctr=0.0))
    budget = 1.4 * 0.70
    shares = compute_player_shares(joueurs, "X", 1.4)
    total = 0.0
    for s in shares:
        a = allocate_player(s, 1.4, False, budget)
        total += a.lambda_assist_90 if hasattr(a, "lambda_assist_90") else -__import__("math").log(1 - a.p_assist_supersub)
    assert abs(total - budget) < 0.02


def test_pas_de_multiplicateur_la_part_suit_le_rythme():
    """Deux joueurs au même rythme, l'un gros créateur en passes clés :
    même cote passeur (le xA n'est plus recompté)."""
    joueurs = _onze(_j(11, "CM", 0.10, kp=4.0, ctr=3.0, ast=12))
    shares = {s.player_id: s for s in compute_player_shares(joueurs, "X", 1.4)}
    a10 = allocate_player(shares[10], 1.4, False, 0.98)
    a11 = allocate_player(shares[11], 1.4, False, 0.98)
    assert a10.fair_odds_assist_supersub == a11.fair_odds_assist_supersub
    assert a11.creation_multiplier == 1.0


def test_le_rythme_carriere_prime_sur_la_saison():
    """Avant-centre à xA de saison quasi nul mais carrière de passeur."""
    joueurs = _onze(_j(11, "CF_lone", 0.005, carriere_xa=0.15))
    shares = {s.player_id: s for s in compute_player_shares(joueurs, "X", 1.4)}
    a = allocate_player(shares[11], 1.4, False, 0.98)
    assert 4.0 < a.fair_odds_assist_supersub < 12.0


def test_jamais_de_cote_plancher():
    joueurs = _onze(_j(11, "CF_lone", 0.0001))
    shares = {s.player_id: s for s in compute_player_shares(joueurs, "X", 1.4)}
    a = allocate_player(shares[11], 1.4, False, 0.98)
    assert a.fair_odds_assist_supersub == 0.0
    assert a.fair_odds_assist == 0.0
