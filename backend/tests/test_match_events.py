"""Tests for match event ingestion from FotMob."""

from unittest.mock import AsyncMock, patch

from app.ingestion.fotmob_scraper import _parse_match_events, _parse_minute


class TestParseMinute:
    """Tests for minute parsing from FotMob events."""

    def test_integer_input(self):
        assert _parse_minute(45) == 45

    def test_string_input(self):
        assert _parse_minute("67") == 67

    def test_stoppage_time(self):
        assert _parse_minute("45+2") == 45

    def test_none_input(self):
        assert _parse_minute(None) is None

    def test_empty_string(self):
        assert _parse_minute("") is None


class TestParseMatchEvents:
    """Tests for FotMob matchDetails event parsing."""

    def test_parses_goals_from_events(self):
        data = {
            "content": {
                "matchFacts": {
                    "events": {
                        "events": [
                            {
                                "type": "Goal",
                                "nameStr": "Kylian Mbappe",
                                "time": 23,
                                "isOwnGoal": False,
                                "isPenalty": False,
                                "assistStr": "Ousmane Dembele",
                            },
                            {
                                "type": "Goal",
                                "nameStr": "Alexandre Lacazette",
                                "time": 56,
                                "isOwnGoal": False,
                                "isPenalty": False,
                                "assistStr": "",
                            },
                        ]
                    }
                }
            }
        }

        events = _parse_match_events(data)

        # 2 goals + 1 assist (only Mbappe's goal had an assist)
        assert len(events) == 3

        goals = [e for e in events if e["event_type"] == "goal"]
        assists = [e for e in events if e["event_type"] == "assist"]

        assert len(goals) == 2
        assert len(assists) == 1
        assert goals[0]["player_name"] == "Kylian Mbappe"
        assert goals[0]["minute"] == 23
        assert assists[0]["player_name"] == "Ousmane Dembele"

    def test_parses_own_goal(self):
        data = {
            "content": {
                "matchFacts": {
                    "events": {
                        "events": [
                            {
                                "type": "Goal",
                                "nameStr": "Defender Name",
                                "time": 72,
                                "isOwnGoal": True,
                            },
                        ]
                    }
                }
            }
        }

        events = _parse_match_events(data)
        assert len(events) == 1
        assert events[0]["event_type"] == "own_goal"

    def test_parses_penalty_goal(self):
        data = {
            "content": {
                "matchFacts": {
                    "events": {
                        "events": [
                            {
                                "type": "Goal",
                                "nameStr": "Striker",
                                "time": 90,
                                "isPenalty": True,
                            },
                        ]
                    }
                }
            }
        }

        events = _parse_match_events(data)
        assert len(events) == 1
        assert events[0]["event_type"] == "penalty_goal"

    def test_empty_data_returns_empty(self):
        events = _parse_match_events({})
        assert events == []

    def test_skips_events_without_name(self):
        data = {
            "content": {
                "matchFacts": {
                    "events": {
                        "events": [
                            {"type": "Goal", "nameStr": "", "time": 10},
                        ]
                    }
                }
            }
        }

        events = _parse_match_events(data)
        assert len(events) == 0


class TestFetchMatchEvents:
    """Tests for the FotMob match events fetch function."""

    @patch("app.ingestion.fotmob_scraper.httpx.AsyncClient")
    async def test_fetch_match_events_success(self, mock_client_class):
        """Test successful fetch returns parsed events."""
        import json

        from app.ingestion.fotmob_scraper import fetch_match_events

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = AsyncMock()
        mock_response.content = json.dumps({
            "content": {
                "matchFacts": {
                    "events": {
                        "events": [
                            {
                                "type": "Goal",
                                "nameStr": "Test Player",
                                "time": 30,
                            },
                        ]
                    }
                }
            }
        }).encode("utf-8")

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_class.return_value = mock_client

        events = await fetch_match_events(12345)

        assert len(events) == 1
        assert events[0]["player_name"] == "Test Player"
        assert events[0]["event_type"] == "goal"
