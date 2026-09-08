"""Les cotes et les recommandations doivent tenir debout toutes seules.

Suite du controle pose sur le calendrier. Les garde-fous du projet mesurent le
DEBIT — fraicheur des cotes, nombre de recommandations en 24h — jamais la
JUSTESSE. Le 07/09/2026, 126 matchs perimes ont produit 3 582 cotes et 523
recommandations en plus : tous les voyants sont passes au vert.

Ce que ces regles ont trouve en production le 08/09/2026 : 4 797 recommandations
sur 9 724 affichaient un avantage qui ne se deduisait pas de leurs propres
nombres. Cause : `best_odds` et `edge` etaient rafraichis quand la cote bougeait,
mais `fair_probability` et `fair_odds` restaient figes a leur valeur de creation.
La ligne gardait donc l'ancienne cote juste a cote du nouvel avantage.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.services.coherence_paris import (
    TOLERANCE_AVANTAGE,
    TOLERANCE_COTE_JUSTE,
    CoteAControler,
    RecommandationAControler,
    controler_cotes,
    controler_recommandations,
    cote_impossible,
    marche_a_marge_negative,
    prix_contradictoire,
    probabilite_hors_bornes,
    resumer,
)

RELEVE = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)


def _cote(identifiant, cote, issue="home", bookmaker="betclic", marche="h2h"):
    return CoteAControler(
        identifiant=identifiant,
        match_id=1,
        bookmaker=bookmaker,
        marche=marche,
        issue=issue,
        cote=cote,
        releve_le=RELEVE,
    )


def _reco(identifiant, proba, cote_juste=None, cote_marche=5.0, avantage=None):
    cote_juste = cote_juste if cote_juste is not None else 1 / proba
    avantage = avantage if avantage is not None else cote_marche / cote_juste - 1
    return RecommandationAControler(
        identifiant=identifiant,
        match_id=1,
        joueur="Joueur Test",
        probabilite_modele=proba,
        cote_juste=cote_juste,
        cote_marche=cote_marche,
        avantage=avantage,
    )


# ---------------------------------------------------------------------------
# Cotes
# ---------------------------------------------------------------------------


def test_une_cote_sous_1_est_impossible():
    """A 1.00 le parieur recupere sa mise, en dessous il perd en gagnant."""
    violations = cote_impossible([_cote(1, 2.50), _cote(2, 0.95), _cote(3, 1.00)])

    assert len(violations) == 1
    assert violations[0].identifiants == frozenset({2, 3})


def test_des_cotes_normales_ne_declenchent_rien():
    assert cote_impossible([_cote(1, 1.01), _cote(2, 15.0)]) == []


def test_un_1x2_a_marge_negative_est_signale():
    """Somme des probabilites implicites sous 100 % : le bookmaker paierait
    pour prendre le risque."""
    violations = marche_a_marge_negative([
        _cote(1, 3.5, "home"), _cote(2, 4.0, "draw"), _cote(3, 4.0, "away"),
    ])

    assert len(violations) == 1
    assert violations[0].regle == "marche_a_marge_negative"
    assert violations[0].identifiants == frozenset({1, 2, 3})


def test_un_1x2_normal_avec_marge_ne_declenche_rien():
    """Un vrai marche tourne autour de 105 %."""
    assert marche_a_marge_negative([
        _cote(1, 2.10, "home"), _cote(2, 3.40, "draw"), _cote(3, 3.60, "away"),
    ]) == []


def test_un_marche_incomplet_ne_prouve_rien():
    """Deux issues sur trois : la somme est mecaniquement basse, ce n'est pas
    une anomalie mais une donnee partielle."""
    assert marche_a_marge_negative([
        _cote(1, 3.5, "home"), _cote(2, 4.0, "draw"),
    ]) == []


def test_deux_bookmakers_sont_juges_separement():
    """Melanger leurs cotes fabriquerait une marge negative artificielle."""
    assert marche_a_marge_negative([
        _cote(1, 2.10, "home", "betclic"), _cote(2, 3.40, "draw", "betclic"),
        _cote(3, 3.60, "away", "betclic"),
        _cote(4, 2.15, "home", "unibet"), _cote(5, 3.35, "draw", "unibet"),
        _cote(6, 3.55, "away", "unibet"),
    ]) == []


# ---------------------------------------------------------------------------
# Recommandations
# ---------------------------------------------------------------------------


def test_une_probabilite_hors_bornes_est_signalee():
    violations = probabilite_hors_bornes([
        _reco(1, 0.25), RecommandationAControler(2, 1, "X", 0.0, 4.0, 5.0, 0.25),
        RecommandationAControler(3, 1, "Y", 1.0, 1.0, 5.0, 4.0),
    ])

    assert len(violations) == 1
    assert violations[0].identifiants == frozenset({2, 3})


def test_une_recommandation_coherente_ne_declenche_rien():
    """Les quatre nombres se repondent : proba 0.20, cote juste 5.00, cote
    marche 8.00, avantage 0.60."""
    assert prix_contradictoire([_reco(1, 0.20, 5.0, 8.0, 0.60)]) == []


def test_l_arrondi_de_stockage_n_est_pas_une_contradiction():
    """`fair_odds` est stocke a deux decimales : 1/0.3958 = 2.5265 devient
    2.53. Signaler cela ferait crier au loup sur 2 485 lignes saines."""
    proba = 0.3958
    assert prix_contradictoire([
        _reco(1, proba, 2.53, 8.0, 8.0 / 2.53 - 1)
    ]) == []


def test_une_cote_juste_figee_est_une_contradiction():
    """Le cas reel : le modele repricie, `edge` suit la nouvelle valeur mais
    `fair_odds` reste a l'ancienne. La ligne se contredit."""
    violations = prix_contradictoire([_reco(1, 0.50, 4.00, 8.0, 1.00)])

    assert len(violations) == 1
    assert violations[0].regle == "prix_contradictoire"
    assert violations[0].identifiants == frozenset({1})


