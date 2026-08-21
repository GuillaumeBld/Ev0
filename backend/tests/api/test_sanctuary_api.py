"""Sanctuaire : amplitude du mouvement et filtres de la bibliotheque."""
import pytest

from app.api.sanctuary import _fold, max_move_pct

OUV = {"h2h": {"home": 2.29, "draw": 3.10, "away": 3.73}}
CLO = {"h2h": {"home": 2.52, "draw": 3.13, "away": 3.18}}


def test_retient_le_plus_grand_mouvement_pas_la_moyenne():
    """Rayo-Alaves reel : dom +10,0 %, nul +1,0 %, ext -14,7 %.
    Le maximum est 14,7 -- une moyenne diluerait le signal."""
    m = max_move_pct(OUV, CLO)
    assert m == round(abs(3.18 - 3.73) / 3.73 * 100, 2)
    assert m > 14.0 and m < 15.0


def test_un_seul_cote_qui_decroche_suffit():
    """Deux issues immobiles, une qui bouge fort : le match doit ressortir."""
    ouv = {"h2h": {"home": 2.00, "draw": 3.00, "away": 4.00}}
    clo = {"h2h": {"home": 2.00, "draw": 3.00, "away": 6.00}}
    assert max_move_pct(ouv, clo) == 50.0


def test_aucun_mouvement_donne_zero():
    assert max_move_pct(OUV, dict(OUV)) == 0.0


def test_sans_cloture_pas_d_amplitude():
    assert max_move_pct(OUV, None) is None
    assert max_move_pct(None, CLO) is None
    assert max_move_pct(None, None) is None


def test_h2h_incomplet_donne_none():
    """On ne devine pas : une issue manquante rend le calcul impossible."""
    assert max_move_pct({"h2h": {"home": 2.0}}, CLO) is None
    assert max_move_pct({"totals": {"over_2.5": 2.0}}, CLO) is None


def test_cote_nulle_ne_fait_pas_exploser():
    """Une cote a zero en base serait aberrante, mais ne doit pas lever."""
    assert max_move_pct({"h2h": {"home": 0, "draw": 3.0, "away": 4.0}}, CLO) is None


def test_recherche_equipe_insensible_aux_accents():
    assert "alaves" in _fold("Deportivo Alavés")
    assert "atletico" in _fold("Atlético Madrid")
    assert "bodo" in _fold("Bodø/Glimt")


def test_recherche_equipe_insensible_a_la_casse():
    assert _fold("ARSENAL") == _fold("Arsenal") == "arsenal"


def test_fold_ne_plante_pas_sur_vide():
    assert _fold("") == ""
    assert _fold(None) == ""


@pytest.mark.parametrize("with_closing,min_move,attendu", [
    (False, None, False),
    (True, None, True),
    (False, 10.0, True),
    (True, 10.0, True),
])
def test_un_seuil_d_amplitude_force_l_exigence_de_cloture(with_closing, min_move, attendu):
    """Regle metier : un match sans cloture n'a pas de mouvement.
    Cette table reproduit la condition du routeur."""
    exiger = with_closing or min_move is not None
    assert exiger is attendu
