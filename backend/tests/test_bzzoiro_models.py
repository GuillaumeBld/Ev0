"""Tests for Bzzoiro SQLAlchemy models."""

from app.models.bzzoiro import (
    BzzLeague,
    BzzTeam,
    BzzPlayer,
    BzzEvent,
    BzzPlayerMatchStat,
    BzzPlayerSeasonStat,
    BzzPrediction,
)


def test_models_have_expected_columns():
    assert hasattr(BzzPlayerMatchStat, "expected_goals")
    assert hasattr(BzzPlayerMatchStat, "shot_accuracy")
    assert hasattr(BzzPlayerMatchStat, "finishing_delta")
    assert hasattr(BzzPlayerSeasonStat, "xg_per_90")
    assert hasattr(BzzPlayerSeasonStat, "form_xg_5")
    assert hasattr(BzzEvent, "shotmap")
    assert hasattr(BzzPrediction, "prob_over_25")
