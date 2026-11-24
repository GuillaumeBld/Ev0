"""La competition d'un match vient de l'Atlas (id interne Bzzoiro)."""
from __future__ import annotations

import inspect

from app.ingestion.bzzoiro import sync_fixtures_from_bzz as sync_mod
from app.services.competitions import (
    SYNCED_COMPETITION_IDS,
    competition_name,
    league_key_for,
)


def test_etiquettes_historiques():
    assert league_key_for(1) == "premier_league"
    assert league_key_for(7) == "champions_league"
    assert league_key_for(27) == "world_cup_2026"


def test_ids_internes_pieges_ne_sont_plus_des_championnats():
    assert league_key_for(8) == "comp_8"
    assert league_key_for(17) == "comp_17"
    assert league_key_for(23) == "comp_23"


def test_noms():
    assert competition_name(8) == "Europa League"
    assert competition_name(17) == "Saudi Pro League"
    assert competition_name(999999) is None
    assert competition_name(None) is None


def test_perimetre_de_synchro():
    # Toutes les compétitions de clubs de l'Atlas + les sélections historiques.
    from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY

    assert {1, 3, 4, 5, 6, 7, 27, 31, 64, 65, 90, 8, 83} <= SYNCED_COMPETITION_IDS
    # MLS, Super League suisse, Saudi, Roumanie, Championship, Coupe de France.
    assert {18, 15, 17, 23, 12, 44} <= SYNCED_COMPETITION_IDS
    # Les championnats féminins restent hors calendrier.
    assert not [c for c in SYNCED_COMPETITION_IDS if LEAGUE_REGISTRY[c].kind == "women"]
    assert league_key_for(18) == "comp_18"
    assert league_key_for(83) == "comp_83"
    assert competition_name(83) == "Conference League"


def test_synchro_utilise_l_atlas():
    src = inspect.getsource(sync_mod)
    assert "_LEAGUE_API_ID_TO_KEY" not in src
    corps = inspect.getsource(sync_mod.sync_fixtures_from_bzz)
    # branche "match existant" : avant le `else:` de creation
    existant, creation = corps.split("# Create new Fixture")
    for branche in (existant, creation):
        assert "competition_api_id" in branche
        assert "league_key" in branche
