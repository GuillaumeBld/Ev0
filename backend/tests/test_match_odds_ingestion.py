import pytest
from app.ingestion.match_odds import parse_match_odds_event

_SAMPLE_EVENT = {
    "id": "event_123",
    "home_team": "Paris Saint-Germain",
    "away_team": "Olympique Lyonnais",
    "bookmakers": [
        {
            "key": "betfair",
            "markets": [
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": "Paris Saint-Germain", "price": 1.55},
                        {"name": "Draw", "price": 4.10},
                        {"name": "Olympique Lyonnais", "price": 6.00},
                    ],
                },
                {
                    "key": "totals",
                    "outcomes": [
                        {"name": "Over", "point": 2.5, "price": 1.85},
                        {"name": "Under", "point": 2.5, "price": 2.00},
                    ],
                },
                {
                    "key": "both_teams_to_score",
                    "outcomes": [
                        {"name": "Yes", "price": 1.92},
                        {"name": "No", "price": 1.90},
                    ],
                },
            ],
        }
    ],
}


def test_match_odds_snapshot_model_importable():
    from app.models.match_odds import MatchOddsSnapshot
    snap = MatchOddsSnapshot(
        fixture_id=1,
        bookmaker="betfair",
        market_type="totals",
        outcome="over_2.5",
        odds=1.85,
    )
    assert snap.bookmaker == "betfair"
    assert snap.market_type == "totals"
    assert snap.outcome == "over_2.5"
    assert snap.odds == 1.85


def test_parse_match_odds_h2h():
    rows = parse_match_odds_event(_SAMPLE_EVENT)
    h2h = [r for r in rows if r["market_type"] == "h2h"]
    assert len(h2h) == 3
    outcomes = {r["outcome"]: r["odds"] for r in h2h}
    assert outcomes["home"] == pytest.approx(1.55)
    assert outcomes["draw"] == pytest.approx(4.10)
    assert outcomes["away"] == pytest.approx(6.00)


def test_parse_match_odds_totals():
    rows = parse_match_odds_event(_SAMPLE_EVENT)
    totals = [r for r in rows if r["market_type"] == "totals"]
    assert len(totals) == 2
    outcomes = {r["outcome"]: r["odds"] for r in totals}
    assert outcomes["over_2.5"] == pytest.approx(1.85)
    assert outcomes["under_2.5"] == pytest.approx(2.00)


def test_parse_match_odds_btts():
    rows = parse_match_odds_event(_SAMPLE_EVENT)
    btts = [r for r in rows if r["market_type"] == "btts"]
    assert len(btts) == 2
    outcomes = {r["outcome"]: r["odds"] for r in btts}
    assert outcomes["yes"] == pytest.approx(1.92)
    assert outcomes["no"] == pytest.approx(1.90)


def test_parse_match_odds_bookmaker():
    rows = parse_match_odds_event(_SAMPLE_EVENT)
    assert all(r["bookmaker"] == "betfair" for r in rows)


def test_parse_match_odds_skips_unknown_bookmaker():
    event = {
        **_SAMPLE_EVENT,
        "bookmakers": [{"key": "winamax", "markets": []}]
    }
    rows = parse_match_odds_event(event)
    assert rows == []


from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_ingest_match_odds_returns_rows_with_event_id():
    """ingest_match_odds_for_league must return MatchOddsRow with event_id."""
    from app.ingestion.match_odds import ingest_match_odds_for_league

    fake_events = [{"id": "evt_42", "home_team": "PSG", "away_team": "Lyon"}]
    fake_response_data = {
        "bookmakers": [{
            "key": "betfair",
            "markets": [{
                "key": "totals",
                "outcomes": [
                    {"name": "Over", "point": 2.5, "price": 1.85},
                    {"name": "Under", "point": 2.5, "price": 2.00},
                ],
            }]
        }]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_response_data
    mock_resp.headers = {}

    with patch("app.ingestion.match_odds.OddsAPIClient") as MockClient:
        instance = MockClient.return_value
        instance.get_events = AsyncMock(return_value=fake_events)
        instance.api_key = "test_key"
        instance._check_quota = MagicMock()
        instance._update_quota = MagicMock()

        with patch("httpx.AsyncClient") as MockHttp:
            mock_http_instance = AsyncMock()
            mock_http_instance.get = AsyncMock(return_value=mock_resp)
            MockHttp.return_value.__aenter__ = AsyncMock(return_value=mock_http_instance)
            MockHttp.return_value.__aexit__ = AsyncMock(return_value=False)

            rows, events = await ingest_match_odds_for_league("ligue_1")

    assert len(rows) == 2  # over_2.5 + under_2.5
    assert all(r.event_id == "evt_42" for r in rows)
    assert {r.outcome for r in rows} == {"over_2.5", "under_2.5"}
    assert len(events) == 1
