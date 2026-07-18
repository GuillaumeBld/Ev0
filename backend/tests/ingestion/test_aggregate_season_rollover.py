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
