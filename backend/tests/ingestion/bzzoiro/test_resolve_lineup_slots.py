"""Resolution des compos brutes en places identifiees.

Sur 1 468 matchs -- du 15/08/2025 au 13/04/2026 -- Bzzoiro renvoie
`"id": null` et ne laisse qu'un nom abrege ("Alisson", "A. Mac Allister").
Le rapprochement par nom complet n'aboutit que dans 2,4 % des cas ; par nom
court, restreint aux joueurs de ce match, dans 97,8 %.

Exigence qui prime sur le taux : une place non resolue ne disparait jamais.
Elle est ecrite sans identifiant, avec son nom et la raison. Un trou doit se
compter.
"""
from app.ingestion.bzzoiro.resolve_lineup_slots import (
    _index,
    places_du_match,
    resoudre_place,
)

VIVIER = [
    {"pid": 322, "nom": "Alisson Becker", "court": "Alisson"},
    {"pid": 323, "nom": "Alexis Mac Allister", "court": "A. Mac Allister"},
    {"pid": 324, "nom": "Virgil van Dijk", "court": "V. van Dijk"},
    {"pid": 325, "nom": "Randal Kolo Muani", "court": "R. Kolo Muani"},
]
INDEX = _index(VIVIER)


# --- Resolution d'une place ------------------------------------------------


def test_l_identifiant_fourni_prime_sur_tout_rapprochement():
    pid, chemin = resoudre_place({"id": 999, "name": "Alisson"}, INDEX)
    assert (pid, chemin) == (999, "id")


def test_un_nom_court_est_retrouve_dans_le_vivier_du_match():
    assert resoudre_place({"id": None, "name": "Alisson"}, INDEX) == (322, "short_name")
    assert resoudre_place(
        {"id": None, "name": "A. Mac Allister"}, INDEX
    ) == (323, "short_name")


def test_un_nom_complet_reste_accepte():
    assert resoudre_place(
        {"id": None, "name": "Virgil van Dijk"}, INDEX
    ) == (324, "name")


def test_les_accents_et_la_ponctuation_ne_bloquent_pas():
    """« V. van Dijk » doit se retrouver quelle que soit sa graphie."""
    assert resoudre_place({"id": None, "name": "V. Van-Dijk"}, INDEX)[0] == 324


def test_une_forme_a_initiales_multiples_reste_introuvable():
    """« R. K. Muani » ne correspond a rien d'exact : on ne devine pas."""
    pid, chemin = resoudre_place({"id": None, "name": "R. K. Muani"}, INDEX)
    assert pid is None
    assert chemin == "introuvable"


def test_un_homonyme_dans_le_meme_match_n_est_pas_tranche_au_hasard():
    index = _index([
        {"pid": 1, "nom": "Rafael Silva", "court": "R. Silva"},
        {"pid": 2, "nom": "Rodrigo Silva", "court": "R. Silva"},
    ])
    pid, chemin = resoudre_place({"id": None, "name": "R. Silva"}, index)
    assert pid is None
    assert chemin == "ambigu"


def test_une_place_sans_nom_ne_leve_pas():
    assert resoudre_place({"id": None, "name": ""}, INDEX) == (None, "introuvable")


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
            "away": {"players": [], "substitutes": []},
        },
        "lineup_status": "confirmed",
    }
    base.update(over)
    return base


def test_titulaires_et_remplacants_sont_distingues():
    places = places_du_match(_compo(), INDEX)

    titulaires = [p for p in places if p["is_starter"]]
    remplacants = [p for p in places if not p["is_starter"]]
    assert [p["player_name"] for p in titulaires] == ["Alisson", "A. Mac Allister"]
    assert [p["player_name"] for p in remplacants] == ["V. van Dijk"]
    assert all(p["is_home"] for p in places)


def test_les_deux_formes_archivees_sont_acceptees():
    """La reponse v2 entiere, ou le seul bloc par camp."""
    entiere = places_du_match(_compo(), INDEX)
    bloc_seul = places_du_match(_compo()["lineups"], INDEX)
    assert entiere == bloc_seul


def test_une_place_non_resolue_est_conservee_et_non_jetee():
    compo = _compo()
    compo["lineups"]["home"]["players"].append(
        {"id": None, "name": "R. K. Muani", "position": "F", "jersey_number": 9}
    )

    places = places_du_match(compo, INDEX)

    perdu = [p for p in places if p["player_name"] == "R. K. Muani"]
    assert len(perdu) == 1
    assert perdu[0]["player_api_id"] is None
    assert perdu[0]["resolution"] == "introuvable"


def test_un_camp_vide_ne_produit_aucune_place():
    assert places_du_match({"lineups": {"home": {}, "away": {}}}, INDEX) == []


def test_le_numero_de_maillot_textuel_est_converti():
    compo = _compo()
    compo["lineups"]["home"]["players"][0]["jersey_number"] = "1"
    assert places_du_match(compo, INDEX)[0]["jersey_number"] == 1


def test_un_numero_absurde_ne_fait_pas_echouer_la_compo():
    compo = _compo()
    compo["lineups"]["home"]["players"][0]["jersey_number"] = "n/a"
    assert places_du_match(compo, INDEX)[0]["jersey_number"] is None
