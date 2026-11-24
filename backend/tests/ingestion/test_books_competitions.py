"""Chaque compétition pricée (xG) est branchée chez Betclic, Unibet et PMU.

Relevé du 24/11/2026 : l'Europa League, la Conference League, la Saudi Pro
League et la Superliga roumaine étaient au calendrier avec leur xG, mais aucun
book n'y était lu.
"""
import pytest

from app.ingestion.betclic_grpc_scraper import BETCLIC_COMPETITION_IDS
from app.ingestion.odds_scheduler import _league_key
from app.ingestion.pmu_scraper import _league_from_event, player_variant
from app.ingestion.unibet_lvs_scraper import LVS_NODE_IDS
from app.services.competitions import SYNCED_COMPETITION_IDS, league_key_for

# identifiant Atlas -> (clé scraper, Betclic, Unibet, chemin PMU)
NOUVELLES = {
    8: ("europa_league", 3453, 58532492, ["Football", "Europa League"]),
    83: ("conference_league", 28946, 58554102, ["Football", "Conference League"]),
    17: ("saudi_pro_league", 4684, 58986505, ["Football", "Saudi Arabia", "Professional League"]),
    23: ("romania_superliga", 552, 58528328, ["Football", "Romania", "Liga I"]),
}


def _event(path):
    return {"path": [{"englishName": n} for n in path]}


@pytest.mark.parametrize("competition_api_id", sorted(NOUVELLES))
def test_competition_branchee_chez_les_trois_books(competition_api_id):
    cle, betclic, unibet, chemin_pmu = NOUVELLES[competition_api_id]
    assert competition_api_id in SYNCED_COMPETITION_IDS
    # Le match porte « comp_<id> » : le planificateur doit en tirer la clé des scrapers.
    assert _league_key(league_key_for(competition_api_id)) == cle
    assert BETCLIC_COMPETITION_IDS[cle] == betclic
    assert LVS_NODE_IDS[cle] == unibet
    assert _league_from_event(_event(chemin_pmu)) == cle
    # Garantie Coaching PMU : Big 5 et Ligue des champions seulement.
    assert player_variant(cle) == "sans_sub"


@pytest.mark.parametrize("competition_api_id", sorted(SYNCED_COMPETITION_IDS))
def test_toute_competition_du_calendrier_a_une_cle_de_scraper(competition_api_id):
    """Garde-fou : une compétition ajoutée au calendrier sans être branchée
    chez les books fait échouer ce test."""
    assert _league_key(league_key_for(competition_api_id)) is not None


@pytest.mark.parametrize("chemin, attendu", [
    (["Football", "England", "Premier League"], "premier_league"),
    (["Football", "Ukraine", "Premier League"], None),
    (["Football", "Wales", "Premier League"], None),
    (["Football", "Germany", "Bundesliga"], "bundesliga"),
    (["Football", "Austria", "Bundesliga"], None),
    (["Football", "Germany", "2. Bundesliga"], None),
    (["Football", "Italy", "Serie A"], "serie_a"),
    (["Football", "Italy", "Serie B"], None),
    (["Football", "Brazil", "Brasileirao Serie A"], None),
    (["Football", "Spain", "La Liga"], "la_liga"),
    (["Football", "Spain", "La Liga 2"], None),
    (["Football", "France", "Ligue 1"], "ligue_1"),
    (["Football", "Champions League"], "champions_league"),
    (["Football", "Copa Libertadores"], None),
])
def test_pmu_le_pays_departage_les_championnats_homonymes(chemin, attendu):
    assert _league_from_event(_event(chemin)) == attendu
