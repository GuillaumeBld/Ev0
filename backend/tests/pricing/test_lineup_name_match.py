"""Matching des titulaires (compo) → effectif, tolérant aux prénoms abrégés."""
from app.pricing.team_xg import _lineup_name_compatible, _match_tokens, compute_lineup_allocation


def _c(a, b):
    return _lineup_name_compatible(_match_tokens(a), _match_tokens(b))


def test_abbreviated_first_name_matches():
    # Le cas réel qui faisait sauter un titulaire (10 → 9)
    assert _c("T. Hernández", "Theo Hernández")
    assert _c("I. Gueye", "Idrissa Gueye")


def test_abbreviated_disambiguates_homonyms():
    # "T. Hernández" ne doit PAS matcher Lucas Hernández
    assert not _c("T. Hernández", "Lucas Hernández")
    assert _c("L. Hernández", "Lucas Hernández")


def test_exact_and_accents():
    assert _c("Kylian Mbappé", "Kylian Mbappe")
    assert _c("Michael Olise", "Michael Olise")


def test_different_players_do_not_match():
    assert not _c("Ethan Mbappé", "Kylian Mbappé")
    assert not _c("Bukayo Saka", "Harry Kane")


def test_mononym():
    assert _c("Vinícius", "Vinícius Júnior")
    assert _c("Rodri", "Rodri")


def test_compute_lineup_keeps_abbreviated_starter():
    # 10 joueurs d'effectif, compo avec un prénom abrégé → les 10 restent
    squad = [
        {"player_id": i, "player_name": n, "position": "MF",
         "npxg_per_90": 0.3, "xa_per_90": 0.2, "minutes": 90, "matches_played": 5,
         "npxg_total": 1.5, "xa_total": 1.0, "avg_rating": 7.0}
        for i, n in enumerate([
            "Theo Hernández", "Aurélien Tchouaméni", "Dayot Upamecano",
            "Désiré Doué", "Jules Koundé", "Kylian Mbappé", "Manu Koné",
            "Maxence Lacroix", "Michael Olise", "Ousmane Dembélé",
        ])
    ]
    starters = [
        "T. Hernández", "Aurélien Tchouaméni", "Dayot Upamecano", "Désiré Doué",
        "Jules Koundé", "Kylian Mbappé", "Manu Koné", "Maxence Lacroix",
        "Michael Olise", "Ousmane Dembélé",
    ]
    allocs = compute_lineup_allocation(squad, starters, "France", match_xg=1.8)
    assert len(allocs) == 10
