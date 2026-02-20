"""Data integrity and quality benchmarks for scraped player data.

Tests that scraped data:
  1. Has the required structure and fields
  2. Contains statistically plausible values
  3. Has no anomalies (duplicates, impossible values, per-90 inconsistencies)
  4. Meets minimum coverage thresholds per league

Usage:
  # Unit tests only (no network):
  pytest tests/test_data_quality.py -v

  # Full integration tests (requires internet):
  pytest tests/test_data_quality.py -v -m integration
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.ingestion.understat_scraper import calculate_per_90


# ============================================================
# Data Quality Validators
# ============================================================

REQUIRED_FIELDS = ("name", "team", "position", "minutes", "xg", "xa", "npxg")

# Statistical plausibility bounds (per 90 min)
MAX_XG_PER_90 = 2.0    # No player sustains > 2.0 xG/90 over a season
MAX_XA_PER_90 = 2.0
PER_90_TOLERANCE = 0.01  # Float rounding tolerance


@dataclass
class QualityReport:
    total: int
    errors: list[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.errors)

    @property
    def passed(self) -> int:
        return self.total - self.failed

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total > 0 else 0.0

    @property
    def is_healthy(self) -> bool:
        return self.pass_rate >= 0.95

    def __str__(self) -> str:
        return (
            f"QualityReport(total={self.total}, passed={self.passed}, "
            f"failed={self.failed}, pass_rate={self.pass_rate:.1%})"
        )


def validate_player(player: dict[str, Any]) -> list[str]:
    """Return a list of data quality errors for a single player record."""
    errors: list[str] = []
    name = player.get("name", "")

    def err(msg: str) -> None:
        errors.append(f"[{name or '?'}] {msg}")

    # --- Required fields ---
    missing = [f for f in REQUIRED_FIELDS if f not in player]
    if missing:
        err(f"Missing fields: {missing}")
        return errors  # Cannot validate further

    # --- Name ---
    if not isinstance(name, str) or len(name.strip()) < 2:
        err(f"Invalid name: {name!r}")

    # --- Non-negative integers ---
    for field_name in ("goals", "assists", "shots", "key_passes", "games", "minutes"):
        val = player.get(field_name, 0)
        if not isinstance(val, (int, float)) or val < 0:
            err(f"Negative or invalid {field_name}: {val}")

    # --- Non-negative floats ---
    for field_name in ("xg", "npxg", "xa"):
        val = player.get(field_name, 0.0)
        if not isinstance(val, (int, float)) or float(val) < 0:
            err(f"Negative {field_name}: {val}")

    # --- npxG ≤ xG (non-penalty can't exceed total) ---
    xg = float(player.get("xg", 0))
    npxg = float(player.get("npxg", 0))
    if npxg > xg + PER_90_TOLERANCE:
        err(f"npxG ({npxg:.3f}) > xG ({xg:.3f}) — impossible value")

    # --- Per-90 plausibility (only if meaningful minutes) ---
    minutes = int(player.get("minutes", 0))
    if minutes >= 90:
        xg_per_90 = float(player.get("xg_per_90", 0.0))
        xa_per_90 = float(player.get("xa_per_90", 0.0))

        if xg_per_90 > MAX_XG_PER_90:
            err(f"Suspicious xG/90: {xg_per_90:.3f} (> {MAX_XG_PER_90})")
        if xa_per_90 > MAX_XA_PER_90:
            err(f"Suspicious xA/90: {xa_per_90:.3f} (> {MAX_XA_PER_90})")

    # --- Per-90 consistency (stored value must match computed value) ---
    if minutes > 0:
        computed_xg_90 = calculate_per_90(xg, minutes)
        stored_xg_90 = float(player.get("xg_per_90", 0.0))
        if abs(computed_xg_90 - stored_xg_90) > PER_90_TOLERANCE:
            err(
                f"xG/90 mismatch: computed {computed_xg_90:.3f} "
                f"vs stored {stored_xg_90:.3f}"
            )

        xa = float(player.get("xa", 0))
        computed_xa_90 = calculate_per_90(xa, minutes)
        stored_xa_90 = float(player.get("xa_per_90", 0.0))
        if abs(computed_xa_90 - stored_xa_90) > PER_90_TOLERANCE:
            err(
                f"xA/90 mismatch: computed {computed_xa_90:.3f} "
                f"vs stored {stored_xa_90:.3f}"
            )

    return errors


def check_quality(players: list[dict[str, Any]]) -> QualityReport:
    """Run quality checks on a full list of player records."""
    report = QualityReport(total=len(players))
    for player in players:
        report.errors.extend(validate_player(player))
    return report


def find_duplicates(players: list[dict[str, Any]]) -> list[str]:
    """Return descriptions of duplicate (name, team) pairs."""
    seen: dict[tuple[str, str], int] = {}
    dupes: list[str] = []
    for p in players:
        key = (p.get("name", ""), p.get("team", ""))
        if key in seen:
            dupes.append(f"Duplicate #{seen[key]+1}: {key[0]} @ {key[1]}")
        else:
            seen[key] = len(seen)
    return dupes


# ============================================================
# Fixtures
# ============================================================

def _make_player(**overrides: Any) -> dict[str, Any]:
    """Create a valid baseline player dict, optionally overriding fields."""
    base: dict[str, Any] = {
        "name": "Test Player",
        "team": "Test FC",
        "position": "FW",
        "games": 20,
        "minutes": 1620,
        "goals": 12,
        "assists": 5,
        "xg": 10.8,
        "npxg": 9.6,
        "xa": 4.2,
        "shots": 52,
        "key_passes": 28,
        "xg_per_90": calculate_per_90(10.8, 1620),
        "xa_per_90": calculate_per_90(4.2, 1620),
        "npxg_per_90": calculate_per_90(9.6, 1620),
    }
    base.update(overrides)
    return base


# ============================================================
# Unit Tests: validate_player
# ============================================================

class TestValidatePlayer:
    def test_valid_player_has_no_errors(self):
        assert validate_player(_make_player()) == []

    def test_missing_required_field(self):
        p = _make_player()
        del p["xg"]
        errors = validate_player(p)
        assert any("xg" in e for e in errors)

    def test_empty_name_is_invalid(self):
        errors = validate_player(_make_player(name=""))
        assert any("name" in e.lower() for e in errors)

    def test_single_char_name_is_invalid(self):
        errors = validate_player(_make_player(name="X"))
        assert any("name" in e.lower() for e in errors)

    def test_negative_minutes_is_invalid(self):
        errors = validate_player(_make_player(minutes=-10))
        assert any("minutes" in e for e in errors)

    def test_negative_goals_is_invalid(self):
        errors = validate_player(_make_player(goals=-3))
        assert any("goals" in e for e in errors)

    def test_negative_xg_is_invalid(self):
        errors = validate_player(_make_player(xg=-0.5))
        assert any("xg" in e.lower() for e in errors)

    def test_npxg_greater_than_xg_is_invalid(self):
        errors = validate_player(_make_player(xg=5.0, npxg=7.0))
        assert any("npxG" in e for e in errors)

    def test_suspicious_xg_per90_is_flagged(self):
        # Over 2.0 xG/90 is statistically implausible over a season
        errors = validate_player(_make_player(xg_per_90=2.5))
        assert any("xG/90" in e for e in errors)

    def test_xg_per90_inconsistency_is_flagged(self):
        # stored value doesn't match what calculate_per_90 computes
        errors = validate_player(_make_player(xg_per_90=9.99))
        assert any("mismatch" in e for e in errors)

    def test_zero_minutes_skips_per90_checks(self):
        p = _make_player(minutes=0, xg=0.0, npxg=0.0, xa=0.0,
                         xg_per_90=0.0, xa_per_90=0.0, npxg_per_90=0.0)
        errors = validate_player(p)
        assert not any("mismatch" in e for e in errors)

    def test_player_with_zero_stats_is_valid(self):
        p = _make_player(goals=0, assists=0, xg=0.0, npxg=0.0, xa=0.0,
                         shots=0, key_passes=0, xg_per_90=0.0,
                         xa_per_90=0.0, npxg_per_90=0.0)
        assert validate_player(p) == []


# ============================================================
# Unit Tests: find_duplicates
# ============================================================

class TestFindDuplicates:
    def test_unique_players_returns_empty(self):
        players = [_make_player(name="Mbappé", team="PSG"),
                   _make_player(name="Salah", team="Liverpool")]
        assert find_duplicates(players) == []

    def test_same_name_same_team_is_duplicate(self):
        players = [_make_player(name="David", team="PSG"),
                   _make_player(name="David", team="PSG")]
        assert len(find_duplicates(players)) == 1

    def test_same_name_different_teams_is_not_duplicate(self):
        """Transfer case: same name, different clubs."""
        players = [_make_player(name="David", team="Atletico"),
                   _make_player(name="David", team="Napoli")]
        assert find_duplicates(players) == []


# ============================================================
# Unit Tests: check_quality (full dataset)
# ============================================================

class TestCheckQuality:
    def test_clean_dataset_is_healthy(self):
        players = [_make_player(name=f"Player {i}") for i in range(50)]
        report = check_quality(players)
        assert report.is_healthy
        assert report.pass_rate == 1.0

    def test_dataset_with_one_bad_record_is_reported(self):
        players = [
            _make_player(name="Good Player"),
            _make_player(name="", goals=-1, npxg=99.0),  # Multiple errors
        ]
        report = check_quality(players)
        assert report.failed >= 1
        assert len(report.errors) > 0

    def test_pass_rate_is_calculated_correctly(self):
        # 9 good + 1 bad (1 error) = 10 total, pass_rate = 0.9
        players = [_make_player(name=f"P{i}") for i in range(9)]
        players.append(_make_player(name=""))  # 1 bad
        report = check_quality(players)
        assert abs(report.pass_rate - 0.9) < 0.01


# ============================================================
# Integration Tests: Live Understat Scraping
# ============================================================

@pytest.mark.integration
class TestUnderstatLiveScraping:
    """Integration tests that hit the real Understat website.

    These tests require internet access and are skipped by default.
    Run explicitly with: pytest tests/test_data_quality.py -m integration
    """

    @pytest.fixture(scope="class")
    async def ligue1_data(self):
        from app.ingestion.understat_scraper import fetch_understat_league
        return await fetch_understat_league("ligue_1")

    @pytest.fixture(scope="class")
    async def pl_data(self):
        from app.ingestion.understat_scraper import fetch_understat_league
        return await fetch_understat_league("premier_league")

    # --- Coverage ---

    @pytest.mark.asyncio
    async def test_ligue1_player_count(self, ligue1_data):
        players, teams = ligue1_data
        # Ligue 1: 18 teams × ~25 players = ~450
        assert len(players) >= 100, f"Only {len(players)} players scraped"
        assert len(teams) >= 10, f"Only {len(teams)} teams scraped"

    @pytest.mark.asyncio
    async def test_pl_player_count(self, pl_data):
        players, teams = pl_data
        # Premier League: 20 teams × ~25 players = ~500
        assert len(players) >= 100, f"Only {len(players)} players scraped"
        assert len(teams) >= 15, f"Only {len(teams)} teams scraped"

    # --- Data Quality ---

    @pytest.mark.asyncio
    async def test_ligue1_data_quality(self, ligue1_data):
        players, _ = ligue1_data
        assert len(players) > 0, "No players scraped"

        report = check_quality(players)
        dupes = find_duplicates(players)

        _print_quality_report("Ligue 1", report, dupes)

        assert len(dupes) == 0, f"Duplicate players: {dupes}"
        assert report.is_healthy, (
            f"Quality too low ({report.pass_rate:.1%})\n"
            f"Sample errors: {report.errors[:10]}"
        )

    @pytest.mark.asyncio
    async def test_pl_data_quality(self, pl_data):
        players, _ = pl_data
        assert len(players) > 0

        report = check_quality(players)
        dupes = find_duplicates(players)

        _print_quality_report("Premier League", report, dupes)

        assert len(dupes) == 0, f"Duplicate players: {dupes}"
        assert report.is_healthy, (
            f"Quality too low ({report.pass_rate:.1%})\n"
            f"Sample errors: {report.errors[:10]}"
        )

    # --- Benchmarks ---

    @pytest.mark.asyncio
    async def test_ligue1_scrapes_within_time_budget(self):
        from app.ingestion.understat_scraper import fetch_understat_league

        t0 = time.perf_counter()
        players, _ = await fetch_understat_league("ligue_1")
        elapsed = time.perf_counter() - t0

        print(f"\nLigue 1 scrape: {elapsed:.2f}s for {len(players)} players")
        assert elapsed < 30.0, f"Scraping too slow: {elapsed:.2f}s"
        assert len(players) > 0

    @pytest.mark.asyncio
    async def test_pl_scrapes_within_time_budget(self):
        from app.ingestion.understat_scraper import fetch_understat_league

        t0 = time.perf_counter()
        players, _ = await fetch_understat_league("premier_league")
        elapsed = time.perf_counter() - t0

        print(f"\nPremier League scrape: {elapsed:.2f}s for {len(players)} players")
        assert elapsed < 30.0, f"Scraping too slow: {elapsed:.2f}s"
        assert len(players) > 0


# ============================================================
# Helpers
# ============================================================

def _print_quality_report(
    league: str, report: QualityReport, dupes: list[str]
) -> None:
    print(f"\n{'='*50}")
    print(f"Data Quality: {league}")
    print(f"  Total players : {report.total}")
    print(f"  Passed        : {report.passed} ({report.pass_rate:.1%})")
    print(f"  Failed        : {report.failed}")
    print(f"  Duplicates    : {len(dupes)}")
    if report.errors:
        print(f"  Errors (first 5):")
        for e in report.errors[:5]:
            print(f"    - {e}")
    print(f"{'='*50}")
