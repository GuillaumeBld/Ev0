"""Le selecteur d'effectif resout par identifiant, jamais par nom de club.

bzz_teams.name et bzz_players.current_team_name relevent d'un espace
d'identifiants different de current_team_api_id : les joueurs du Barca y
portent current_team_name = "Saint George". Toute resolution par nom stocke
est donc structurellement fausse.
"""
from unittest.mock import AsyncMock, MagicMock

from app.api.lineups import resolve_team_bzz_id

# (name_fr, name_en, bzz_team_id) — extrait reel de canonical_teams
_CANONIQUES = [
    ("Inter Milan", "Internazionale", 2697),
    ("Barcelone", "Barcelona", 2817),
    ("Séville", "Sevilla", 77903),
    ("Paris Saint-Germain", "Paris Saint-Germain", 114),
    ("Mönchengladbach", "Borussia Mönchengladbach", 2527),
]


def _session_avec_canoniques(lignes=None):
    session = MagicMock()
    result = MagicMock()
    result.all.return_value = _CANONIQUES if lignes is None else lignes
    session.execute = AsyncMock(return_value=result)
    return session


async def test_resolve_trouve_le_club_par_nom_francais():
    assert await resolve_team_bzz_id("Inter Milan", _session_avec_canoniques()) == 2697


async def test_resolve_bascule_sur_le_nom_anglais():
    """name_fr ne matche pas, name_en oui."""
    assert await resolve_team_bzz_id("Barcelona", _session_avec_canoniques()) == 2817


async def test_resolve_ignore_les_accents():
    assert await resolve_team_bzz_id("Seville", _session_avec_canoniques()) == 77903
    assert await resolve_team_bzz_id("Monchengladbach", _session_avec_canoniques()) == 2527


async def test_resolve_gere_la_ponctuation():
    """_fold remplace le tiret par une espace : les deux graphies se valent.

    C'est le cas qui casserait avec une comparaison SQL lower(unaccent(...)),
    qui conserverait le tiret.
    """
    for graphie in ("Paris Saint-Germain", "Paris Saint Germain", "paris saint-germain"):
        assert await resolve_team_bzz_id(graphie, _session_avec_canoniques()) == 114


async def test_resolve_rend_none_si_club_inconnu():
    assert await resolve_team_bzz_id("Club Inexistant", _session_avec_canoniques()) is None


async def test_resolve_rend_none_sur_nom_vide():
    assert await resolve_team_bzz_id("", _session_avec_canoniques()) is None


async def test_resolve_supporte_un_name_en_absent():
    """Plusieurs lignes de canonical_teams ont name_en a NULL."""
    session = _session_avec_canoniques([("Juventus", None, 2687)])
    assert await resolve_team_bzz_id("Juventus", session) == 2687
