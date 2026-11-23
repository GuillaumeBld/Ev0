"""Réglages communs à tous les tests."""
import pytest


@pytest.fixture(autouse=True)
def _memoire_alertes_vide():
    """Chaque test part sans mémoire des alertes déjà envoyées (team_xg)."""
    from app.pricing import team_xg

    team_xg._incidents_envoyes.clear()
    yield
    team_xg._incidents_envoyes.clear()
