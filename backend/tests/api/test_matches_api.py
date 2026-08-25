"""Fiche match : lecture de la base, jamais de l'API.

Contrairement a get_match_detail de la CDM qui interroge Bzzoiro en direct
quand le cache est vide, la fiche club montre ce qui est archive — comme le
Sanctuaire. Si l'archive est incomplete, elle le dit.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.matches import get_match_detail


def _event(**kw):
    e = MagicMock()
    e.api_id = 209544
    e.home_team, e.away_team = "Fulham", "Chelsea"
    e.home_team_api_id, e.away_team_api_id = 102, 13
    e.home_score, e.away_score = 2, 3
    e.home_score_ht, e.away_score_ht = 1, 1
    e.home_xg, e.away_xg = 1.4, 2.1
    e.status, e.event_date = "finished", None
    e.league_api_id, e.round_number = 1, 3
    e.shotmap = [{"pos": {"x": 26.6, "y": 41.8}, "xg": 0.03, "type": "save",
                  "body": "right-foot", "sit": "regular", "home": True, "min": 90}]
    e.incidents = [{"type": "goal", "minute": 12}]
    e.momentum = [1, 2, 3]
    e.average_positions = []
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def _session(event, compos=(), stats=(), noms=None):
    """noms : {bzz_team_id: nom} rendus par canonical_teams."""
    s = MagicMock()
    noms = {102: "Fulham", 13: "Chelsea"} if noms is None else noms

    async def _execute(stmt):
        r = MagicMock()
        r.scalar_one_or_none.return_value = event
        texte = str(stmt)
        if "canonical_teams" in texte:
            r.all.return_value = list(noms.items())
            r.scalars.return_value.all.return_value = []
        elif "team_lineups" in texte:
            r.scalars.return_value.all.return_value = list(compos)
            r.all.return_value = []
        else:
            r.scalars.return_value.all.return_value = list(stats)
            r.all.return_value = []
        return r

    s.execute = AsyncMock(side_effect=_execute)
    return s


async def test_fiche_rend_les_blocs_presents():
    d = await get_match_detail(209544, session=_session(_event()))

    assert d["home_team"] == "Fulham"
    assert d["away_team"] == "Chelsea"
    assert d["home_score"] == 2
    assert len(d["shotmap"]) == 1
    assert len(d["incidents"]) == 1


async def test_match_inconnu_rend_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await get_match_detail(1, session=_session(None))
    assert exc.value.status_code == 404


async def test_bloc_absent_est_signale_pas_masque():
    """Une carte des tirs absente doit se voir, pas se confondre avec zero tir."""
    d = await get_match_detail(209544, session=_session(_event(shotmap=None)))

    assert d["shotmap"] == []
    assert "shotmap" in d["blocs_manquants"]


async def test_bloc_present_n_est_pas_signale_manquant():
    d = await get_match_detail(209544, session=_session(_event()))
    assert "shotmap" not in d["blocs_manquants"]
    assert "incidents" not in d["blocs_manquants"]


async def test_la_fiche_n_appelle_jamais_l_api():
    """Elle montre l'archive : aucun client Bzzoiro ne doit apparaitre."""
    import inspect

    import app.api.matches as mod

    src = inspect.getsource(mod)
    assert "BzzoiroClient" not in src
    assert "/api/v2/" not in src


async def test_compo_porte_son_statut():
    """Pricer sur la derniere compo connue n'est pas pricer sur celle du jour."""
    compo = MagicMock()
    compo.team = "Fulham"
    compo.lineup_type = "official"
    compo.lineup_status = "confirmed"
    compo.published_at = None
    compo.players = []

    d = await get_match_detail(209544, session=_session(_event(), compos=[compo]))

    assert d["home_lineup"]["lineup_type"] == "official"
    assert d["home_lineup"]["lineup_status"] == "confirmed"


async def test_carte_des_tirs_aplatie_pour_le_composant():
    """Bzzoiro rend pos:{x,y} et home ; le composant attend x, y, is_home."""
    d = await get_match_detail(209544, session=_session(_event()))

    tir = d["shotmap"][0]
    assert tir["x"] == 26.6 and tir["y"] == 41.8
    assert tir["is_home"] is True
    assert tir["minute"] == 90
    assert "pos" not in tir
