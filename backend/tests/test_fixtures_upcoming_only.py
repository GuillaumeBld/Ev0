"""Tests for upcoming_only filter logic in fixtures endpoint."""

from unittest.mock import MagicMock

from app.api.fixtures import _apply_upcoming_only_filter


class TestUpcomingOnlyFilter:
    def test_function_exists(self):
        """_apply_upcoming_only_filter must exist."""
        assert callable(_apply_upcoming_only_filter)

    def test_returns_stmt_unchanged_when_false(self):
        """When upcoming_only=False, statement is returned as-is."""
        stmt = MagicMock()
        result = _apply_upcoming_only_filter(stmt, upcoming_only=False)
        assert result is stmt

    def test_calls_where_when_true(self):
        """When upcoming_only=True, .where() is called on the statement."""
        stmt = MagicMock()
        stmt.where.return_value = stmt
        result = _apply_upcoming_only_filter(stmt, upcoming_only=True)
        stmt.where.assert_called_once()
        assert result is stmt
