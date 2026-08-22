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


# ---------------------------------------------------------------------------
# Bornes de saison — season_start / season_end
# ---------------------------------------------------------------------------


def test_season_end_est_le_1er_aout_de_la_seconde_annee():
    from datetime import date

    from app.services.season_service import season_end, season_start

    assert season_start("2021-2022") == date(2021, 8, 1)
    assert season_end("2021-2022") == date(2022, 8, 1)
    assert season_end("2025-2026") == date(2026, 8, 1)


def test_season_end_refuse_une_saison_discontinue():
    import pytest as _pytest

    from app.services.season_service import season_end

    with _pytest.raises(ValueError):
        season_end("2021-2023")


def test_les_bornes_de_saison_ne_se_chevauchent_pas():
    """La fin d'une saison est exactement le debut de la suivante (borne exclusive)."""
    from app.services.season_service import season_end, season_start

    assert season_end("2021-2022") == season_start("2022-2023")
    assert season_end("2024-2025") == season_start("2025-2026")


def test_canonical_team_porte_son_championnat():
    """Le championnat est une donnee, plus une deduction depuis les noms.

    Il etait auparavant devine en regroupant les joueurs par
    current_team_name, colonne fausse pour 886 joueurs sur 2 401.
    """
    from app.models.canonical_teams import CanonicalTeam

    cols = CanonicalTeam.__table__.columns
    assert "league_api_id" in cols
    assert "season" in cols
    # Nullables : un relegue garde sa ligne et son historique sans engagement.
    assert cols["league_api_id"].nullable is True
    assert cols["season"].nullable is True
