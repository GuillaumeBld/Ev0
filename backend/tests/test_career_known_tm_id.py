"""Jalon 4/5 (contournement rate-limit Transfermarkt) : import carriere via
tm_id deja connu (`player_tm_ids`, rempli aux jalons squad/Wikidata).

`build_player_report_from_tm_id` doit sauter `search_player_candidates`
(schnellsuche, rate-limite et ambigu sur homonymes) et attaquer directement
l'API carriere (`ceapi`) + la page profil (validation de la date de
naissance). Le garde-fou anti-erreur (dob TM discordante -> refus) doit
rester actif meme sur ce chemin rapide.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.scripts.transfermarkt_career import BASE_URL, build_player_report_from_tm_id

EXPECTED = {
    "name": "Kylian Mbappe",
    "nationality": "FR",
    "dob": "1998-12-20",
    "club": "Real Madrid",
}

TM_ID = 342229


def _patch_target(name: str):
    return patch(f"app.scripts.transfermarkt_career.{name}")


def test_chemin_rapide_saute_la_recherche_et_appelle_profil_et_carriere():
    client = MagicMock()

    with _patch_target("fetch_profile_dob") as dob_mock, \
         _patch_target("fetch_career_games") as games_mock, \
         _patch_target("aggregate_by_season_and_competition") as agg_mock, \
         _patch_target("search_player_candidates") as search_mock:
        dob_mock.return_value = EXPECTED["dob"]
        games_mock.return_value = [{"gameInformation": {}}, {"gameInformation": {}}]
        agg_mock.return_value = [{"season": "24/25", "appearances": 10}]

        report = build_player_report_from_tm_id(client, EXPECTED, TM_ID)

    search_mock.assert_not_called()
    dob_mock.assert_called_once()
    games_mock.assert_called_once_with(client, str(TM_ID))
    agg_mock.assert_called_once()

    assert report["matched"] is True
    assert report["tm_id"] == TM_ID
    assert report["match_confidence"] == "known_tm_id"
    assert report["reason"] is None
    assert report["profile_url"] == f"{BASE_URL}/x/profil/spieler/{TM_ID}"
    assert report["dob_transfermarkt"] == EXPECTED["dob"]
    assert report["total_games_fetched"] == 2
    assert report["seasons"] == [{"season": "24/25", "appearances": 10}]

    # Meme structure de cles que `build_player_report`.
    assert set(report.keys()) == {
        "input_name", "input_nationality", "input_dob", "input_club",
        "matched", "tm_id", "matched_name", "profile_url", "dob_transfermarkt",
        "match_confidence", "reason", "candidates_checked",
        "total_games_fetched", "seasons",
    }


def test_dob_tm_discordante_refuse_le_tm_id_sans_toucher_la_carriere():
    client = MagicMock()

    with _patch_target("fetch_profile_dob") as dob_mock, \
         _patch_target("fetch_career_games") as games_mock, \
         _patch_target("aggregate_by_season_and_competition") as agg_mock, \
         _patch_target("search_player_candidates") as search_mock:
        dob_mock.return_value = "1990-01-01"  # != EXPECTED["dob"]

        report = build_player_report_from_tm_id(client, EXPECTED, TM_ID)

    search_mock.assert_not_called()
    games_mock.assert_not_called()
    agg_mock.assert_not_called()

    assert report["matched"] is False
    assert report["seasons"] == []
    assert report["total_games_fetched"] == 0
    assert report["reason"] == "dob TM discordante (fast-path tm_id)"
    assert report["dob_transfermarkt"] == "1990-01-01"


def test_dob_tm_absente_est_acceptee_source_deja_verifiee():
    """Si TM n'expose pas de date (profil minimal), on accepte : le tm_id
    vient d'une source deja validee en amont (squad roster ou Wikidata
    dob-valide), pas d'une recherche par nom non verifiee."""
    client = MagicMock()

    with _patch_target("fetch_profile_dob") as dob_mock, \
         _patch_target("fetch_career_games") as games_mock, \
         _patch_target("aggregate_by_season_and_competition") as agg_mock, \
         _patch_target("search_player_candidates") as search_mock:
        dob_mock.return_value = None
        games_mock.return_value = [{"gameInformation": {}}]
        agg_mock.return_value = [{"season": "23/24", "appearances": 5}]

        report = build_player_report_from_tm_id(client, EXPECTED, TM_ID)

    search_mock.assert_not_called()
    assert report["matched"] is True
    assert report["match_confidence"] == "known_tm_id"
    assert report["dob_transfermarkt"] is None
    assert report["total_games_fetched"] == 1
    assert report["seasons"] == [{"season": "23/24", "appearances": 5}]
