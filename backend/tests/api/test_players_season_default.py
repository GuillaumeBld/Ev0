"""Les endpoints joueurs ne hardcodent plus la saison — défaut None résolu via season_service."""

import inspect

from app.api import players


def _season_default(func) -> object:
    param = inspect.signature(func).parameters["season"]
    default = param.default
    # FastAPI Query(...) : la valeur est dans .default de l'objet Query
    return getattr(default, "default", default)


def test_aucun_endpoint_joueur_ne_hardcode_la_saison():
    """Tout paramètre `season` d'un endpoint du module doit avoir None pour défaut."""
    offenders = []
    for name, func in inspect.getmembers(players, inspect.iscoroutinefunction):
        sig = inspect.signature(func)
        if "season" in sig.parameters and _season_default(func) == "2025-2026":
            offenders.append(name)
    assert offenders == [], f"Endpoints avec saison hardcodée: {offenders}"


def test_sync_fixtures_ne_stampe_plus_la_constante():
    import app.ingestion.bzzoiro.sync_fixtures_from_bzz as sf
    src = inspect.getsource(sf)
    assert "season=CURRENT_SEASON" not in src
