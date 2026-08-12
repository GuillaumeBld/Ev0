"""Tests des helpers purs du module d'audit Autopilot (read-only)."""
from datetime import UTC, datetime

from app.autopilot.audit import (
    EXPECTED_FEATURE_DIM,
    SETTLEMENT_FIX_CUTOFF,
    edge_bucket,
    feature_dim,
    is_feature_dim_valid,
    is_reward_verifiable,
)


def _vec(n: int) -> str:
    return "[" + ", ".join("0.0" for _ in range(n)) + "]"


def test_feature_dim_counts():
    assert feature_dim(_vec(13)) == 13
    assert feature_dim(_vec(10)) == 10
    assert feature_dim(None) == 0
    assert feature_dim("") == 0
    assert feature_dim("pas du json") == 0


def test_is_feature_dim_valid():
    assert is_feature_dim_valid(_vec(EXPECTED_FEATURE_DIM))
    assert not is_feature_dim_valid(_vec(10))
    assert not is_feature_dim_valid(None)


def test_edge_bucket_boundaries():
    assert edge_bucket(-0.01) == "negatif"
    assert edge_bucket(0.0) == "0-5%"
    assert edge_bucket(0.049) == "0-5%"
    assert edge_bucket(0.05) == "5-10%"
    assert edge_bucket(0.10) == "10-15%"
    assert edge_bucket(0.15) == "15%+"
    assert edge_bucket(None) == "unknown"


def test_reward_verifiable_happy_path():
    after = datetime(2026, 7, 12, tzinfo=UTC)
    assert is_reward_verifiable("won", after, _vec(13))
    assert is_reward_verifiable("lost", after, _vec(13))


def test_reward_not_verifiable_before_cutoff():
    before = datetime(2026, 6, 1, tzinfo=UTC)
    assert not is_reward_verifiable("won", before, _vec(13))


def test_reward_not_verifiable_void_or_skip():
    after = datetime(2026, 7, 12, tzinfo=UTC)
    assert not is_reward_verifiable("void", after, _vec(13))
    assert not is_reward_verifiable("skip_correct", after, _vec(13))
    assert not is_reward_verifiable(None, after, _vec(13))


def test_reward_not_verifiable_wrong_dim():
    after = datetime(2026, 7, 12, tzinfo=UTC)
    assert not is_reward_verifiable("won", after, _vec(10))


def test_reward_not_verifiable_missing_settled_at():
    assert not is_reward_verifiable("won", None, _vec(13))


def test_naive_datetime_treated_as_utc():
    # settled_at sans tzinfo (naïf) après le cutoff → traité comme UTC, valide
    naive_after = datetime(2026, 7, 12, 0, 0, 0)
    assert naive_after.tzinfo is None
    assert is_reward_verifiable("won", naive_after, _vec(13))
    # naïf avant le cutoff → invalide
    naive_before = datetime(2026, 5, 1, 0, 0, 0)
    assert not is_reward_verifiable("won", naive_before, _vec(13))


def test_cutoff_is_the_july_fix_date():
    assert SETTLEMENT_FIX_CUTOFF == datetime(2026, 7, 10, tzinfo=UTC)
