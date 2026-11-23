"""Le reste du serveur raisonne par competition Atlas (competition_api_id)."""
import re
from pathlib import Path

import pytest

from app.notifications import _format_movement
from app.services.competitions import (
    WC2026_COMPETITION_API_ID,
    is_international,
    is_wc2026,
)

APP = Path(__file__).resolve().parents[1] / "app"

FICHIERS_SANS_ETIQUETTE_CODEE_EN_DUR = [
    "backtest/simulator.py",
    "api/sanctuary.py",
    "api/history.py",
    "api/fixtures.py",
    "api/lineups.py",
    "api/recommendations.py",
    "ingestion/bzzoiro/sync_bzzoiro_lineups.py",
    "scripts/backfill_substitutions.py",
    "pricing/team_xg.py",
    "pricing/beta_pricing.py",
    "services/player_identity.py",
    "notifications.py",
]

_SLUG = re.compile(
    r"""["'](premier_league|ligue_1|bundesliga|la_liga|serie_a|champions_league|"""
    r"""world_cup_2026|friendly_international|nations_league_\w+|uefa_super_cup)["']"""
)


def _mouvement(**extra):
    base = {
        "kind": "new", "player": "X", "fixture": "A vs B", "kickoff": "20:00",
        "bet_type": "anytime_scorer", "odds": 2.0, "previous_odds": None,
        "bookmaker": "betclic", "edge": 0.1, "previous_edge": None,
    }
    base.update(extra)
    return base


def test_telegram_affiche_le_nom_de_la_competition():
    msg = _format_movement(_mouvement(competition_api_id=8, league="comp_8"))
    assert "Europa League" in msg
    assert "comp_8" not in msg


def test_telegram_repli_sur_etiquette_si_nom_inconnu():
    msg = _format_movement(_mouvement(competition_api_id=999999, league="comp_999999"))
    assert "comp_999999" in msg


def test_telegram_sans_competition_ne_plante_pas():
    assert "A vs B" in _format_movement(_mouvement())


def test_is_international():
    assert is_international(WC2026_COMPETITION_API_ID)
    for cid in (27, 31, 64, 65):
        assert is_international(cid)
    # championnats, C1 et Supercoupe d'UEFA : des clubs
    for cid in (1, 3, 4, 5, 6, 7, 90, None):
        assert not is_international(cid)


def test_is_wc2026():
    assert is_wc2026(27) and not is_wc2026(31) and not is_wc2026(None)


@pytest.mark.parametrize("chemin", FICHIERS_SANS_ETIQUETTE_CODEE_EN_DUR)
def test_aucune_etiquette_de_competition_codee_en_dur(chemin):
    lignes = (APP / chemin).read_text().splitlines()
    trouvees = [
        (i + 1, l.strip()) for i, l in enumerate(lignes)
        if not l.lstrip().startswith("#") and _SLUG.search(l)

    ]
    assert not trouvees, f"{chemin} : {trouvees}"


def test_les_filtres_acceptent_competition_api_id():
    for chemin, marque in [
        ("api/recommendations.py", "competition_api_id"),
        ("api/sanctuary.py", "competition_api_id"),
        ("api/fixtures.py", "competition_api_id"),
        ("backtest/simulator.py", "competition_api_id"),
    ]:
        assert marque in (APP / chemin).read_text(), chemin


# ── Matchs sans identifiant : l'etiquette historique garde son sens ──────────

from types import SimpleNamespace  # noqa: E402

from app.services.competitions import (  # noqa: E402
    clubs_only_clause,
    fixture_competition_id,
    is_international_fixture,
    is_wc2026_fixture,
)


def _fx(cid, league):
    return SimpleNamespace(competition_api_id=cid, league=league)


def test_match_sans_identifiant_retombe_sur_son_etiquette():
    assert is_international_fixture(_fx(None, "world_cup_2026"))
    assert is_wc2026_fixture(_fx(None, "world_cup_2026"))
    assert is_international_fixture(_fx(None, "nations_league_uefa"))
    assert not is_wc2026_fixture(_fx(None, "nations_league_uefa"))
    assert not is_international_fixture(_fx(None, "ligue_1"))
    assert not is_international_fixture(_fx(None, "uefa_super_cup"))
    assert not is_international_fixture(_fx(None, None))


def test_l_identifiant_prime_sur_l_etiquette():
    assert not is_international_fixture(_fx(6, "world_cup_2026"))
    assert fixture_competition_id(_fx(8, "x")) == 8


def test_clubs_only_exclut_les_selections_meme_sans_identifiant():
    from sqlalchemy import column
    sql = str(clubs_only_clause(column("league"), column("competition_api_id")).compile(
        compile_kwargs={"literal_binds": True}))
    assert "nations_league_uefa" in sql and "world_cup_2026" in sql
    assert "competition_api_id IS NULL" in sql and "NOT IN" in sql
    assert "uefa_super_cup" not in sql
