"""La saison de l'agrégation n'est plus codée en dur — elle vient du season_service."""

import inspect

from app.ingestion.bzzoiro import aggregate


def test_aggregate_all_leagues_sans_saison_par_defaut_en_dur():
    """Le défaut doit être None (résolu via current_season), plus "2025-2026"."""
    sig = inspect.signature(aggregate.aggregate_all_leagues)
    assert sig.parameters["season"].default is None


def test_constante_season_start_date_supprimee():
    from app.ingestion.bzzoiro import constants
    assert not hasattr(constants, "SEASON_START_DATE")


def test_agregation_bornee_des_deux_cotes():
    """Sans borne haute, agreger une saison passee ramasse les suivantes.

    Inoffensif tant que la base ne portait que la saison courante ; faux des
    que l'historique 5 saisons y a ete verse (21/08/2026). Les deux requetes
    du module -- totaux et forme sur 5 matchs -- doivent etre bornees.
    """
    from pathlib import Path

    src = Path("app/ingestion/bzzoiro/aggregate.py").read_text()
    assert src.count("BzzEvent.event_date >= cutoff_date") == 2
    assert src.count("BzzEvent.event_date < cutoff_end") == 2
    assert "cutoff_end = season_end_of(season)" in src
