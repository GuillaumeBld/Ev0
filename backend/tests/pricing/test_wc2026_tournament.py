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
