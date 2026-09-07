"""Le calendrier doit etre arithmetiquement possible, et la source fait foi.

Deux defaillances constatees le 07/09/2026, sur le meme incident :

  - `sync_events` n'ajoutait et ne mettait a jour, sans jamais retirer. Le
    29/08 la C1 a rendu un calendrier provisoire de 144 matchs pour la
    journee 1 ; le 07/09 elle rendait les 18 vrais matchs, avec d'autres
    identifiants. Les deux versions ont coexiste.
  - aucun controle ne regardait si le resultat tenait debout. Real Madrid
    disputait huit matchs au meme coup d'envoi et tous les indicateurs de
    sante restaient au vert — les fantomes AUGMENTAIENT le nombre de cotes et
    de recommandations.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.ingestion.bzzoiro.sync_events import PART_MINIMALE_RENDUE
from app.services.coherence_calendrier import (
    MatchAControler,
    controler,
    equipe_dedoublee,
    identifiants_incoherents,
    journee_surchargee,
    resumer,
)

CRENEAU = datetime(2026, 9, 8, 19, 0, tzinfo=UTC)
PLUS_TARD = datetime(2026, 9, 8, 21, 0, tzinfo=UTC)


def _match(identifiant, dom, ext, quand=CRENEAU, competition="champions_league"):
    return MatchAControler(
        identifiant=identifiant,
        competition=competition,
        equipe_domicile=dom,
        equipe_exterieur=ext,
        coup_denvoi=quand,
    )


# ---------------------------------------------------------------------------
# Regle 1 : une equipe ne joue qu'un match a la fois
# ---------------------------------------------------------------------------


def test_une_equipe_ne_peut_pas_jouer_deux_matchs_au_meme_coup_denvoi():
    """Le cas reel : Real Madrid contre Inter, contre le LASK et contre
    Leipzig, tous a 19h00 le 8 septembre."""
    matchs = [
        _match(1, "Real Madrid", "Inter"),
        _match(2, "Real Madrid", "LASK"),
        _match(3, "Real Madrid", "RB Leipzig"),
    ]

    violations = equipe_dedoublee(matchs)

    assert len(violations) == 1
    assert violations[0].regle == "equipe_dedoublee"
    assert violations[0].identifiants == frozenset({1, 2, 3})
    assert "Real Madrid" in violations[0].motif


def test_la_meme_equipe_a_deux_heures_differentes_est_licite():
    """Deux matchs le meme jour a des heures distinctes ne violent pas la
    regle : c'est rare, ce n'est pas impossible."""
    matchs = [
        _match(1, "Real Madrid", "Inter"),
        _match(2, "Real Madrid", "LASK", quand=PLUS_TARD),
    ]

    assert equipe_dedoublee(matchs) == []


def test_un_calendrier_normal_ne_declenche_rien():
    """Six matchs, douze equipes distinctes, meme creneau : une vraie soiree
    de C1."""
    matchs = [
        _match(i, f"Club {2 * i}", f"Club {2 * i + 1}") for i in range(1, 7)
    ]

    assert controler(matchs) == []
    assert identifiants_incoherents(matchs) == set()


# ---------------------------------------------------------------------------
# Regle 2 : n matchs exigent 2n equipes distinctes
# ---------------------------------------------------------------------------


def test_une_journee_ne_peut_pas_avoir_plus_de_matchs_que_la_moitie_des_equipes():
    """Quatre equipes ne peuvent pas produire trois matchs dans la journee."""
    matchs = [
        _match(1, "A", "B"),
        _match(2, "C", "D", quand=PLUS_TARD),
        _match(3, "A", "C", quand=datetime(2026, 9, 8, 23, 0, tzinfo=UTC)),
    ]

    violations = journee_surchargee(matchs)

    assert len(violations) == 1
    assert violations[0].regle == "journee_surchargee"
    assert violations[0].identifiants == frozenset({1, 2, 3})
    assert "3 matchs" in violations[0].motif


def test_le_maximum_theorique_exact_reste_licite():
    """Quatre equipes, deux matchs : c'est le maximum, pas une violation."""
    matchs = [
        _match(1, "A", "B"),
        _match(2, "C", "D", quand=PLUS_TARD),
    ]

    assert journee_surchargee(matchs) == []


def test_deux_competitions_sont_jugees_separement():
    """Un championnat charge ne doit pas contaminer le verdict d'un autre."""
    matchs = [
        _match(1, "A", "B", competition="ligue_1"),
        _match(2, "C", "D", competition="serie_a", quand=PLUS_TARD),
    ]

    assert controler(matchs) == []


# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------


def test_les_matchs_fautifs_sont_identifies_pour_etre_ecartes():
    """Le controle ne sert pas qu'a alerter : il nomme les matchs a ne jamais
    laisser atteindre le calculateur — et LUI SEUL. Lille-Betis partage la
    journee impossible des trois Real Madrid, mais n'a rien a se reprocher :
    l'ecarter reviendrait a supprimer du vrai calendrier."""
    matchs = [
        _match(1, "Real Madrid", "Inter"),
        _match(2, "Real Madrid", "LASK"),
        _match(9, "Lille", "Real Betis"),
    ]

    assert identifiants_incoherents(matchs) == {1, 2}


