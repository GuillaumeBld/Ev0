import math
import pytest
from app.pricing.wc2026_tournament import poisson_ge, run_monte_carlo


def test_poisson_ge_k1_lambda1():
    # P(X >= 1) = 1 - e^(-1) for lambda=1
    assert abs(poisson_ge(1.0, 1) - (1 - math.exp(-1))) < 1e-10


def test_poisson_ge_k2_lambda1():
    # P(X >= 2) = 1 - e^(-1)(1 + 1) for lambda=1
    expected = 1 - math.exp(-1) * (1 + 1)
    assert abs(poisson_ge(1.0, 2) - expected) < 1e-10


def test_poisson_ge_k1_lambda0():
    assert poisson_ge(0.0, 1) == pytest.approx(0.0, abs=1e-10)


def test_poisson_ge_k4_lambda3():
    lam = 3.0
    cdf3 = math.exp(-lam) * (1 + lam + lam**2 / 2 + lam**3 / 6)
    assert abs(poisson_ge(lam, 4) - (1 - cdf3)) < 1e-10


def test_monte_carlo_top_scorer_sums_to_one():
    lambdas_g = [3.0, 2.0, 1.5, 1.0, 0.5, 0.2]
    lambdas_a = [1.5, 1.0, 0.8, 0.5, 0.3, 0.1]
    wc_goals   = [0] * 6
    wc_assists = [0] * 6
    results = run_monte_carlo(lambdas_g, lambdas_a, wc_goals, wc_assists, n_sim=20_000, seed=42)
    assert abs(sum(r["p_top_scorer"]   for r in results) - 1.0) < 0.02
    assert abs(sum(r["p_top_assister"] for r in results) - 1.0) < 0.02


def test_monte_carlo_highest_lambda_wins_most():
    lambdas_g = [5.0, 1.0, 0.5]
    lambdas_a = [3.0, 1.0, 0.5]
    wc_goals   = [0, 0, 0]
    wc_assists = [0, 0, 0]
    results = run_monte_carlo(lambdas_g, lambdas_a, wc_goals, wc_assists, n_sim=20_000, seed=42)
    assert results[0]["p_top_scorer"]   > results[1]["p_top_scorer"]
    assert results[0]["p_top_assister"] > results[1]["p_top_assister"]


import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_compute_expected_games_uses_advancement_table():
    """When wc2026_team_advancement has data, it takes priority over bookmaker odds."""
    from app.pricing.wc2026_tournament import compute_expected_games

    # Mock DB session that returns advancement table rows
    mock_rows = [
        {"nation": "Spain",  "e_games": 6.12},
        {"nation": "France", "e_games": 5.87},
        {"nation": "Iraq",   "e_games": 3.10},
    ]
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = mock_rows

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await compute_expected_games(mock_db)

    assert result["Spain"]  == pytest.approx(6.12)
    assert result["France"] == pytest.approx(5.87)
    assert result["Iraq"]   == pytest.approx(3.10)
    # When advancement table is populated, only one DB call should have been made
    assert mock_db.execute.call_count == 1


@pytest.mark.asyncio
async def test_compute_expected_games_fallback_when_empty():
    """When wc2026_team_advancement is empty, falls back to bookmaker odds query."""
    from app.pricing.wc2026_tournament import compute_expected_games

    # First call returns empty (no advancement data)
    # Second call returns bookmaker odds — we just verify the fallback path is entered
    mock_db = AsyncMock()
    # First execute call (advancement table) returns empty
    empty_result = MagicMock()
    empty_result.mappings.return_value.all.return_value = []
    # Second execute call (bookmaker odds) returns empty too (just tests fallback logic runs)
    bm_result = MagicMock()
    bm_result.mappings.return_value.all.return_value = []
    mock_db.execute = AsyncMock(side_effect=[empty_result, bm_result])

    result = await compute_expected_games(mock_db)

    # Fell back to bookmaker path (which returned empty)
    assert result == {}
    # Both DB calls were made
    assert mock_db.execute.call_count == 2


# ── Marchés décisif / top 3 + dead-heat ──────────────────────────────────────

def _frozen_mc(goals, assists):
    """MC avec λ restants nuls : les scores sont figés, résultat déterministe."""
    n = len(goals)
    return run_monte_carlo([0.0] * n, [0.0] * n, goals, assists, n_sim=1_000, seed=1)


def test_dead_heat_winner_split_evenly():
    # Deux joueurs figés à 7 buts → 50/50 exactement (partage dead-heat)
    results = _frozen_mc([7, 7, 3], [0, 0, 0])
    assert results[0]["p_top_scorer"] == pytest.approx(0.5)
    assert results[1]["p_top_scorer"] == pytest.approx(0.5)
    assert results[2]["p_top_scorer"] == pytest.approx(0.0)


