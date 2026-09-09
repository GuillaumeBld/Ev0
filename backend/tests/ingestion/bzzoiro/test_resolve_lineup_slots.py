"""Resolution des compos brutes en places identifiees.

Sur 1 468 matchs -- du 15/08/2025 au 13/04/2026 -- Bzzoiro renvoie
`"id": null` et ne laisse qu'un nom abrege ("Alisson", "A. Mac Allister").
Le rapprochement par nom complet n'aboutit que dans 2,4 % des cas.

On raisonne comme un observateur du match : du vivier le plus etroit -- les
joueurs de ce camp sur ce match -- au plus large -- l'effectif du club --, en
s'arretant a la premiere correspondance unique.

Exigence qui prime sur le taux : on ne tranche jamais au hasard, et une place
non resolue ne disparait pas. Elle est ecrite sans identifiant, avec son nom.
"""
from app.ingestion.bzzoiro.resolve_lineup_slots import (
    chercher,
    nom_de_famille,
    places_du_match,
    resoudre_place,
)


def _j(pid, nom, court, maillot=None):
    return {"pid": pid, "nom": nom, "court": court, "maillot": maillot}


LIVERPOOL = [
    _j(322, "Alisson Becker", "Alisson", 1),
    _j(323, "Alexis Mac Allister", "A. Mac Allister", 10),
    _j(324, "Virgil van Dijk", "V. van Dijk", 4),
    _j(325, "Ignace Van den Brempt", "I. V. d. Brempt", 21),
]
ADVERSAIRE = [
    _j(701, "Raul Garcia", "R. García", 8),
    _j(702, "Pascal Groß", "P. Gross", 13),
]
CAMPS = {True: LIVERPOOL, False: ADVERSAIRE}


# --- Lecture du nom ---------------------------------------------------------


def test_le_nom_de_famille_ignore_les_initiales():
    assert nom_de_famille("I. V. d. Brempt") == "brempt"
    assert nom_de_famille("R. K. Muani") == "muani"
    assert nom_de_famille("Alisson") == "alisson"
    assert nom_de_famille("") == ""


def test_l_eszett_et_l_apostrophe_ne_bloquent_pas():
    """« Groß » vaut « Gross », « N'Soki » vaut « Nsoki »."""
    assert nom_de_famille("P. Gross") == nom_de_famille("Pascal Groß")
    assert nom_de_famille("S. N'Soki") == nom_de_famille("Stanley Nsoki")


# --- Recherche dans un vivier ----------------------------------------------


def test_le_nom_court_est_la_lecture_la_plus_stricte():
    assert chercher(LIVERPOOL, "Alisson", None) == (322, "court")


def test_le_nom_complet_reste_accepte():
    assert chercher(LIVERPOOL, "Virgil van Dijk", None) == (324, "complet")


def test_une_forme_a_initiales_multiples_passe_par_le_nom_de_famille():
    """« I. V. d. Brempt » ne se lit que par sa fin."""
    pid, lecture = chercher(
        [_j(325, "Ignace Van den Brempt", "autre chose", 21)],
        "I. V. d. Brempt", None,
    )
    assert (pid, lecture) == (325, "famille")


def test_un_nom_de_famille_noye_au_milieu_est_retrouve():
    pid, lecture = chercher(
        [_j(9, "Lionel Mpasi Nzau", "L. Nzau", 77)], "L. M'Pasi", 77
    )
    assert (pid, lecture) == (9, "inclus")


def test_un_nom_de_famille_ne_se_compare_pas_a_la_fin_d_un_autre():
    """« X. Ann » ne doit pas trouver « Antoine Griezmann », qui finit par « ann ».

    Piege reel : compare a la chaine compactee entiere, un fragment court
    rapproche a peu pres n'importe qui.
    """
    assert chercher([_j(9, "Antoine Griezmann", "A. Griezmann")], "X. Ann", None) == (
        None, None,
    )


def test_un_fragment_trop_court_n_est_pas_recherche_par_inclusion():
    """« Ann » se trouve dans « Yann Sommer » sans rien vouloir dire.

    Trois lettres se rencontrent partout : l'inclusion en exige quatre.
    """
    assert chercher([_j(9, "Yann Sommer", "Y. Sommer")], "X. Ann", None) == (
        None, None,
    )


def test_le_maillot_departage_deux_homonymes_du_meme_camp():
    vivier = [_j(1, "Rafael Silva", "R. Silva", 7), _j(2, "Rodrigo Silva", "R. Silva", 22)]
    assert chercher(vivier, "R. Silva", 22) == (2, "court+maillot")


def test_sans_maillot_deux_homonymes_ne_sont_pas_tranches():
    vivier = [_j(1, "Rafael Silva", "R. Silva"), _j(2, "Rodrigo Silva", "R. Silva")]
    assert chercher(vivier, "R. Silva", None) == (None, None)


# --- Enchainement des viviers ----------------------------------------------


