"""Un joueur sans statistique exploitable n'a pas de cote.

Regression du 06/11/2026. Le plancher de lambda (0.001 buteur, 0.01 passeur)
existe pour eviter une division par zero. Normalise a 90 minutes par la cote
supersub -- lambda / (minutes_attendues / 90) -- il etait multiplie par 90 et
ressortait en prix d'allure serieuse :

    1.69 passeur    867 recommandations sur 30 jours, lambda = 0.0100
    11.62 buteur    362 recommandations sur 30 jours, lambda = 0.0010

Les deux viennent d'une minute de jeu attendue : 0.01 / (1/90) = 0.9 donne
une probabilite de 0.593, soit 1.69 ; 0.001 / (1/90) = 0.09 donne 0.086,
soit 11.62. Aucune de ces cotes ne repose sur une donnee du joueur.
"""
import math

from app.pricing.assist import ASSIST_GOAL_RATE
from app.pricing.team_xg import allocate_player, compute_player_shares

# Joueur normal : des statistiques, une vraie part du xG d'equipe.
JOUEUR_AVEC_STATS = {
    "player_id": 1, "player_name": "Buteur normal", "position": "FW",
    "matches_played": 15, "minutes_played": 1200,
    "npxg_per_90": 0.35, "xa_per_90": 0.08,
    "xg_per_90": 0.35, "xgchain_per_90": 0.0,
    "shot_accuracy": 0.42, "xg_per_shot": 0.12, "avg_rating": 6.9,
    "key_pass_per_90": 0.30, "accurate_cross_per_90": 0.15, "cross_accuracy": 0.30,
    "npxg_total": 4.0, "goals_total": 5, "xa_total": 1.0, "assists_total": 2,
    "form_xg_5": None, "form_assists_5": None,
    "has_bzz_stats": True,
}

# Le cas reel : aucune statistique offensive. C'est ce joueur qui ressortait
# a 1.69 passeur et 11.62 buteur.
JOUEUR_SANS_STATS = {
    "player_id": 2, "player_name": "Remplacant sans donnee", "position": "MF",
    "matches_played": 0, "minutes_played": 0,
    "npxg_per_90": 0.0, "xa_per_90": 0.0,
    "xg_per_90": 0.0, "xgchain_per_90": 0.0,
    "shot_accuracy": 0.0, "xg_per_shot": 0.0, "avg_rating": 0.0,
    "key_pass_per_90": 0.0, "accurate_cross_per_90": 0.0, "cross_accuracy": 0.0,
    "npxg_total": 0.0, "goals_total": 0, "xa_total": 0.0, "assists_total": 0,
    "form_xg_5": None, "form_assists_5": None,
    "has_bzz_stats": True,
}

BUDGET = 1.5 * ASSIST_GOAL_RATE


def _alloc(joueur, is_pen_taker=False):
    share = compute_player_shares([joueur], "Equipe", lambda_team=1.5)[0]
    return allocate_player(share, team_match_xg=1.5, is_pen_taker=is_pen_taker,
                           budget_assists=BUDGET)


# ── Sans matiere, pas de prix ─────────────────────────────────────

def test_sans_statistique_aucune_cote_buteur():
    a = _alloc(JOUEUR_SANS_STATS)
    assert a.has_signal_goal is False
    assert a.prob_goal == 0.0
    assert a.fair_odds_goal == 0.0


def test_sans_statistique_aucune_cote_passeur():
    a = _alloc(JOUEUR_SANS_STATS)
    assert a.has_signal_assist is False
    assert a.prob_assist == 0.0
    assert a.fair_odds_assist == 0.0


def test_sans_statistique_aucune_cote_supersub():
    """C'est la cote supersub qui portait 1.69 et 11.62 : c'est elle qui
    normalise a 90 minutes, donc elle qui amplifiait le plancher."""
    a = _alloc(JOUEUR_SANS_STATS)
    assert a.p_goal_supersub == 0.0
    assert a.fair_odds_goal_supersub == 0.0
    assert a.p_assist_supersub == 0.0
    assert a.fair_odds_assist_supersub == 0.0


def test_les_valeurs_exactes_du_bug_ne_sortent_plus():
    """1.69 et 11.62 etaient les deux valeurs constatees en production."""
    a = _alloc(JOUEUR_SANS_STATS)
    produites = {
        a.fair_odds_goal, a.fair_odds_assist,
        a.fair_odds_goal_supersub, a.fair_odds_assist_supersub,
    }
    assert 1.69 not in produites
    assert 11.62 not in produites


def test_une_cote_nulle_est_ecartee_par_les_garde_fous():
    """Les deux chemins de recommandation testent `not fair_odds` ou
    `probability <= 0` : une cote a 0 ne peut pas produire d'alerte."""
    a = _alloc(JOUEUR_SANS_STATS)
    for cote, proba in (
        (a.fair_odds_goal, a.prob_goal),
        (a.fair_odds_assist, a.prob_assist),
        (a.fair_odds_goal_supersub, a.p_goal_supersub),
        (a.fair_odds_assist_supersub, a.p_assist_supersub),
    ):
        assert (not cote) or proba <= 0


# ── Avec matiere, rien ne change ──────────────────────────────────

def test_un_joueur_avec_statistiques_garde_sa_cote():
    a = _alloc(JOUEUR_AVEC_STATS)
    assert a.has_signal_goal is True
    assert a.has_signal_assist is True
    assert 2.0 <= a.fair_odds_goal <= 10.0
    assert a.prob_goal > 0
    assert a.fair_odds_assist > 1.0


def test_la_cote_supersub_reste_calculee_quand_il_y_a_du_signal():
    a = _alloc(JOUEUR_AVEC_STATS)
    assert a.p_goal_supersub > 0
    # Normalisee a 90 minutes, donc au moins aussi bonne que la cote standard.
    assert a.fair_odds_goal_supersub <= a.fair_odds_goal
    # Les champs sont stockes arrondis a 4 decimales, d'ou la tolerance.
    assert math.isclose(
        a.p_goal_supersub,
        1.0 - math.exp(-a.lambda_total / (a.expected_minutes / 90.0)),
        abs_tol=1e-4,
    )


def test_un_tireur_de_penalty_sans_stats_garde_une_cote_buteur():
    """Un penalty est une vraie information, independante des statistiques :
    le signal existe meme sans historique offensif."""
    a = _alloc(JOUEUR_SANS_STATS, is_pen_taker=True)
    assert a.has_signal_goal is True
    assert a.prob_goal > 0
    # Le passeur, lui, reste sans matiere.
    assert a.has_signal_assist is False
    assert a.fair_odds_assist == 0.0
