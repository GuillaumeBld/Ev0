"""Un joueur sans minutes suffisantes recoit la cote de son poste, pas une cote inventee.

Deux bugs successifs, le second decouvert en corrigeant le premier.

1. Le plancher de lambda devenait un prix (06/11/2026, matin). Les planchers
   -- 0.001 buteur, 0.01 passeur -- existent pour eviter une division par
   zero. La cote supersub, qui normalise a 90 minutes en divisant lambda par
   (minutes_attendues / 90), les multipliait par 90 :

       passeur :  0.01  / (1/90) = 0.9   -> probabilite 0.593 -> cote 1.69
       buteur  :  0.001 / (1/90) = 0.09  -> probabilite 0.086 -> cote 11.62

   867 et 362 recommandations sur 30 jours, aucune reposant sur une donnee
   du joueur.

2. L'amplification ne venait PAS du plancher (06/11/2026, apres-midi). Elle
   frappe tout rythme divise par un temps de jeu minuscule, plancher ou pas :

       Federico Gatti, defenseur, lambda 0.0594, 1,6 minute attendue
       -> 0.0594 / (1.6/90) = 3.26 -> 96 % de chances de marquer -> cote 1.04

   1 206 recommandations actives reposaient sur moins de 20 minutes
   attendues, 2 590 affichaient un edge superieur a 100 %.

Le correctif retenu traite les deux d'un coup : sous SEUIL_MINUTES, le rythme
du joueur est ramene vers l'a priori de son poste a proportion des minutes
jouees. Un rythme surbooste ne peut plus se propager -- et le joueur recoit
un prix fonde (celui de son poste, ancre sur le xG du match) au lieu d'une
absence ou d'une aberration.
"""
import math

from app.pricing.assist import ASSIST_GOAL_RATE
from app.pricing.team_xg import (
    POSITION_PRIOR_XA,
    POSITION_PRIOR_XG,
    SEUIL_MINUTES,
    allocate_player,
    compute_player_shares,
    position_prior,
    regress_to_position,
)

BUDGET = 1.5 * ASSIST_GOAL_RATE