def test_le_resume_est_lisible_sur_telegram():
    matchs = [_match(1, "Real Madrid", "Inter"), _match(2, "Real Madrid", "LASK")]

    message = resumer(controler(matchs))

    assert "Real Madrid" in message
    assert "incoherent" in message.lower()
    assert resumer([]) == "calendrier coherent"


# ---------------------------------------------------------------------------
# Reconciliation : la source fait autorite sur ce qui EXISTE
# ---------------------------------------------------------------------------


def test_la_sentinelle_de_reconciliation_est_prudente():
    """Une source a moitie muette ne doit jamais pouvoir vider le calendrier.
    Le seuil protege exactement ce cas."""
    assert 0 < PART_MINIMALE_RENDUE <= 1


def test_la_reconciliation_ne_touche_ni_au_passe_ni_hors_fenetre():
    """Un match joue porte des statistiques et des paris regles : il ne
    disparait jamais, meme si la source cesse de le publier."""
    import inspect

    from app.ingestion.bzzoiro import sync_events as module

    source = inspect.getsource(module._reconcilier)
    assert 'status == "notstarted"' in source
    assert "event_date >= debut" in source
    assert "event_date <= fin" in source
    assert "league_api_id.in_(league_api_ids)" in source


def test_une_journee_impossible_alerte_sans_supprimer_les_matchs_valides():
    """`journee_surchargee` constate qu'une journee est impossible sans savoir
    quels matchs sont en trop. Elle doit donc alerter, jamais bloquer : sinon
    un vrai match paie pour ses voisins."""
    matchs = [
        _match(1, "A", "B"),
        _match(2, "C", "D", quand=PLUS_TARD),
        _match(3, "A", "C", quand=datetime(2026, 9, 8, 23, 0, tzinfo=UTC)),
    ]

    # La journee est signalee...
    assert any(v.regle == "journee_surchargee" for v in controler(matchs))
    # ... mais aucun match n'est ecarte : aucune equipe n'est dedoublee sur un
    # meme creneau, donc aucun coupable nominatif.
    assert identifiants_incoherents(matchs) == set()


def test_le_conflit_est_departage_par_la_fraicheur():
    """Un groupe en conflit contient presque toujours UN vrai match et des
    perimes. Le 07/09/2026, Real Madrid apparaissait dans huit rencontres a
    19h00 : une seule, contre l'Inter, etait au vrai calendrier ; les sept
    autres dataient du 29 aout. Ecarter tout le groupe supprimait aussi le vrai
    match — c'est ce qui est arrive lors de la purge, qui a emporte quatre
    rencontres reelles."""
    recent = datetime(2026, 9, 7, 11, 27, tzinfo=UTC)
    ancien = datetime(2026, 8, 29, 15, 28, tzinfo=UTC)
    matchs = [
        MatchAControler(1, "champions_league", "Real Madrid", "Inter", CRENEAU, recent),
        MatchAControler(2, "champions_league", "Real Madrid", "LASK", CRENEAU, ancien),
        MatchAControler(3, "champions_league", "Real Madrid", "RB Leipzig", CRENEAU, ancien),
    ]

    assert identifiants_incoherents(matchs) == {2, 3}


def test_sans_fraicheur_connue_on_ecarte_tout_le_groupe():
    """On ne devine pas lequel est le vrai : le groupe part, et le vrai match
    revient a la synchro suivante."""
    matchs = [
        _match(1, "Real Madrid", "Inter"),
        _match(2, "Real Madrid", "LASK"),
    ]

    assert identifiants_incoherents(matchs) == {1, 2}


# ---------------------------------------------------------------------------
# Le calculateur ne propose que ce qu'il sait pricer
# ---------------------------------------------------------------------------


def test_les_selections_nationales_sont_exclues_du_calculateur():
    """Le calculateur price des joueurs de club a partir de leurs statistiques
    de championnat et de l'effectif de leur equipe. Une selection nationale n'a
    ni l'un ni l'autre : 178 matchs de Ligue des Nations et d'amicaux noyaient
    les 109 vrais matchs de championnat."""
    from app.api.fixtures import COMPETITIONS_DE_SELECTIONS

    for cle in (
        "nations_league_uefa",
        "nations_league_concacaf",
        "friendly_international",
        "world_cup_2026",
    ):
        assert cle in COMPETITIONS_DE_SELECTIONS

    # Les competitions de clubs ne doivent jamais y figurer.
    for cle in ("premier_league", "champions_league", "ligue_1", "la_liga"):
        assert cle not in COMPETITIONS_DE_SELECTIONS


def test_le_filtre_est_optionnel_et_desactive_par_defaut():
    """Le calendrier continue d'afficher les selections : seul le calculateur
    demande le filtre."""
    import inspect

    from app.api.fixtures import list_fixtures

    signature = inspect.signature(list_fixtures)
    assert "clubs_only" in signature.parameters
    assert signature.parameters["clubs_only"].default.default is False
