"""Quota quotidien Bzzoiro (7 500 appels par jour, constaté le 24/11/2026).

Une fois épuisé, l'API répond 429 « daily limit reached » jusqu'à minuit UTC.
Le client ne doit ni réessayer (155 s d'attente par appel) ni rappeler l'API.
"""
import httpx
import pytest

from app.ingestion.bzzoiro import client as c


@pytest.fixture(autouse=True)
def _etat_propre(monkeypatch):
    monkeypatch.setattr(c, "_quota_exhausted_until", None)
    monkeypatch.setattr(c, "_calls_day", None)
    c._calls_today.clear()
    monkeypatch.setattr("app.alerts.alert_bg", lambda *a, **k: None)


def _client(handler) -> c.BzzoiroClient:
    bz = c.BzzoiroClient("cle")
    bz._client = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler))
    return bz


async def test_quota_epuise_aucune_reprise_et_plus_aucun_appel():
    appels = []

    def handler(request):
        appels.append(request.url.path)
        return httpx.Response(429, json={"error": "daily limit reached", "code": "taster_exhausted"})

    bz = _client(handler)
    with pytest.raises(c.BzzoiroQuotaExhausted):
        await bz.get_page("/api/events/", {"league": 1})
    assert len(appels) == 1                    # pas de reprise
    with pytest.raises(c.BzzoiroQuotaExhausted):
        await bz.get_page("/api/v2/events/603182/incidents/")
    assert len(appels) == 1                    # l'API n'est plus rappelée
    assert c.quota_exhausted()


async def test_appels_comptes_par_famille():
    bz = _client(lambda request: httpx.Response(200, json={"results": []}))
    await bz.get_page("/api/events/", {"league": 1})
    await bz.get_page("/api/v2/events/603182/incidents/")
    await bz.get_page("/api/v2/events/7353/incidents/")
    assert c.calls_today() == {"events": 1, "events/incidents": 2}


def test_les_echecs_de_quota_ne_repartent_pas_en_alerte(monkeypatch):
    import logging

    from app.alerts import ErrorAlertHandler

    envoyes = []
    monkeypatch.setattr("app.alerts.alert_bg", lambda msg, channel: envoyes.append(msg))
    h = ErrorAlertHandler()
    h.emit(logging.LogRecord("app.worker", logging.ERROR, "", 0,
                             "Error in Bzzoiro events sync: quota quotidien Bzzoiro épuisé", None, None))
    assert envoyes == []
    h.emit(logging.LogRecord("app.worker", logging.ERROR, "", 0, "autre panne", None, None))
    assert len(envoyes) == 1


def test_incidents_seulement_pour_les_matchs_avec_cotes_ou_paris():
    import inspect

    from app.ingestion.bzzoiro import sync_incidents

    sql = inspect.getsource(sync_incidents.sync_incidents)
    assert "FROM player_odds_snapshots p WHERE p.fixture_id = f.id" in sql
    assert "FROM recommendations r WHERE r.fixture_id = f.id" in sql