def _joueur(nom, poste, xg90, xa90, minutes, matchs=None):
    """Un joueur minimal, avec le temps de jeu qui pilote la regression."""
    matchs = matchs if matchs is not None else max(1, int(minutes // 90) or 1)
    return {
        "player_id": abs(hash(nom)) % 100000, "player_name": nom, "position": poste,
        "matches_played": matchs, "minutes_played": minutes,
        "npxg_per_90": xg90, "xa_per_90": xa90, "xg_per_90": xg90,
        "xgchain_per_90": 0.0,
        "shot_accuracy": 0.42, "xg_per_shot": 0.12, "avg_rating": 6.9,
        "key_pass_per_90": 0.30, "accurate_cross_per_90": 0.15, "cross_accuracy": 0.30,
        "npxg_total": xg90 * minutes / 90.0, "goals_total": 0,
        "xa_total": xa90 * minutes / 90.0, "assists_total": 0,
        "form_xg_5": None, "form_assists_5": None, "has_bzz_stats": True,
    }


def _alloc(joueur, is_pen_taker=False):
    share = compute_player_shares([joueur], "Equipe", lambda_team=1.5)[0]
    return allocate_player(share, team_match_xg=1.5, is_pen_taker=is_pen_taker,
                           budget_assists=BUDGET)


# ── La regression elle-meme ───────────────────────────────────────

def test_sans_minutes_le_rythme_est_celui_du_poste():
    rythme, poids = regress_to_position(5.40, 0.0, "FW", POSITION_PRIOR_XG, 0.122)
    assert poids == 0.0
    assert rythme == POSITION_PRIOR_XG["FW"]


def test_au_dela_du_seuil_le_rythme_est_intact():
    """Un joueur etabli n'est JAMAIS tire vers son poste. C'est ce qui separe
    cette regression d'une penalite de poste, rejetee par le depot (voir
    test_defender_calibration.py)."""
    for minutes in (SEUIL_MINUTES, 1200.0, 3000.0):
        rythme, poids = regress_to_position(0.30, minutes, "DF", POSITION_PRIOR_XG, 0.122)
        assert poids == 1.0
        assert rythme == 0.30, "un defenseur atypique garde son rythme mesure"


def test_aucune_marche_a_la_frontiere():
    juste_avant, _ = regress_to_position(0.30, SEUIL_MINUTES - 1, "DF", POSITION_PRIOR_XG, 0.122)
    juste_apres, _ = regress_to_position(0.30, SEUIL_MINUTES, "DF", POSITION_PRIOR_XG, 0.122)
    assert abs(juste_apres - juste_avant) < 0.001


def test_le_poids_monte_avec_les_minutes():
    poids = [regress_to_position(0.30, m, "FW", POSITION_PRIOR_XG, 0.122)[1]
             for m in (0, 90, 225, 450)]
    assert poids == sorted(poids), "le joueur doit peser de plus en plus"
    assert poids[2] == 0.5, "a mi-seuil, moitie joueur moitie poste"


def test_un_poste_inconnu_retombe_sur_la_moyenne():
    assert position_prior(None, POSITION_PRIOR_XG, 0.122) == 0.122
    assert position_prior("INCONNU", POSITION_PRIOR_XG, 0.122) == 0.122


def test_les_buckets_fins_sont_rabattus_sur_leur_famille():
    for bucket in ("CF_lone", "CF_pair", "SS", "winger"):
        assert position_prior(bucket, POSITION_PRIOR_XG, 0.0) == POSITION_PRIOR_XG["FW"]
    for bucket in ("AM", "CM", "DM"):
        assert position_prior(bucket, POSITION_PRIOR_XA, 0.0) == POSITION_PRIOR_XA["MF"]
    for bucket in ("WB", "FB", "CB"):
        assert position_prior(bucket, POSITION_PRIOR_XG, 0.0) == POSITION_PRIOR_XG["DF"]


# ── Les cas reels de production ───────────────────────────────────

def test_le_remplacant_qui_marque_en_cinq_minutes_nexplose_plus():
    """0.30 xG en 5 minutes = 5.40 xG/90 brut, soit une cote de 1.00.
    Ses 5 minutes doivent peser presque rien."""
    rythme, poids = regress_to_position(5.40, 5.0, "FW", POSITION_PRIOR_XG, 0.122)
    assert poids < 0.02
    assert rythme < 0.45, f"rythme {rythme:.3f} encore surbooste"


def test_le_cas_gatti_ne_produit_plus_une_cote_a_1_04():
    """Federico Gatti, defenseur, 1,6 minute jouee : cote juste a 1.04 en
    production le 06/11/2026, soit 96 % de chances de marquer."""
    a = _alloc(_joueur("Gatti", "DF", xg90=3.34, xa90=0.0, minutes=1.6, matchs=1))
    assert a.fair_odds_goal > 2.0, f"cote {a.fair_odds_goal} toujours aberrante"
    assert a.fair_odds_goal_supersub > 2.0
    assert a.prob_goal < 0.5


def test_aucune_cote_juste_sous_le_plausible():
    """Aucun joueur n'a plus de ~55 % de chances de marquer dans un match.
    Une cote juste de 1.04 ou 1.18 est impossible par construction."""
    for minutes in (1.0, 1.6, 3.0, 11.0, 30.0):
        a = _alloc(_joueur(f"J{minutes}", "DF", xg90=3.34, xa90=2.0, minutes=minutes, matchs=1))
        for cote in (a.fair_odds_goal, a.fair_odds_goal_supersub,
                     a.fair_odds_assist, a.fair_odds_assist_supersub):
            assert cote > 1.80, f"cote {cote} a {minutes} minutes"


def test_les_valeurs_exactes_des_deux_bugs_ne_sortent_plus():
    """1.69 passeur et 11.62 buteur, les deux valeurs constatees."""
    a = _alloc(_joueur("SansStats", "MF", xg90=0.0, xa90=0.0, minutes=1.0, matchs=1))
    produites = {a.fair_odds_goal, a.fair_odds_assist,
                 a.fair_odds_goal_supersub, a.fair_odds_assist_supersub}
    assert 1.69 not in produites
    assert 11.62 not in produites


# ── Ce qui ne doit pas changer ────────────────────────────────────

def test_un_joueur_sans_stats_recoit_un_prix_et_non_une_absence():
    """C'est le changement voulu : une cote de base plutot que rien."""
    a = _alloc(_joueur("Inconnu", "FW", xg90=0.0, xa90=0.0, minutes=0.0, matchs=0))
    assert a.prob_goal > 0, "un joueur sans stats doit avoir un prix"
    assert a.fair_odds_goal > 1.0
    assert a.poids_mesure_goal == 0.0, "et ce prix vient entierement de son poste"


def test_un_joueur_etabli_garde_sa_cote():
    a = _alloc(_joueur("Titulaire", "FW", xg90=0.35, xa90=0.08, minutes=1200.0, matchs=14))
    assert a.poids_mesure_goal == 1.0
    assert 2.0 <= a.fair_odds_goal <= 10.0
    assert a.fair_odds_assist > 1.0


def test_la_cote_supersub_reste_coherente():
    a = _alloc(_joueur("Titulaire", "FW", xg90=0.35, xa90=0.08, minutes=1200.0, matchs=14))
    assert a.p_goal_supersub > 0
    # Normalisee a 90 minutes, donc au moins aussi bonne que la cote standard.
    assert a.fair_odds_goal_supersub <= a.fair_odds_goal
    assert math.isclose(
        a.p_goal_supersub,
        1.0 - math.exp(-a.lambda_total / (a.expected_minutes / 90.0)),
        abs_tol=1e-4,
    )


def test_un_tireur_de_penalty_garde_sa_cote_buteur():
    """Un penalty est une information reelle, independante de l'historique."""
    a = _alloc(_joueur("Tireur", "MF", xg90=0.0, xa90=0.0, minutes=0.0, matchs=0),
               is_pen_taker=True)
    assert a.prob_goal > 0
    assert a.lambda_penalty > 0
