"""Tests pour la table isolee player_selection_season_stats (selections nationales).

Debut Task 1 (smoke test d'import du modele) ; complete en Task 2.
"""


def test_selection_model_imports():
    from app.models.selection_stats import PlayerSelectionSeasonStat

    assert PlayerSelectionSeasonStat.__tablename__ == "player_selection_season_stats"
