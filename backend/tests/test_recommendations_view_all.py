"""Tests for view-all pagination in recommendations API."""
from app.api.recommendations import RecommendationsResponse, Recommendation


def _make_rec(**kwargs) -> Recommendation:
    defaults = dict(
        id=1, fixture_id="ext-1", fixture_name="PSG vs Lyon",
        kickoff_utc="2026-04-10T18:45:00+00:00",
        player_name="Mbappe", team="PSG", market_type="goalscorer",
        fair_odds=3.5, best_bookmaker="Betclic", best_odds=4.0,
        edge=0.14, classification="VALUE", confidence=0.72, explanation={},
    )
    defaults.update(kwargs)
    return Recommendation(**defaults)


class TestRecommendationsResponsePagination:
    def test_pagination_fields_present(self):
        resp = RecommendationsResponse(
            recommendations=[_make_rec()],
            total=100,
            page=2,
            page_size=50,
            pages=2,
        )
        assert resp.total == 100
        assert resp.page == 2
        assert resp.page_size == 50
        assert resp.pages == 2

    def test_pagination_defaults(self):
        """Default pagination = page 1, page_size 50, pages 1."""
        resp = RecommendationsResponse(recommendations=[])
        assert resp.page == 1
        assert resp.page_size == 50
        assert resp.pages == 1
        assert resp.total == 0

    def test_date_optional(self):
        """date field is now optional (None in view-all mode)."""
        resp = RecommendationsResponse(recommendations=[])
        assert resp.date is None

    def test_recs_returned_without_date(self):
        """RecommendationsResponse builds fine without date (view-all)."""
        recs = [_make_rec(id=i) for i in range(1, 4)]
        resp = RecommendationsResponse(recommendations=recs, total=3, pages=1)
        assert len(resp.recommendations) == 3


import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


class TestGetRecommendationsViewAll:
    """Tests for the view-all code path (no target_date)."""

    def _make_db_rec(self, id_=1, market_type="goalscorer", edge=0.14, status="pending"):
        rec = MagicMock()
        rec.id = id_
        rec.player_name = "Mbappe"
        rec.market_type = market_type
        rec.fair_odds = 3.5
        rec.best_bookmaker = "Betclic"
        rec.best_odds = 4.0
        rec.edge = edge
        rec.classification = "VALUE"
        rec.confidence = 0.72
        rec.explanation = {}
        rec.status = status
        return rec

    def _make_db_fix(self, external_id="ext-1"):
        fix = MagicMock()
        fix.external_id = external_id
        fix.home_team = "PSG"
        fix.away_team = "Lyon"
        fix.kickoff_utc = datetime(2026, 4, 10, 18, 45, tzinfo=timezone.utc)
        return fix

    @pytest.mark.asyncio
    async def test_view_all_reads_from_db_not_generator(self):
        """When target_date is None, must NOT call get_recommendations_for_date."""
        from app.api.recommendations import get_recommendations

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        items_result = MagicMock()
        items_result.all.return_value = [(self._make_db_rec(), self._make_db_fix())]
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        with patch("app.api.recommendations.get_recommendations_for_date") as mock_gen:
            response = await get_recommendations(db=mock_db, target_date=None)
            mock_gen.assert_not_called()

        assert len(response.recommendations) == 1

    @pytest.mark.asyncio
    async def test_recommendations_pagination(self):
        """Returns correct total/page/pages metadata."""
        from app.api.recommendations import get_recommendations

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 120
        items_result = MagicMock()
        items_result.all.return_value = [
            (self._make_db_rec(id_=i), self._make_db_fix(f"ext-{i}"))
            for i in range(1, 51)
        ]
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        response = await get_recommendations(db=mock_db, target_date=None)

        assert response.total == 120
        assert response.page == 1
        assert response.page_size == 50
        assert response.pages == 3

    @pytest.mark.asyncio
    async def test_market_edge_filter_in_view_all(self):
        """market_type filter — db.execute is called twice (count + items) in view-all mode."""
        from app.api.recommendations import get_recommendations
        from app.api.recommendations import MarketType

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        items_result = MagicMock()
        items_result.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        response = await get_recommendations(
            db=mock_db, target_date=None, market_type=MarketType.GOALSCORER
        )
        assert mock_db.execute.call_count == 2
        assert response.total == 0

    @pytest.mark.asyncio
    async def test_recommendations_with_date_no_pagination(self):
        """With target_date, endpoint returns pages=1 and page=1 (no pagination)."""
        from app.api.recommendations import get_recommendations
        from datetime import date

        mock_db = AsyncMock()
        with patch("app.api.recommendations.get_recommendations_for_date", new=AsyncMock(return_value=([], None))):
            response = await get_recommendations(db=mock_db, target_date=date(2026, 4, 10))

        assert response.pages == 1
        assert response.page == 1
        assert response.date == "2026-04-10"

    @pytest.mark.asyncio
    async def test_recommendations_no_date_returns_all(self):
        """Without target_date, returns active (pending/approved) recs ordered ASC by kickoff."""
        from app.api.recommendations import get_recommendations

        mock_db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 2
        items_result = MagicMock()
        fix1 = MagicMock()
        fix1.external_id = "ext-1"
        fix1.home_team = "PSG"
        fix1.away_team = "Lyon"
        fix1.kickoff_utc = datetime(2026, 4, 10, 18, 45, tzinfo=timezone.utc)
        fix2 = MagicMock()
        fix2.external_id = "ext-2"
        fix2.home_team = "OM"
        fix2.away_team = "Nice"
        fix2.kickoff_utc = datetime(2026, 4, 12, 20, 0, tzinfo=timezone.utc)
        items_result.all.return_value = [
            (self._make_db_rec(id_=1), fix1),
            (self._make_db_rec(id_=2), fix2),
        ]
        mock_db.execute = AsyncMock(side_effect=[count_result, items_result])

        with patch("app.api.recommendations.get_recommendations_for_date") as mock_gen:
            response = await get_recommendations(db=mock_db, target_date=None)
            mock_gen.assert_not_called()

        assert response.total == 2
        assert len(response.recommendations) == 2
        assert response.recommendations[0].fixture_id == "ext-1"
        assert response.recommendations[1].fixture_id == "ext-2"
