"""Appariement d'un club de l'API a sa ligne canonique existante.

La reconstruction du 22/08 a cree 14 doublons : elle ne comparait que name_fr
en exact. « FC Barcelona » n'a pas reconnu « Barcelone », d'ou une seconde
ligne. L'ancienne portant l'identifiant Transfermarkt, le sync de 04:30
resolvait vers elle et reecrivait chaque nuit le mauvais identifiant sur les
joueurs.
"""
from app.scripts.rebuild_team_registry import cle_club


def test_replie_accents_et_ponctuation():
    assert cle_club("Séville") == cle_club("Seville")
    assert cle_club("Paris Saint-Germain") == cle_club("Paris Saint Germain")


def test_retire_les_affixes_de_club():
    """FC, AC, AS, SC, VfB, TSG, RB, UD, CF… ne distinguent pas deux clubs."""
    assert cle_club("FC Barcelona") == cle_club("Barcelona")
    assert cle_club("AC Milan") == cle_club("Milan")
    assert cle_club("AS Roma") == cle_club("Roma")
    assert cle_club("VfB Stuttgart") == cle_club("Stuttgart")
    assert cle_club("TSG Hoffenheim") == cle_club("Hoffenheim")
    assert cle_club("SC Freiburg") == cle_club("Freiburg")
    assert cle_club("RB Leipzig") == cle_club("Leipzig")
    assert cle_club("1. FC Union Berlin") == cle_club("Union Berlin")
    assert cle_club("Levante UD") == cle_club("Levante")


def test_ne_confond_pas_deux_clubs_distincts():
    assert cle_club("Inter") != cle_club("Inter Miami")
    assert cle_club("Manchester United") != cle_club("Manchester City")
    assert cle_club("Real Madrid") != cle_club("Real Sociedad")


def test_cle_vide_pour_un_nom_absent():
    assert cle_club(None) == ""
    assert cle_club("") == ""
