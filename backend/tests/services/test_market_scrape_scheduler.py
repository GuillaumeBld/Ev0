"""Tests for the adaptive scrape scheduler."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.market_scrape_scheduler import (
    _compute_interval_minutes,
    _compute_score,
    _compute_target_rpm,
)


class TestComputeIntervalMinutes:
    def test_beyond_24h(self):
        assert _compute_interval_minutes(t_minutes=1500) == 120

    def test_between_6h_and_24h(self):
        assert _compute_interval_minutes(t_minutes=500) == 60

    def test_between_2h_and_6h(self):
        assert _compute_interval_minutes(t_minutes=200) == 20

    def test_between_30m_and_2h(self):
        assert _compute_interval_minutes(t_minutes=60) == 7

    def test_between_5m_and_30m(self):
        assert _compute_interval_minutes(t_minutes=15) == 3

    def test_at_or_below_5m_returns_none(self):
        assert _compute_interval_minutes(t_minutes=5) is None
        assert _compute_interval_minutes(t_minutes=0) is None
        assert _compute_interval_minutes(t_minutes=-1) is None


class TestComputeScore:
    def test_urgent_match_scores_higher(self):
        score_near = _compute_score(t_minutes=10, error_streak=0)
        score_far = _compute_score(t_minutes=1000, error_streak=0)
        assert score_near > score_far

    def test_error_streak_reduces_score(self):
        score_clean = _compute_score(t_minutes=30, error_streak=0)
        score_errors = _compute_score(t_minutes=30, error_streak=5)
        assert score_clean > score_errors

    def test_penalty_capped_at_0_5(self):
        score_5 = _compute_score(t_minutes=30, error_streak=5)
        score_100 = _compute_score(t_minutes=30, error_streak=100)
        assert score_5 == score_100  # penalty capped


class TestComputeTargetRpm:
    def test_eco_mode_when_no_due(self):
        assert _compute_target_rpm(due_count=0, pressure_count=0) == 1.0

    def test_medium_when_few_due(self):
        assert _compute_target_rpm(due_count=2, pressure_count=0) == 2.0

    def test_high_when_many_due(self):
        assert _compute_target_rpm(due_count=10, pressure_count=0) == 3.0

    def test_boost_when_pressure(self):
        rpm = _compute_target_rpm(due_count=5, pressure_count=15, max_rpm_hard=5.0)
        assert rpm == 5.0

    def test_capped_by_max_rpm_hard(self):
        rpm = _compute_target_rpm(due_count=100, pressure_count=100, max_rpm_hard=5.0)
        assert rpm <= 5.0
