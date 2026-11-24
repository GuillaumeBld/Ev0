"""Les books sont branchés sur toutes les compétitions de l'Atlas qu'ils proposent.

Audit du 24/11/2026 : 715 matchs de clubs sur 10 jours dans 63 compétitions ;
Ev0 n'en avait que 134 au calendrier (9 compétitions) et les books n'étaient
lus que sur le Big 5 et la Ligue des champions.
"""
from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion.betclic_grpc_scraper import BETCLIC_COMPETITION_IDS, BetclicGrpcScraper
from app.ingestion.book_competitions import BOOK_COMPETITIONS, book_key
from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY
from app.ingestion.odds_scheduler import _league_key
from app.ingestion.pmu_scraper import _fetch_events, _league_from_event, player_variant
from app.ingestion.unibet_lvs_scraper import LVS_NODE_IDS
from app.services.competitions import (
    SYNCED_COMPETITION_IDS,
    competition_name,
    league_key_for,
)


def _event(path, start="2030-11-30T19:45:00Z"):
    return {"path": [{"englishName": n} for n in path], "start": start, "id": 1}


@pytest.mark.parametrize("cid, betclic, unibet, pmu", [
    (18, 504, 58529410, ["Football", "USA", "MLS"]),                          # MLS
    (15, 27, 58530105, ["Football", "Switzerland", "Super League"]),           # Super League suisse
    (8, 3453, 58532492, ["Football", "Europa League"]),
    (83, 28946, 58554102, ["Football", "Conference League"]),
    (17, 4684, 58986505, ["Football", "Saudi Arabia", "Professional League"]),
    (12, 28, 58532448, ["Football", "England", "The Championship"]),
    (2, 32, 58530875, ["Football", "Portugal", "Primeira Liga"]),
])
def test_competition_branchee_chez_les_trois_books(cid, betclic, unibet, pmu):
    cle = league_key_for(cid)
    assert cid in SYNCED_COMPETITION_IDS
    assert _league_key(cle) == cle            # le planificateur déclenche les scrapers
    assert BETCLIC_COMPETITION_IDS[cle] == betclic
    assert LVS_NODE_IDS[cle] == unibet
    assert _league_from_event(_event(pmu)) == cle
    assert player_variant(cle) == "sans_sub"  # Garantie Coaching PMU : Big 5 + LDC seulement


def test_la_table_ne_contient_que_des_competitions_du_calendrier():
    for cid in BOOK_COMPETITIONS:
        assert cid in LEAGUE_REGISTRY and cid in SYNCED_COMPETITION_IDS, cid


def test_liga_mx_apertura_et_clausura_partagent_la_meme_cle():
    assert book_key(19) == book_key(20) == "comp_19"
    assert _league_key("comp_20") == "comp_19"


def test_coupe_proposee_par_un_seul_book():
    """Coupe de France : Betclic seul. Les autres books ne sont pas demandés pour rien."""
    assert "comp_44" in BETCLIC_COMPETITION_IDS
    assert "comp_44" not in LVS_NODE_IDS


def test_competition_sans_aucun_book_pas_de_cle():
    """League One : au calendrier (xG), mais aucun book ne la propose."""
    assert 86 in SYNCED_COMPETITION_IDS
    assert _league_key(league_key_for(86)) is None


@pytest.mark.parametrize("chemin, attendu", [
    (["Football", "England", "Premier League"], "premier_league"),
    (["Football", "Ukraine", "Premier League"], None),
    (["Football", "Wales", "Premier League"], None),
    (["Football", "Germany", "Bundesliga"], "bundesliga"),
    (["Football", "Austria", "Bundesliga"], "comp_96"),
    (["Football", "Germany", "2. Bundesliga"], "comp_94"),
    (["Football", "Italy", "Serie A"], "serie_a"),
    (["Football", "Italy", "Serie B"], "comp_100"),
    (["Football", "Brazil", "Brasileirao Serie A"], "comp_9"),
    (["Football", "Champions League"], "champions_league"),
    (["Football", "Georgia", "Erovnuli Liga"], None),
    (["Football", "UEFA Super Cup"], "uefa_super_cup"),
])
def test_pmu_le_pays_departage_les_championnats_homonymes(chemin, attendu):
    assert _league_from_event(_event(chemin)) == attendu


def test_noms_de_competition_sans_homonyme():
    """Deux « Bundesliga » et deux « Superliga » dans l'Atlas : leurs matchs
    se mélangeaient dans le calculateur."""
    assert competition_name(5) == "Bundesliga"
    assert competition_name(96) == "Bundesliga (Austria)"
    assert competition_name(84) != competition_name(23)
    noms = [competition_name(c) for c in LEAGUE_REGISTRY]
    assert len(noms) == len(set(noms))


# ── Lecture limitée aux matchs à rafraîchir ──────────────────────────────────

async def test_betclic_ne_lit_que_les_matchs_attendus(monkeypatch):
    ko = datetime(2030, 11, 30, 19, 45, tzinfo=UTC)
    matchs = [
        {"match_id": 1, "home_team": "A", "away_team": "B", "kickoff_utc": ko},
        {"match_id": 2, "home_team": "C", "away_team": "D", "kickoff_utc": ko + timedelta(hours=5)},
        {"match_id": 3, "home_team": "E", "away_team": "F", "kickoff_utc": ko + timedelta(days=7)},
    ]
    lus: list[int] = []

    async def liste(self, league):
        return matchs

    async def cotes(self, match_id, *a, **k):
        lus.append(match_id)
        return None

    monkeypatch.setattr(BetclicGrpcScraper, "fetch_competition_matches", liste)
    monkeypatch.setattr(BetclicGrpcScraper, "fetch_match_odds", cotes)
    monkeypatch.setattr("app.ingestion.betclic_grpc_scraper._MATCH_SLEEP", 0)
    await BetclicGrpcScraper(client=None).scrape_league("comp_18", [ko])
    assert lus == [1]
    lus.clear()
    await BetclicGrpcScraper(client=None).scrape_league("comp_18")
    assert lus == [1, 2, 3]


async def test_pmu_ne_lit_que_les_matchs_attendus():
    class Reponse:
        def raise_for_status(self): ...
        def json(self):
            return {"events": [
                {"event": {**_event(["Football", "USA", "MLS"], "2030-11-30T19:45:00Z"), "id": 1}},
                {"event": {**_event(["Football", "USA", "MLS"], "2030-12-07T19:45:00Z"), "id": 2}},
                {"event": {**_event(["Football", "Ukraine", "Premier League"], "2030-11-30T19:45:00Z"), "id": 3}},
            ]}

    class Client:
        async def get(self, *a, **k):
            return Reponse()

    ko = datetime(2030, 11, 30, 19, 45, tzinfo=UTC)
    tous = await _fetch_events(Client(), {"comp_18", "premier_league"})
    assert [e["id"] for e, _ in tous] == [1, 2]            # l'Ukraine n'est pas la Premier League
    dus = await _fetch_events(Client(), {"comp_18"}, {"comp_18": [ko]})
    assert [e["id"] for e, _ in dus] == [1]
