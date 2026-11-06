"""Un prenom abrege d'un cote et complet de l'autre doit se rapprocher.

Regression du 06/11/2026. Bzzoiro nomme les joueurs en abrege dans les
incidents de match ("H. Kane", "O. Dembele", "L. Martinez") alors que les
cotes bookmaker portent le nom complet ("Harry Kane", "Ousmane Dembele").

`_names_match` ne savait pas franchir l'abreviation : "harrykane" ne contient
pas "hkane", et le premier mot "harry" ne correspond pas a "h.". Resultat, le
reglement a marque PERDU 599 paris sur 7 509 alors que leur buteur figurait
bien dans les evenements du match.

C'est le trou residuel deja connu sous le nom de "noms abreges (AGF)".
"""
from app.ingestion.auto_settle import _names_match


# ── Les cas reels constates ───────────────────────────────────────

def test_les_cas_de_production():
    """Tous releves le 06/11/2026 parmi les 599 faussement perdants."""
    for complet, abrege in (
        ("Harry Kane", "H. Kane"),
        ("Mohamed Salah", "M. Salah"),
        ("Lautaro Martínez", "L. Martínez"),
        ("Takefusa Kubo", "T. Kubo"),
        ("John McGinn", "J. McGinn"),
        ("Ruben Aguilar", "R. Aguilar"),
        ("Ousmane Dembélé", "O. Dembélé"),
        ("Michael Olise", "M. Olise"),
        ("Dusan Vlahović", "D. Vlahović"),
    ):
        assert _names_match(complet, abrege), f"{complet} ≢ {abrege}"
        assert _names_match(abrege, complet), "le rapprochement doit etre symetrique"


def test_les_cas_deja_couverts_ne_regressent_pas():
    """Ces formes marchaient avant, elles doivent continuer."""
    assert _names_match("Enzo Le Fee", "Enzo Le Fée")          # accents
    assert _names_match("Adam Wharton", "Adam Wharton")        # identique
    assert _names_match("Mousa Al-Tamari", "Mousa Al Tamari")  # tiret
    assert _names_match("N'Diaye", "Ndiaye")                   # apostrophe
    assert _names_match("Idrissa Gueye", "Idrissa Gana Gueye") # deuxieme prenom


# ── Le garde-fou contre les faux positifs ─────────────────────────

def test_deux_prenoms_complets_differents_ne_se_confondent_pas():
    """Le coeur du garde-fou : au moins un cote doit etre abrege. Sinon
    "Marco Silva" et "Mateo Silva" passeraient, tous deux en M + Silva."""
    assert not _names_match("Marco Silva", "Mateo Silva")
    assert not _names_match("Thiago Silva", "Thomas Silva")
    assert not _names_match("Carlos Soler", "Cesar Soler")


def test_initiale_differente_ne_passe_pas():
    assert not _names_match("Harry Kane", "P. Kane")
    assert not _names_match("M. Salah", "Omar Salah")


def test_nom_de_famille_different_ne_passe_pas():
    assert not _names_match("Harry Kane", "H. Maguire")
    assert not _names_match("M. Salah", "Mohamed Simons")


def test_joueurs_sans_rapport():
    assert not _names_match("Kylian Mbappe", "Antoine Griezmann")
    assert not _names_match("K. Mbappe", "A. Griezmann")


# ── Robustesse ────────────────────────────────────────────────────

def test_mononymes_et_noms_uniques():
    """Un seul mot de chaque cote : la regle de l'initiale ne s'applique pas,
    seule l'egalite exacte ou l'inclusion decide."""
    assert _names_match("Rodri", "Rodri")
    assert not _names_match("Rodri", "R. Hernandez")


def test_abreviation_sans_point():
    """Certaines sources ecrivent "H Kane" sans point."""
    assert _names_match("Harry Kane", "H Kane")


def test_noms_de_famille_composes():
    assert _names_match("Kiernan Dewsbury-Hall", "K. Dewsbury-Hall")
    assert _names_match("Trent Alexander-Arnold", "T. Alexander-Arnold")
