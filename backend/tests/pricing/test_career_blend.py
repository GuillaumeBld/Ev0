"""Tests for the Beta blended rhythm module (app.pricing.career_blend).

Covers:
  1. blend_rate — pure weighted-average function (hand-computed values)
  2. blended_rhythm — DB-backed orchestration (session mocked)
     - bzz-only (no career data)
     - anti-double-counting: bzz-covered seasons never re-pulled from career
     - 90-minute threshold applied on both sources
     - None when no usable data at all
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pricing.career_blend import (
    BLEND_DECAY,
    BlendedRhythm,
    blend_rate,
    blended_rhythm,
)

MODULE = "app.pricing.career_blend"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _exec_result(rows: list[tuple]) -> MagicMock:
    """Mock of the object returned by `await session.execute(...)`."""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _make_session(bzz_rows: list[tuple], career_rows: list[tuple]) -> MagicMock:
    """AsyncSession mock: first execute() call -> bzz rows, second -> career rows."""
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[_exec_result(bzz_rows), _exec_result(career_rows)]
    )
    return session


# ---------------------------------------------------------------------------
# blend_rate — pure function
# ---------------------------------------------------------------------------


class TestBlendRate:
    def test_constant_decay_is_zero_point_five(self):
        assert BLEND_DECAY == 0.50

    def test_manual_two_seasons(self):
        # age=0, minutes=100, rate=0.5  -> weight = 100 * 0.5^0 = 100
        # age=1, minutes=100, rate=0.3  -> weight = 100 * 0.5^1 = 50
        # num = 100*0.5 + 50*0.3 = 65 ; den = 150 ; result = 65/150
        result = blend_rate([(0, 100.0, 0.5), (1, 100.0, 0.3)])
        assert result == pytest.approx(65.0 / 150.0)

    def test_single_season_returns_its_own_rate(self):
        result = blend_rate([(3, 500.0, 0.42)])
        assert result == pytest.approx(0.42)

    def test_decay_pulls_result_toward_recent_season(self):
        # age=0 rate=1.0, age=2 rate=0.0, equal minutes -> plain average would
        # be 0.5, but decay=0.5 must pull the blended value above 0.5 since
        # the older (age=2) season is down-weighted.
        result = blend_rate([(0, 100.0, 1.0), (2, 100.0, 0.0)])
        assert result == pytest.approx(0.8)  # 100*1 + 25*0 over 125
        assert result > 0.5

    def test_ignores_seasons_under_90_minutes(self):
        # The 50-minute season has an outlier rate (10.0) that must NOT
        # affect the result — only the 100-minute season counts.
        result = blend_rate([(0, 50.0, 10.0), (0, 100.0, 0.3)])
        assert result == pytest.approx(0.3)

    def test_empty_entries_returns_none(self):
        assert blend_rate([]) is None

    def test_all_entries_under_threshold_returns_none(self):
        assert blend_rate([(0, 10.0, 0.5), (1, 89.0, 0.9)]) is None


# ---------------------------------------------------------------------------
# blended_rhythm — DB-backed orchestration
# ---------------------------------------------------------------------------


class TestBlendedRhythm:
    @pytest.mark.asyncio
    async def test_bzz_only_no_career_data(self):
        # Two competitions in bzz for the same season -> aggregated toutes comp.
        bzz_rows = [
            ("2025-2026", 1000, 5.0, 2.0),
            ("2025-2026", 200, 1.0, 0.4),
        ]
        session = _make_session(bzz_rows, career_rows=[])

        with patch(f"{MODULE}.current_season", new=AsyncMock(return_value="2026-2027")):
            result = await blended_rhythm(session, player_api_id=1)

        assert result is not None
        # minutes total = 1200, xg total = 6.0 -> 6.0 / (1200/90) = 0.45
        assert result.goal_rate_per_90 == pytest.approx(0.45)
        # xa total = 2.4 -> 2.4 / (1200/90) = 0.18
        assert result.assist_rate_per_90 == pytest.approx(0.18)
        assert result.seasons_used == 1
        assert result.has_career is False

    @pytest.mark.asyncio
    async def test_anti_double_counting_bzz_covered_season_excluded_from_career(self):
        # bzz covers 2025-2026 (age 1 relative to current season 2026-2027).
        bzz_rows = [("2025-2026", 1000, 5.0, 2.0)]  # xg_per_90=0.45, xa_per_90=0.18
        career_rows = [
            # Same season as bzz — MUST be ignored even though present in
            # player_career_seasons (outlier values would blow up the blend
            # if wrongly included).
            (2025, 100, 50, 1000),
            # Older season, not covered by bzz — MUST be used.
            # goals=9, assists=4.5(-> use 4 int in reality, keep float ok), minutes=1000
            (2024, 9, 4, 1000),
        ]
        session = _make_session(bzz_rows, career_rows)

        with patch(f"{MODULE}.current_season", new=AsyncMock(return_value="2026-2027")):
            result = await blended_rhythm(session, player_api_id=1)

        assert result is not None
        # bzz: age=1, minutes=1000, rate=0.45 -> weight = 1000*0.5 = 500
        # career 2024: age=2, minutes=1000, rate=9*90/1000=0.81 -> weight = 1000*0.25=250
        expected_goal = (500 * 0.45 + 250 * 0.81) / 750
        assert result.goal_rate_per_90 == pytest.approx(expected_goal)
        # If the 2025 career row (goals=100) had leaked in, the result would
        # be far higher than expected_goal — guard against that explicitly.
        assert result.goal_rate_per_90 < 1.0

        expected_assist = (500 * 0.18 + 250 * (4 * 90 / 1000)) / 750
        assert result.assist_rate_per_90 == pytest.approx(expected_assist)

        assert result.seasons_used == 2
        assert result.has_career is True

    @pytest.mark.asyncio
    async def test_minutes_threshold_applied_to_both_sources(self):
        # bzz season under 90 minutes -> excluded (and, since it's excluded
        # from the blend, the season is still "seen" by bzz so it must not
        # leak into career even though it contributes nothing itself).
        bzz_rows = [("2025-2026", 50, 3.0, 1.0)]
        # Career season under 90 minutes -> excluded too.
        career_rows = [(2024, 1, 1, 80)]
        session = _make_session(bzz_rows, career_rows)

        with patch(f"{MODULE}.current_season", new=AsyncMock(return_value="2026-2027")):
            result = await blended_rhythm(session, player_api_id=1)

        assert result is None

    @pytest.mark.asyncio
    async def test_career_only_used_when_bzz_season_under_threshold(self):
        # bzz season excluded (< 90 min) but a DIFFERENT, older career season
        # (not bzz-covered) has enough minutes -> must still be used.
        bzz_rows = [("2025-2026", 50, 3.0, 1.0)]
        career_rows = [(2023, 9, 3, 1000)]  # goal_rate = 0.81, assist_rate=0.27
        session = _make_session(bzz_rows, career_rows)

        with patch(f"{MODULE}.current_season", new=AsyncMock(return_value="2026-2027")):
            result = await blended_rhythm(session, player_api_id=1)

        assert result is not None
        assert result.goal_rate_per_90 == pytest.approx(0.81)
        assert result.assist_rate_per_90 == pytest.approx(0.27)
        assert result.seasons_used == 1
        assert result.has_career is True

    @pytest.mark.asyncio
    async def test_returns_none_when_no_data_at_all(self):
        session = _make_session(bzz_rows=[], career_rows=[])

        with patch(f"{MODULE}.current_season", new=AsyncMock(return_value="2026-2027")):
            result = await blended_rhythm(session, player_api_id=999)

        assert result is None

    @pytest.mark.asyncio
    async def test_blended_rhythm_is_a_blended_rhythm_instance(self):
        bzz_rows = [("2025-2026", 1000, 5.0, 2.0)]
        session = _make_session(bzz_rows, career_rows=[])

        with patch(f"{MODULE}.current_season", new=AsyncMock(return_value="2026-2027")):
            result = await blended_rhythm(session, player_api_id=1)

        assert isinstance(result, BlendedRhythm)
