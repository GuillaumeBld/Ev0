"""Sanctuaire : amplitude du mouvement et filtres de la bibliotheque."""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.sanctuary import _fold, list_matches, max_move_pct

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


def test_fold_reutilise_anchor_pour_les_lettres_non_decomposables():
    """anchor._fold traite les lettres qui ne se decomposent pas en NFKD
    (o barre, l barre, thorn, ae ligature) -- une copie locale sans leurs
    entrees majuscules les perd des lors qu'elles sont en initiale, avant
    meme que .lower() n'entre en jeu."""
    assert "lks" in _fold("ŁKS Łódź")
    assert "orn" in _fold("Ørn Horten")
    assert "thor" in _fold("Þór Akureyri")
    assert "aegir" in _fold("Ægir")


# ── Tests de list_matches, sans PostgreSQL ──────────────────────────────────
#
# La requete SQL n'est qu'une jointure ; le regroupement des deux phases et
# les filtres with_closing/min_move sont du Python execute apres. On simule
# donc db.execute pour lui faire renvoyer des lignes construites a la main --
# des objets factices avec seulement les attributs lus par le routeur --
# et on appelle list_matches() directement, sans passer par Depends(get_db).

def _fixture(fid, home="Home", away="Away", league="ligue1", kickoff=None):
    return SimpleNamespace(
        id=fid,
        home_team=home,
        away_team=away,
        league=league,
        kickoff_utc=kickoff or datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


def _estimate(phase, odds, lambda_home=1.5, lambda_away=1.2, as_of=None):
    return SimpleNamespace(
        phase=phase,
        odds=odds,
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        as_of_utc=as_of or datetime(2026, 8, 19, tzinfo=timezone.utc),
    )


class _FakeResult:
    """Imite l'objet renvoye par AsyncSession.execute : seul .all() est lu."""

    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Renvoie les lignes fournies telles quelles, sans toucher a PostgreSQL.

    Ce qu'on veut exercer, c'est le regroupement/filtrage Python qui suit la
    requete dans list_matches -- pas la requete elle-meme.
    """

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _FakeResult(self._rows)


async def test_ouverture_seule_exclue_par_un_seuil_de_mouvement():
    """Un match sans cloture n'a pas de mouvement mesurable : min_move l'exclut."""
    fx = _fixture(1)
    db = _FakeSession([(_estimate("opening", OUV), fx)])
    out = await list_matches(team=None, league=None, with_closing=False, min_move=10.0, db=db)
    assert out == []


async def test_ouverture_seule_presente_sans_filtre():
    """Sans with_closing ni min_move, une ouverture seule doit ressortir."""
    fx = _fixture(1)
    db = _FakeSession([(_estimate("opening", OUV), fx)])
    out = await list_matches(team=None, league=None, with_closing=False, min_move=None, db=db)
    assert len(out) == 1
    assert out[0].fixture_id == 1
    assert out[0].opening is not None
    assert out[0].closing is None


async def test_ouverture_seule_exclue_par_with_closing_sans_seuil():
    """with_closing=True seul (sans min_move) doit aussi exiger la cloture --
    ce cas ne passe pas par le filtre min_move, contrairement au precedent."""
    fx = _fixture(1)
    db = _FakeSession([(_estimate("opening", OUV), fx)])
    out = await list_matches(team=None, league=None, with_closing=True, min_move=None, db=db)
    assert out == []


async def test_mouvement_au_dessus_du_seuil_presente():
    fx = _fixture(2)
    db = _FakeSession([
        (_estimate("opening", OUV), fx),
        (_estimate("closing", CLO), fx),
    ])
    out = await list_matches(team=None, league=None, with_closing=False, min_move=10.0, db=db)
    assert len(out) == 1
    assert out[0].fixture_id == 2


async def test_mouvement_sous_le_seuil_absent():
    fx = _fixture(2)
    db = _FakeSession([
        (_estimate("opening", OUV), fx),
        (_estimate("closing", CLO), fx),
    ])
    out = await list_matches(team=None, league=None, with_closing=False, min_move=20.0, db=db)
    assert out == []


async def test_regroupe_les_deux_phases_et_respecte_l_ordre_des_lignes():
    """Un match avec ouverture+cloture doit avoir les deux champs remplis ;
    l'ordre de sortie doit suivre celui des lignes renvoyees par la requete."""
    fx1 = _fixture(1, home="A", away="B")
    fx2 = _fixture(2, home="C", away="D")
    db = _FakeSession([
        (_estimate("opening", OUV), fx1),
        (_estimate("closing", CLO), fx1),
        (_estimate("opening", OUV), fx2),
    ])
    out = await list_matches(team=None, league=None, with_closing=False, min_move=None, db=db)
    assert [m.fixture_id for m in out] == [1, 2]
    assert out[0].opening is not None and out[0].closing is not None
    assert out[1].opening is not None and out[1].closing is None