def test_top3_distinct_scores():
    results = _frozen_mc([9, 7, 5, 3, 1], [0] * 5)
    assert [r["p_top3_scorer"] for r in results] == pytest.approx([1, 1, 1, 0, 0])


def test_top3_dead_heat_straddles_third_place():
    # 10, 8, puis trois joueurs à 5 → 1 place restante partagée en 3
    results = _frozen_mc([10, 8, 5, 5, 5], [0] * 5)
    assert results[0]["p_top3_scorer"] == pytest.approx(1.0)
    assert results[1]["p_top3_scorer"] == pytest.approx(1.0)
    for r in results[2:]:
        assert r["p_top3_scorer"] == pytest.approx(1 / 3)


def test_most_decisive_combines_goals_and_assists():
    # G+A : 5+0=5, 0+4=4, 2+2=4 → le premier gagne seul
    results = _frozen_mc([5, 0, 2], [0, 4, 2])
    assert results[0]["p_most_decisive"] == pytest.approx(1.0)
    assert results[1]["p_most_decisive"] == pytest.approx(0.0)
    assert results[2]["p_most_decisive"] == pytest.approx(0.0)
    # et les deux derniers se partagent le reste du top 3 (2 places pour 2 joueurs)
    assert results[1]["p_top3_decisive"] == pytest.approx(1.0)
    assert results[2]["p_top3_decisive"] == pytest.approx(1.0)


def test_top3_probabilities_sum_to_three():
    lambdas_g = [3.0, 2.0, 1.5, 1.0, 0.5, 0.2, 0.1, 0.05]
    lambdas_a = [1.5, 1.0, 0.8, 0.5, 0.3, 0.1, 0.05, 0.02]
    n = len(lambdas_g)
    results = run_monte_carlo(lambdas_g, lambdas_a, [0] * n, [0] * n, n_sim=20_000, seed=42)
    assert sum(r["p_top3_scorer"] for r in results) == pytest.approx(3.0, abs=1e-6)
    assert sum(r["p_top3_assister"] for r in results) == pytest.approx(3.0, abs=1e-6)
    assert sum(r["p_top3_decisive"] for r in results) == pytest.approx(3.0, abs=1e-6)
    assert sum(r["p_most_decisive"] for r in results) == pytest.approx(1.0, abs=1e-6)


def test_winner_market_sums_to_exactly_one_with_dead_heat():
    lambdas = [2.0, 2.0, 1.0, 0.5]
    results = run_monte_carlo(lambdas, lambdas, [0] * 4, [0] * 4, n_sim=10_000, seed=7)
    assert sum(r["p_top_scorer"] for r in results) == pytest.approx(1.0, abs=1e-6)


# ── Réconciliation de noms (dédup joueurs dupliqués) ─────────────────────────

def test_names_similar_variants():
    from app.pricing.wc2026_tournament import _names_similar
    assert _names_similar("Alex Baena", "Alejandro Baena")
    assert _names_similar("Kylian Mbappé", "Kylian Mbappé")
    assert _names_similar("David Møller Wolfe", "David Möller Wolfe")
    assert _names_similar("Tony Ralston", "Anthony Ralston")


def test_names_similar_rejects_different_players():
    from app.pricing.wc2026_tournament import _names_similar
    # Collision réelle en base : internal_id de Rashford pointait vers Ronaldo
    assert not _names_similar("Marcus Rashford", "Cristiano Ronaldo")
    assert not _names_similar("Harry Kane", "Erling Haaland")


# ── Fraction restante au niveau équipe ───────────────────────────────────────

def test_remaining_fraction_eliminated_team_is_frozen():
    from app.pricing.wc2026_tournament import _remaining_fraction
    # Éliminée : plus aucune projection, même si e_games est périmé (> matchs joués)
    assert _remaining_fraction(5.98, 4, alive=False) == 0.0
    assert _remaining_fraction(5.0, 5, alive=False) == 0.0


def test_remaining_fraction_alive_team():
    from app.pricing.wc2026_tournament import _remaining_fraction
    # France : e_games 6.93, 5 matchs joués → ~28% du budget restant
    assert _remaining_fraction(6.93, 5, alive=True) == pytest.approx(1.93 / 6.93, abs=1e-9)


def test_remaining_fraction_alive_team_with_stale_e_games():
    from app.pricing.wc2026_tournament import _remaining_fraction
    # En lice avec e_games périmé (≤ matchs joués) : au moins 1 match à venir
    assert _remaining_fraction(5.0, 5, alive=True) == pytest.approx(1 / 5.0)
    assert _remaining_fraction(4.5, 5, alive=True) == pytest.approx(1 / 4.5)


def test_remaining_fraction_degenerate_e_games():
    from app.pricing.wc2026_tournament import _remaining_fraction
    assert _remaining_fraction(0.0, 0, alive=True) == 0.0
