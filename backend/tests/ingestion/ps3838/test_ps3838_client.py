import json
from datetime import UTC, datetime
from pathlib import Path

from app.ingestion.ps3838 import client as ps3838_client
from app.ingestion.ps3838.client import Ps3838Event, fetch_events, parse_events

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "ps3838_events.json"


def _payload():
    return json.loads(FIXTURE.read_text())


def test_parse_returns_events():
    evs = parse_events(_payload())
    assert len(evs) > 50
    assert all(isinstance(e, Ps3838Event) for e in evs)


def test_event_fields_are_typed():
    e = next(e for e in parse_events(_payload()) if e.h2h)
    assert isinstance(e.event_id, int)
    assert isinstance(e.home, str) and e.home
    assert isinstance(e.away, str) and e.away
    assert isinstance(e.kickoff_utc, datetime)
    assert e.kickoff_utc.tzinfo is not None


def test_h2h_order_is_away_home_draw():
    """PS3838 range le 1X2 en [exterieur, domicile, nul]. Inverser dom/ext est
    exactement la classe de bug que ce chantier elimine."""
    raw = ["7.130", "1.386", "5.110"]
    from app.ingestion.ps3838.client import _parse_h2h

    assert _parse_h2h(raw) == {"home": 1.386, "draw": 5.11, "away": 7.13}


def test_h2h_missing_or_empty_returns_none():
    from app.ingestion.ps3838.client import _parse_h2h

    assert _parse_h2h(None) is None
    assert _parse_h2h(["", "", None]) is None
    assert _parse_h2h(["1.5"]) is None


def test_totals_uses_line_closest_to_2_5_and_ignores_quarter_lines():
    from app.ingestion.ps3838.client import _parse_totals

    raw = [["3-3.5", 3.25, "2.090", "1.793"], ["3.0", 3.0, "1.854", "2.040"]]
    line, totals = _parse_totals(raw)
    assert line == 3.0
    assert totals == {"over_3.0": 1.854, "under_3.0": 2.04}


def test_totals_prefers_half_integer_when_available():
    from app.ingestion.ps3838.client import _parse_totals

    raw = [["3.0", 3.0, "1.85", "2.04"], ["2.5", 2.5, "1.60", "2.35"]]
    line, totals = _parse_totals(raw)
    assert line == 2.5
    assert totals == {"over_2.5": 1.6, "under_2.5": 2.35}


def test_totals_empty_returns_none():
    from app.ingestion.ps3838.client import _parse_totals

    assert _parse_totals([]) == (None, None)
    assert _parse_totals(None) == (None, None)


def test_event_ids_are_unique():
    evs = parse_events(_payload())
    ids = [e.event_id for e in evs]
    assert len(ids) == len(set(ids))


# --- fetch_events : fusion des deux flux, sans reseau ---------------------
#
# httpx.AsyncClient est remplace par un faux client dont .get() renvoie une
# reponse (ou leve une exception) selon les parametres de requete recus.
# parse_events est remplace par une simple table de correspondance tag ->
# evenements, pour ne pas dependre de la structure reelle du payload : elle
# est deja couverte par les tests de parse_events ci-dessus.


def _event(event_id: int, home_odds: float) -> Ps3838Event:
    return Ps3838Event(
        event_id=event_id,
        home="Home",
        away="Away",
        kickoff_utc=datetime(2026, 1, 1, tzinfo=UTC),
        league="Test League",
        h2h={"home": home_odds, "draw": 3.0, "away": 3.0},
    )


def _params_key(params: dict) -> tuple:
    return tuple(sorted(params.items()))


class _FakeResponse:
    def __init__(self, tag):
        self._tag = tag

    def raise_for_status(self):
        pass

    def json(self):
        return self._tag


class _FakeAsyncClient:
    """Remplace httpx.AsyncClient : .get() rejoue une reponse ou une
    exception preparee par le test, sans toucher au reseau."""

    def __init__(self, responses: dict, **_kwargs):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    async def get(self, _url, params=None):
        outcome = self._responses[_params_key(params)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _patch_fetch(monkeypatch, responses: dict, tag_to_events: dict):
    monkeypatch.setattr(
        ps3838_client.httpx,
        "AsyncClient",
        lambda **kw: _FakeAsyncClient(responses, **kw),
    )
    monkeypatch.setattr(
        ps3838_client, "parse_events", lambda payload: tag_to_events.get(payload, [])
    )


async def test_fetch_events_merges_distinct_events(monkeypatch):
    _patch_fetch(
        monkeypatch,
        responses={
            _params_key(ps3838_client._QUERY_IMMINENT): _FakeResponse("imminent"),
            _params_key(ps3838_client._QUERY_UPCOMING): _FakeResponse("upcoming"),
        },
        tag_to_events={
            "imminent": [_event(1, 1.5)],
            "upcoming": [_event(2, 2.5)],
        },
    )

    evs = await fetch_events()

    assert {e.event_id for e in evs} == {1, 2}


async def test_fetch_events_imminent_wins_on_duplicate(monkeypatch):
    """Meme event_id present dans les deux flux, avec des cotes differentes :
    c'est la version du flux imminent (sp=29 seul) qui doit gagner."""
    _patch_fetch(
        monkeypatch,
        responses={
            _params_key(ps3838_client._QUERY_IMMINENT): _FakeResponse("imminent"),
            _params_key(ps3838_client._QUERY_UPCOMING): _FakeResponse("upcoming"),
        },
        tag_to_events={
            "imminent": [_event(1, 1.5)],
            "upcoming": [_event(1, 9.9)],
        },
    )

    evs = await fetch_events()

    assert len(evs) == 1
    assert evs[0].h2h["home"] == 1.5


async def test_fetch_events_tolerates_one_call_failure(monkeypatch):
    """L'appel 'upcoming' echoue : les evenements du flux 'imminent' sont
    quand meme retournes, sans exception propagee."""
    _patch_fetch(
        monkeypatch,
        responses={
            _params_key(ps3838_client._QUERY_IMMINENT): _FakeResponse("imminent"),
            _params_key(ps3838_client._QUERY_UPCOMING): RuntimeError("boom"),
        },
        tag_to_events={"imminent": [_event(1, 1.5)]},
    )

    evs = await fetch_events()

    assert [e.event_id for e in evs] == [1]


async def test_fetch_events_both_calls_fail_returns_empty(monkeypatch):
    _patch_fetch(
        monkeypatch,
        responses={
            _params_key(ps3838_client._QUERY_IMMINENT): RuntimeError("boom1"),
            _params_key(ps3838_client._QUERY_UPCOMING): RuntimeError("boom2"),
        },
        tag_to_events={},
    )

    evs = await fetch_events()

    assert evs == []
