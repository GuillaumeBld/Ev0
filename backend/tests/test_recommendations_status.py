"""Tests for status field in recommendations API response."""

from app.api.recommendations import Recommendation


class TestRecommendationStatusField:
    def test_recommendation_has_status_field(self):
        """Recommendation model must expose a status field."""
        rec = Recommendation(
            id=1,
            fixture_id="ext-123",
            fixture_name="PSG vs Lyon",
            kickoff_utc="2026-03-15T20:00:00+00:00",
            player_name="Mbappe",
            team="PSG",
            market_type="goalscorer",
            fair_odds=3.50,
            best_bookmaker="Betclic",
            best_odds=4.00,
            edge=0.14,
            classification="VALUE",
            confidence=0.72,
            explanation={},
            status="pending",
        )
        assert rec.status == "pending"

    def test_recommendation_status_defaults_to_pending(self):
        """If no status provided, default is 'pending'."""
        rec = Recommendation(
            id=1,
            fixture_id="ext-123",
            fixture_name="PSG vs Lyon",
            kickoff_utc="2026-03-15T20:00:00+00:00",
            player_name="Mbappe",
            team="PSG",
            market_type="goalscorer",
            fair_odds=3.50,
            best_bookmaker="Betclic",
            best_odds=4.00,
            edge=0.14,
            classification="VALUE",
            confidence=0.72,
            explanation={},
        )
        assert rec.status == "pending"