def test_l_identifiant_fourni_prime_sur_tout_rapprochement():
    assert resoudre_place(
        {"id": 999, "name": "Alisson"}, [("camp", LIVERPOOL)]
    ) == (999, "id")


def test_on_s_arrete_au_vivier_le_plus_etroit():
    pid, chemin = resoudre_place(
        {"id": None, "name": "Alisson"},
        [("camp", LIVERPOOL), ("effectif", ADVERSAIRE)],
    )
    assert (pid, chemin) == (322, "camp/court")


def test_l_effectif_du_club_rattrape_le_remplacant_non_utilise():
    """Il figure sur la feuille de match sans y avoir de statistique."""
    pid, chemin = resoudre_place(
        {"id": None, "name": "S. N'Soki", "jersey_number": 34},
        [("camp", LIVERPOOL), ("effectif", [_j(50, "Stanley Nsoki", "S. Nsoki", 34)])],
    )
    assert (pid, chemin) == (50, "effectif/court")


def test_un_joueur_absent_de_la_base_reste_absent():
    pid, chemin = resoudre_place(
        {"id": None, "name": "Savinho", "jersey_number": 26},
        [("camp", LIVERPOOL), ("effectif", ADVERSAIRE)],
    )
    assert (pid, chemin) == (None, "absent")


def test_une_place_sans_nom_ne_leve_pas():
    assert resoudre_place({"id": None, "name": ""}, [("camp", LIVERPOOL)]) == (
        None, "absent",
    )


# --- Places d'un match ------------------------------------------------------


def _compo(**over):
    base = {
        "lineups": {
            "home": {
                "formation": "4-3-3",
                "players": [
                    {"id": None, "name": "Alisson", "position": "G",
                     "jersey_number": 1},
                    {"id": None, "name": "A. Mac Allister", "position": "M",
                     "jersey_number": 10},
                ],
                "substitutes": [
                    {"id": None, "name": "V. van Dijk", "position": "D",
                     "jersey_number": 4},
                ],
            },
            "away": {
                "players": [
                    {"id": None, "name": "R. García", "position": "M",
                     "jersey_number": 8},
                ],
                "substitutes": [],
            },
        },
        "lineup_status": "confirmed",
    }
    base.update(over)
    return base


def test_titulaires_et_remplacants_sont_distingues():
    places = places_du_match(_compo(), CAMPS)

    domicile = [p for p in places if p["is_home"]]
    assert [p["player_name"] for p in domicile if p["is_starter"]] == [
        "Alisson", "A. Mac Allister",
    ]
    assert [p["player_name"] for p in domicile if not p["is_starter"]] == [
        "V. van Dijk",
    ]


def test_deux_homonymes_adverses_ne_sont_jamais_confondus():
    """C'est tout l'interet de chercher d'abord dans le camp du joueur.

    Un « R. Garcia » de chaque cote n'est jamais compare a l'autre.
    """
    camps = {
        True: LIVERPOOL + [_j(900, "Ricardo Garcia", "R. García", 8)],
        False: ADVERSAIRE,
    }
    compo = _compo()
    compo["lineups"]["home"]["players"].append(
        {"id": None, "name": "R. García", "position": "M", "jersey_number": 8}
    )

    places = places_du_match(compo, camps)
    par_camp = {p["is_home"]: p for p in places if p["player_name"] == "R. García"}
    assert par_camp[True]["player_api_id"] == 900
    assert par_camp[False]["player_api_id"] == 701


def test_les_deux_formes_archivees_sont_acceptees():
    """La reponse v2 entiere, ou le seul bloc par camp."""
    assert places_du_match(_compo(), CAMPS) == places_du_match(
        _compo()["lineups"], CAMPS
    )


def test_une_place_non_resolue_est_conservee_et_non_jetee():
    """Deux joueurs libres : l'elimination s'abstient, la place reste vide."""
    compo = _compo()
    compo["lineups"]["home"]["players"].append(
        {"id": None, "name": "Savinho", "position": "F", "jersey_number": 26}
    )
    camps = {
        True: LIVERPOOL + [_j(400, "Rodrigo Hernandez", "Rodri", 16)],
        False: ADVERSAIRE,
    }

    perdu = [p for p in places_du_match(compo, camps) if p["player_name"] == "Savinho"]
    assert len(perdu) == 1
    assert perdu[0]["player_api_id"] is None
    assert perdu[0]["resolution"] == "absent"


def test_un_camp_vide_ne_produit_aucune_place():
    assert places_du_match({"lineups": {"home": {}, "away": {}}}, CAMPS) == []


def test_le_numero_de_maillot_textuel_est_converti():
    compo = _compo()
    compo["lineups"]["home"]["players"][0]["jersey_number"] = "1"
    assert places_du_match(compo, CAMPS)[0]["jersey_number"] == 1


