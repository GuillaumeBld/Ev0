"""Liste ANJ : ce que les books français ont le droit de proposer."""
from app.ingestion.book_competitions import (
    ANJ_NON_AUTORISEES,
    ANJ_PHASES_AUTORISEES,
    BOOK_COMPETITIONS,
    anj_status,
)
from app.ingestion.bzzoiro.league_registry import LEAGUE_REGISTRY
from app.services.competitions import SYNCED_COMPETITION_IDS


def test_les_competitions_citees_existent_dans_l_atlas():
    for cid in ANJ_NON_AUTORISEES | set(ANJ_PHASES_AUTORISEES):
        assert cid in LEAGUE_REGISTRY, cid
    assert not ANJ_NON_AUTORISEES & set(ANJ_PHASES_AUTORISEES)


def test_aucun_book_branche_sur_une_competition_non_autorisee():
    """Si un book propose une compétition « non autorisée », la liste ANJ a
    changé ou notre lecture est fausse : à revoir, pas à ignorer."""
    assert not ANJ_NON_AUTORISEES & set(BOOK_COMPETITIONS)


def test_non_autorisee_reste_au_calendrier_pour_le_xg():
    assert ANJ_NON_AUTORISEES <= SYNCED_COMPETITION_IDS


def test_statuts():
    assert anj_status(86) == "non autorisée"     # League One
    assert anj_status(44) == "restreinte"        # Coupe de France, à partir des 32es
    assert anj_status(18) == "autorisée"         # MLS
    assert anj_status(15) == "autorisée"         # Super League suisse


def test_competitions_autorisees_pas_encore_branchees():
    """Autorisées par l'ANJ mais sans adresse relevée chez un book : à relever
    quand elles sont à l'affiche (saison terminée ou tour pas encore ouvert)."""
    clubs = {c for c, m in LEAGUE_REGISTRY.items()
             if m.kind in ("domestic_1", "domestic_2", "uefa_club", "world", "cup")}
    historiques = {1, 3, 4, 5, 6, 7, 90}
    a_relever = clubs - ANJ_NON_AUTORISEES - set(BOOK_COMPETITIONS) - historiques
    noms = sorted(LEAGUE_REGISTRY[c].name for c in a_relever)
    assert noms == sorted([
        "Veikkausliiga", "Chinese Super League", "Copa Sudamericana", "Copa del Rey",
        "Taça de Portugal", "Taça da Liga", "Ligue des champions CAF", "Ligue 3",
    ]), noms
