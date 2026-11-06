"""Un 404 sur un match recent ne doit pas condamner la fixture.

Regression du 06/11/2026.

L'endpoint incidents renvoie 404 dans DEUX situations que la reponse ne
distingue pas : l'evenement a ete supprime chez Bzzoiro (definitif) et il
n'est pas encore publie (transitoire). La synchro tournant peu apres la fin
des matchs, le second cas est le plus courant -- et il etait traite comme le
premier : sentinelle `events_unavailable`, fixture ecartee pour toujours.

Degats constates en production :

    1 030 matchs marques definitivement indisponibles
    plus un seul but ingere depuis aout 2026
    8 514 paris annules sur 9 903 faute de verite terrain (86 %)

Et a la verification, sur 20 matchs marques tires au hasard, 20 avaient leurs
incidents disponibles. La sentinelle etait fausse dans tous les cas testes.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.ingestion.bzzoiro.sync_incidents import (
    DELAI_AVANT_404_DEFINITIF,
    SENTINEL_UNAVAILABLE_TYPE,
    sync_incidents,
)


def _fixture(fid=1, heures_depuis_le_match=2.0):
    f = MagicMock()
    f.id = fid
    f.external_id = f"bzz_{1000 + fid}"
    f.kickoff_utc = datetime.now(UTC) - timedelta(hours=heures_depuis_le_match)
    f.home_team, f.away_team = "A", "B"
    f.home_score, f.away_score = 2, 1
    return f


def _session(fixtures):
    """Session qui rend les ids puis les objets Fixture."""
    appels = {"n": 0}

    async def execute(stmt, params=None, *a, **k):
        appels["n"] += 1
        res = MagicMock()
        if appels["n"] == 1:
            res.all.return_value = [(f.id,) for f in fixtures]
        else:
            scal = MagicMock()
            scal.all.return_value = fixtures
            res.scalars.return_value = scal
            res.scalar_one.return_value = 0
        return res

    s = MagicMock()
    s.execute = AsyncMock(side_effect=execute)
    s.commit = AsyncMock()
    s.rollback = AsyncMock()
    return s


def _client_404():
    c = MagicMock()
    reponse = httpx.Response(404, request=httpx.Request("GET", "https://x/incidents/"))
    c.get_page = AsyncMock(
        side_effect=httpx.HTTPStatusError("404", request=reponse.request, response=reponse)
    )
    return c


@pytest.mark.asyncio
async def test_404_sur_match_recent_ne_pose_pas_de_sentinelle():
    """Le coeur de la regression : un match termine il y a 2 h qui renvoie 404
    doit etre repris, pas condamne."""
    f = _fixture(heures_depuis_le_match=2.0)
    s = _session([f])

    with pytest.MonkeyPatch.context() as mp:
        ecrits = []
        async def _store(session, fixture_id, events):
            ecrits.extend(events)
            return len(events)
        mp.setattr("app.ingestion.bzzoiro.sync_incidents._store_events", _store)
        await sync_incidents(s, _client_404(), limit=10)

    types = [e["event_type"] for e in ecrits]
    assert SENTINEL_UNAVAILABLE_TYPE not in types, (
        "une sentinelle a ete posee sur un match recent : la fixture est perdue"
    )


@pytest.mark.asyncio
async def test_404_persistant_pose_bien_la_sentinelle():
    """Au-dela du delai, un 404 est tenu pour definitif -- sinon la fixture
    serait reprise indefiniment a chaque passage."""
    heures = DELAI_AVANT_404_DEFINITIF.total_seconds() / 3600 + 1
    f = _fixture(heures_depuis_le_match=heures)
    s = _session([f])

    with pytest.MonkeyPatch.context() as mp:
        ecrits = []
        async def _store(session, fixture_id, events):
            ecrits.extend(events)
            return len(events)
        mp.setattr("app.ingestion.bzzoiro.sync_incidents._store_events", _store)
        await sync_incidents(s, _client_404(), limit=10)

    types = [e["event_type"] for e in ecrits]
    assert SENTINEL_UNAVAILABLE_TYPE in types


@pytest.mark.asyncio
async def test_404_sans_date_de_coup_denvoi_pose_la_sentinelle():
    """Sans date exploitable, impossible de juger si le 404 est transitoire :
    on tranche pour le definitif plutot que de boucler sans fin."""
    f = _fixture()
    f.kickoff_utc = None
    s = _session([f])

    with pytest.MonkeyPatch.context() as mp:
        ecrits = []
        async def _store(session, fixture_id, events):
            ecrits.extend(events)
            return len(events)
        mp.setattr("app.ingestion.bzzoiro.sync_incidents._store_events", _store)
        await sync_incidents(s, _client_404(), limit=10)

    types = [e["event_type"] for e in ecrits]
    assert SENTINEL_UNAVAILABLE_TYPE in types


@pytest.mark.asyncio
async def test_la_frontiere_du_delai():
    """Juste avant le delai : repris. Juste apres : condamne."""
    heures_limite = DELAI_AVANT_404_DEFINITIF.total_seconds() / 3600

    for heures, sentinelle_attendue in ((heures_limite - 1, False), (heures_limite + 1, True)):
        s = _session([_fixture(heures_depuis_le_match=heures)])
        with pytest.MonkeyPatch.context() as mp:
            ecrits = []
            async def _store(session, fixture_id, events):
                ecrits.extend(events)
                return len(events)
            mp.setattr("app.ingestion.bzzoiro.sync_incidents._store_events", _store)
            await sync_incidents(s, _client_404(), limit=10)
        pose = SENTINEL_UNAVAILABLE_TYPE in [e["event_type"] for e in ecrits]
        assert pose is sentinelle_attendue, f"a {heures:.0f} h du match"