def test_un_numero_absurde_ne_fait_pas_echouer_la_compo():
    compo = _compo()
    compo["lineups"]["home"]["players"][0]["jersey_number"] = "n/a"
    assert places_du_match(compo, CAMPS)[0]["jersey_number"] is None


def test_le_chemin_de_resolution_reste_court_pour_la_colonne():
    """resolution est un String(32) : aucun libelle ne doit le deborder."""
    compo = _compo()
    compo["lineups"]["home"]["players"].append(
        {"id": None, "name": "R. Silva", "jersey_number": 22}
    )
    camps = {
        True: [_j(1, "Rafael Silva", "R. Silva", 7), _j(2, "Rodrigo Silva", "R. Silva", 22)],
        False: [],
    }
    for p in places_du_match(compo, camps, {True: [], False: []}):
        assert len(p["resolution"]) <= 32


# --- Elimination ------------------------------------------------------------
#
# Un surnom ne se rapproche d'aucune chaine : la compo dit « Savinho » quand
# la fiche dit « Sávio », « M. Kim » quand elle dit « Kim Min-jae ». Seule la
# deduction les rattrape.


def _compo_un_camp(noms):
    return {"lineups": {
        "home": {"players": [{"id": None, "name": n} for n in noms],
                 "substitutes": []},
        "away": {"players": [], "substitutes": []},
    }}


def test_le_dernier_nom_designe_le_dernier_joueur_libre():
    vivier = [
        _j(1, "Alisson Becker", "Alisson"),
        _j(2, "Savio Moreira", "Sávio"),
    ]
    places = places_du_match(
        _compo_un_camp(["Alisson", "Savinho"]), {True: vivier, False: []}
    )

    savinho = [p for p in places if p["player_name"] == "Savinho"][0]
    assert savinho["player_api_id"] == 2
    assert savinho["resolution"] == "elimination"


def test_deux_noms_restants_ne_sont_pas_apparies_au_hasard():
    """A deux contre deux, rien ne dit lequel va avec lequel."""
    vivier = [_j(1, "Savio Moreira", "Sávio"), _j(2, "Kim Min-jae", "M.-J. Kim")]
    places = places_du_match(
        _compo_un_camp(["Savinho", "M. Kim"]), {True: vivier, False: []}
    )
    assert all(p["player_api_id"] is None for p in places)
    assert all(p["resolution"] == "absent" for p in places)


def test_un_seul_nom_mais_plusieurs_joueurs_libres_reste_absent():
    """Un remplacant non utilise laisse plusieurs joueurs sans place."""
    vivier = [
        _j(1, "Alisson Becker", "Alisson"),
        _j(2, "Savio Moreira", "Sávio"),
        _j(3, "Rodrigo Hernandez", "Rodri"),
    ]
    places = places_du_match(
        _compo_un_camp(["Alisson", "Savinho"]), {True: vivier, False: []}
    )
    assert [p["resolution"] for p in places] == ["camp/court", "absent"]


def test_l_elimination_ne_traverse_pas_les_camps():
    """Le joueur libre de l'autre camp ne doit jamais etre pris."""
    places = places_du_match(
        _compo_un_camp(["Savinho"]),
        {True: [], False: [_j(9, "Savio Moreira", "Sávio")]},
    )
    assert places[0]["player_api_id"] is None


# --- Deux joueurs, un seul nom ----------------------------------------------
#
# Osasuna aligne deux « R. García » (maillots 9 et 14), Middlesbrough deux
# « J. Jones » (51 et 52). 29 cas dans le perimetre. Une cle portant le nom
# les fondait en une seule ligne, effacant un joueur.


def test_deux_homonymes_du_meme_camp_occupent_deux_places():
    vivier = [
        _j(1, "Raul Garcia", "R. García", 14),
        _j(2, "Ruben Garcia", "R. García", 9),
    ]
    compo = {"lineups": {
        "home": {"players": [
            {"id": None, "name": "R. García", "jersey_number": 14},
            {"id": None, "name": "R. García", "jersey_number": 9},
        ], "substitutes": []},
        "away": {"players": [], "substitutes": []},
    }}

    places = places_du_match(compo, {True: vivier, False: []})

    assert [p["slot"] for p in places] == [0, 1]
    assert [p["player_api_id"] for p in places] == [1, 2]


def test_le_rang_numerote_les_titulaires_puis_les_remplacants():
    places = places_du_match(_compo(), CAMPS)

    domicile = [p for p in places if p["is_home"]]
    assert [(p["slot"], p["is_starter"]) for p in domicile] == [
        (0, True), (1, True), (2, False),
    ]
    # chaque camp repart de zero
    assert [p["slot"] for p in places if not p["is_home"]] == [0]


def test_le_rang_est_unique_par_camp():
    """C'est la cle d'unicite en base : elle ne doit jamais collisionner."""
    places = places_du_match(_compo(), CAMPS)
    for dom in (True, False):
        rangs = [p["slot"] for p in places if p["is_home"] == dom]
        assert len(rangs) == len(set(rangs))
