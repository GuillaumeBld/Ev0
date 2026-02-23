"""Tests for outcome resolver."""

import pytest

from app.backtest.outcome_resolver import resolve_outcome


class TestResolveOutcome:
    """Tests for resolving bet outcomes from match events."""

    @pytest.fixture
    def match_events(self):
        return [
            {"player_name": "Kylian Mbappe", "event_type": "goal", "minute": 23},
            {"player_name": "Ousmane Dembele", "event_type": "assist", "minute": 23},
            {"player_name": "Bradley Barcola", "event_type": "goal", "minute": 67},
            {"player_name": "Defender Name", "event_type": "own_goal", "minute": 45},
            {"player_name": "Penalty Taker", "event_type": "penalty_goal", "minute": 90},
        ]

    def test_exact_name_match_goal(self, match_events):
        """Player who scored should return 1."""
        result = resolve_outcome("Kylian Mbappe", "goalscorer", match_events)
        assert result == 1

    def test_exact_name_match_assist(self, match_events):
        """Player who assisted should return 1 for assist market."""
        result = resolve_outcome("Ousmane Dembele", "assist", match_events)
        assert result == 1

    def test_player_not_in_events(self, match_events):
        """Player who didn't score/assist should return 0."""
        result = resolve_outcome("Random Player", "goalscorer", match_events)
        assert result == 0

    def test_normalized_fuzzy_match(self, match_events):
        """Names with accents should still match after normalization."""
        # "Kylian Mbappé" (accented) should match "Kylian Mbappe"
        result = resolve_outcome("Kylian Mbappé", "goalscorer", match_events)
        assert result == 1

    def test_goalscorer_does_not_match_assist_market(self, match_events):
        """Mbappe scored but didn't assist — assist market should return 0."""
        result = resolve_outcome("Kylian Mbappe", "assist", match_events)
        assert result == 0

    def test_assister_does_not_match_goalscorer_market(self, match_events):
        """Dembele assisted but didn't score — goalscorer market should return 0."""
        result = resolve_outcome("Ousmane Dembele", "goalscorer", match_events)
        assert result == 0

    def test_own_goal_excluded_from_goalscorer(self, match_events):
        """Own goals should NOT count as goalscorer wins."""
        result = resolve_outcome("Defender Name", "goalscorer", match_events)
        assert result == 0

    def test_penalty_goal_counts_for_goalscorer(self, match_events):
        """Penalty goals should count as goalscorer wins."""
        result = resolve_outcome("Penalty Taker", "goalscorer", match_events)
        assert result == 1

    def test_empty_events(self):
        """Empty event list should return 0."""
        result = resolve_outcome("Anyone", "goalscorer", [])
        assert result == 0

    def test_unknown_market_type(self, match_events):
        """Unknown market type should return 0."""
        result = resolve_outcome("Kylian Mbappe", "unknown_market", match_events)
        assert result == 0