def test_un_avantage_fige_est_une_contradiction():
    """La cote du marche a bouge, l'avantage est reste : il ne se deduit plus
    des nombres affiches."""
    violations = prix_contradictoire([_reco(1, 0.20, 5.0, 8.0, avantage=0.10)])

    assert len(violations) == 1
    assert violations[0].identifiants == frozenset({1})


def test_les_tolerances_sont_calees_sur_la_precision_reelle():
    """La cote juste absorbe l'arrondi a deux decimales, l'avantage non : il est
    stocke en pleine precision."""
    assert TOLERANCE_COTE_JUSTE >= 0.005
    assert TOLERANCE_AVANTAGE < TOLERANCE_COTE_JUSTE


# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------


def test_le_resume_est_lisible_sur_telegram():
    message = resumer(
        controler_cotes([_cote(1, 0.95)]), "Cotes"
    )

    assert "Cotes" in message
    assert resumer([], "Cotes") == "Cotes : rien a signaler"


def test_un_lot_sain_ne_produit_aucune_violation():
    cotes = [_cote(1, 2.10, "home"), _cote(2, 3.40, "draw"), _cote(3, 3.60, "away")]
    recos = [_reco(1, 0.20, 5.0, 8.0, 0.60), _reco(2, 0.10, 10.0, 15.0, 0.50)]

    assert controler_cotes(cotes) == []
    assert controler_recommandations(recos) == []


def test_l_arrondi_se_propage_dans_l_avantage():
    """Cas reel : Barcelone, cote juste 1,26 et cote marche 17,25. Un demi-pas
    d'arrondi sur la cote juste deplace l'avantage de 0,054 — bien au-dela du
    seuil fixe de 0,005. Comparer a un seuil unique accuserait l'arrondi."""
    from app.services.coherence_paris import tolerance_avantage

    assert tolerance_avantage(1.26, 17.25) > 0.05
    # Cote juste longue : l'arrondi ne deplace presque rien, le seuil reste serre.
    assert tolerance_avantage(20.0, 30.0) == TOLERANCE_AVANTAGE

    # La ligne Barcelone ne doit donc PAS etre signalee...
    assert prix_contradictoire([_reco(1, 1 / 1.26, 1.26, 17.25, 12.7406)]) == []
    # ... alors qu'un avantage vraiment fige l'est toujours.
    assert len(prix_contradictoire([_reco(2, 0.20, 5.0, 8.0, avantage=0.10)])) == 1
